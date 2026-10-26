# WD My Cloud V4 implementation plan

## Objective and compatibility rules

Extend the existing, hardware-proven V4 support incrementally without changing V2/V5 behavior, existing entity unique IDs, or device-registry identifiers. Every new capability starts with captured, sanitized V4 response fixtures and offline tests. Optional read failures must not invalidate otherwise healthy coordinator data.

This document is a plan only. No client or integration source was changed as part of this research.

## Current implementation assessment

The local `wdnas_client` V4 implementation currently defines and uses the four proven routes `local_login`, `system_information`, `firmware_info`, and `system_state`. Its V4 adapter:

- reuses one authenticated `aiohttp` session;
- parses XML responses into Python structures;
- caches stable system/firmware data for the session;
- sanitizes authentication exceptions;
- maps V4 system information and qualitative health into the existing public models;
- deliberately rejects V4 power actions.

The local Home Assistant integration accepts a manually selected API version 4, preserves the existing domain/device identity, and currently exposes total storage plus the proven system, temperature-health, SMART-health, volume-health, and free-space-health states. Its normal automatic probe sequence still covers V5 and V2, leaving V4 as a manual fallback.

Relevant implementation evidence:

- `wdnas_client/const.py:7-12` contains only the four current V4 endpoint constants.
- `wdnas_client/v4.py:70-90` performs the query-parameter login and sanitizes `aiohttp` errors; `v4.py:92-112` implements authenticated XML GET and root validation.
- `wdnas_client/v4.py:114-149` reads/caches system information and firmware and reads qualitative system state; `v4.py:151-163` explicitly leaves used/unused capacity unset.
- `wdnas_client/__init__.py:147-209` limits server logout to V5 and performs only local V4 reset during `close()`.
- `custom_components/mycloud/config_flow.py:207-219` probes only V5 then V2; `config_flow.py:249-263` provides the explicit V4 manual fallback.
- `custom_components/mycloud/coordinator.py:156-183` isolates optional V4 firmware/state failures while retaining core system/device data.
- `custom_components/mycloud/sensor.py:138-150` creates V4 state sensors and total storage only; `binary_sensor.py:240-282` creates device-specific qualitative V4 health problem sensors.

### Confirmed gaps and cautions

1. **Server-side V4 logout is missing.** The original UI proves `GET /api/2.1/rest/logout`. Local cleanup alone may leave a server session active. Hardware validation of status/body/cookie behavior is required before enabling it.
2. **Storage detail is incomplete.** `GET storage_usage` supplies `size`, `usage`, and media categories. The current adapter consequently cannot populate numeric used/free values for V4.
3. **V4 automatic discovery is absent.** This is acceptable as a safe fallback today. Adding V4 to the bounded probe sequence is sensible only after login/logout lifecycle is validated, so probing cannot leak server sessions.
4. **A successful HTTP 200 login is not content-validated.** The original UI behaves the same way and real authentication works. Do not change this without a sanitized failed-login sample proving a machine-readable error contract.
5. **401/403 behavior differs beneficially.** The UI only special-cases 401, whereas the client treats 401/403 as authentication failures. Retain the safer client behavior.
6. **No numeric temperature, CPU, RAM, uptime, or raw SMART source exists.** Do not create numeric entities from qualitative states.
7. **Alerts are incomplete.** Response consumers exist but the captured source lacks the GET form actions. Do not guess routes.
8. **V4 power calls are source-confirmed but untested.** They require an explicitly authorized maintenance-window validation before end-user buttons are enabled.
9. **Concurrent-session behavior is unknown.** Avoid parallel logins and use one adapter/session per configured device.

## Priority 1 – high-value, read-only improvements

### P1.1 Capture and implement `storage_usage`

Evidence: `Main/aggregate.js:54`, dashboard storage handler. The UI consumes `storage_usage.size`, `usage`, `photos`, `music`, `video`, and `other`, formats the values as storage quantities, and computes free space as `size - usage`.

Implementation sequence:

1. Run the read-only hardware test in `HARDWARE_TEST_PLAN.md` once, using the already authenticated adapter.
2. Save only a sanitized fixture preserving element names, nesting, types, empty-value behavior, and representative magnitudes.
3. Add the endpoint constant and a typed V4 adapter method. Keep the raw returned `size` and `usage`; reject negative or non-numeric values and guard `size == 0`.
4. Define `free = size - usage` only when `0 <= usage <= size`. Treat inconsistent values as unavailable rather than clamping silently.
5. Determine units from comparison with the known total capacity and the UI math. Do not label bytes until validated.
6. Populate the existing common capacity model's used/free fields for V4 without changing V2/V5 parsing.
7. In Home Assistant, expose existing used/unused storage entity types if their unique-ID scheme is already device-specific. Preserve all current IDs. Add a utilization-percentage sensor only if it has a new stable, NAS-specific unique ID and adds value beyond used/free values.
8. Keep the current `system_information.capacity` result as a fallback when `storage_usage` is absent or invalid. Do not overwrite one raw source with the other in diagnostic attributes; document provenance.

Tests:

- single-element and missing-element XML;
- numeric strings, whitespace, zero capacity, usage greater than size, negative and malformed values;
- media categories absent or not summing to total usage;
- optional endpoint timeout/404 leaves the rest of coordinator data usable;
- unchanged V2/V5 fixtures and unique IDs.

### P1.2 Validate and implement V4 server logout

Evidence: `Main/aggregate.js:54`, `authentication.logout()`, default GET request to `logout`.

Implementation sequence:

1. Validate `GET logout` on hardware after normal read-only checks and record only status/shape, never cookies.
2. Add a V4 logout method that uses the same authenticated session.
3. Always clear local cookies/session resources even if server logout fails, but surface the server-side failure when no earlier exception is active.
4. During cleanup caused by an already propagating exception, do not mask the original exception; attach/log only a sanitized logout diagnostic.
5. Serialize concurrent `logout()`/`close()` calls and make cleanup idempotent, matching the established V5 safety model where practical.
6. Do not retry logout automatically after ambiguous network failure.

Tests:

- successful XML/empty logout response;
- 401/403, 5xx, timeout, connection loss, malformed response;
- repeated and concurrent close/logout;
- failed login followed by cleanup;
- original exception remains primary;
- V2/V5 behavior unchanged.

### P1.3 Optional network connectivity status

Evidence: `GET internet_access`, `Main/aggregate.js:54`, `checkForFirmwareAvailable()`, checking `internet_access.status == "true"`.

After a hardware sample confirms shape and stable semantics, expose this at most as an optional diagnostic binary sensor. It describes the NAS's internet access, not LAN reachability. A failure to query it must never make the coordinator unavailable. Poll no more frequently than the existing coordinator interval.

### P1.4 Authentication lifecycle and bounded V4 detection

After V4 logout is proven:

1. Extend automatic detection with one bounded V4 attempt in a deterministic sequence.
2. Reuse one client/session per attempt and close it completely before moving to another API version.
3. Distinguish connection/timeout, explicit authentication rejection, and response incompatible with that API generation.
4. Never try unlimited credential/version combinations and never run probes in parallel.
5. Keep the manual V4 selector/fallback for ambiguous devices and preserve stored version values in existing config entries.
6. Use a successful `system_information` structure as positive protocol evidence; do not infer V4 solely from firmware text.
7. If a V4 probe obtains a session but later validation fails, perform best-effort server logout and guaranteed local cleanup.

No config-entry version bump is required merely to recognize version 4 because the stored API-version field already supports a value. Revisit only if the data schema itself changes.

## Priority 2 – optional, sample-dependent improvements

### P2.1 Network diagnostics

`GET network_configuration` exposes protocol, IP, netmask, gateway, and DNS fields in the UI. Possible uses are diagnostic attributes or a disabled-by-default diagnostic sensor. Avoid duplicating Home Assistant's host value and do not expose MAC/IP/DNS data in logs or issue templates. Never call the corresponding PUT.

### P2.2 Diagnostic state visibility

`GET smart_test` and `GET smart_test2` may show an already-running diagnostic and per-drive status. A read-only status entity is possible only after confirming that GET has no side effect. Never start a test (`PUT smart_test`) without a distinct, explicit user action and separate approval. Raw paths/locations must be sanitized.

### P2.3 Device description and date/time diagnostics

`GET device_description` and `GET date_time_configuration` are supported read paths. They have limited monitoring value. Prefer device-info enrichment or diagnostics over new continuously polled entities. Machine names, descriptions, addresses, and time configuration require redaction in diagnostics.

### P2.4 USB storage information

`GET usb_drives` contains useful capacity/usage and device identity data, but USB disks are dynamic and serial numbers are sensitive. If requested later, model each drive with stable identifiers that do not expose raw serials and handle hot-plug removal without entity duplication. Never call unlock/eject routes from a polling path.

### P2.5 Alerts

Alerts could be high value, but the exact GET endpoints are not proved because the required form fragments are absent. Obtain the missing WD page fragment or a sanitized browser-network capture first. Only then define severity mapping, deduplication, timestamps, dismissal behavior, and polling. Do not implement guessed `alert_*` paths, and do not call `DELETE alerts` automatically.

### P2.6 RAID/internal-drive detail

`POST /settings/get_raid_details` returns JSON and can include model, capacity, serial, location, and SMART status, but it is outside the REST base and is tied to RAID UI code. The target `sq` model may not support it. Keep it out of the adapter until a read-only capture on the exact hardware proves availability and purpose. Never use adjacent RAID conversion routes.

## Priority 3 – administrative/state-changing functions

### P3.1 Restart and shutdown

The source confirms:

- `PUT /api/2.1/rest/shutdown?state=reboot`
- `PUT /api/2.1/rest/shutdown?state=halt`

The requests require the authenticated cookie session. The browser provides only a local confirmation dialog; no confirmation token or safety preflight appears in source.

Implementation gates:

1. Add offline request-construction tests before any hardware work: exact method, path, query value, same-session cookies, bounded timeout, no retry.
2. Review authorization/model gating. V4 buttons must not appear until the exact model/firmware path is validated.
3. Conduct a separately approved maintenance-window test with physical recovery access and a current backup. Reboot and shutdown are two separate approvals/tests.
4. Define outcomes narrowly: an explicit successful response is success; a connection drop after confirmed request transmission is **ambiguous**, not automatically success; pre-send connection errors and timeouts are failures. Never retry.
5. Only after validation, add Home Assistant buttons with explicit press actions, appropriate device class/icon, `CONFIG` category, device-specific unique IDs, and no invocation during setup/update.
6. Preserve V2/V5 power implementations and feature detection. Unsupported devices receive no buttons.

### P3.2 Excluded administrative APIs

Do not add automatic actions for firmware update, factory restore, RAID conversion, safepoint restore/delete, user/share mutation, network mutation, USB unlock/eject, media rebuild, SSH toggling, or support-log upload. These are outside monitoring scope and range from disruptive to destructive.

## Home Assistant integration design

Coordinator data should distinguish required core data from optional V4 extensions:

```text
required: system/device information
optional: firmware information, system state, storage usage, internet access
```

An optional endpoint failure should be retained as endpoint-specific availability/diagnostic information while successful core data continues to update. The coordinator should perform one login and reuse the session for all calls in a refresh cycle and across cycles until reauthentication is necessary. It must not create per-entity clients or trigger concurrent relogins.

Compatibility invariants:

- existing config entries continue using their stored API version;
- existing entity unique IDs and device identifiers are unchanged;
- new entities use the same device-specific identity basis and never hostname alone where a stable serial-derived identifier already exists;
- all new V4-only entities are conditionally created from supported, validated data;
- no entity setup, coordinator refresh, or availability callback can invoke a state-changing endpoint;
- V2/V5 fixtures and all existing tests remain unchanged and passing.

## Proposed implementation order

1. Capture sanitized `storage_usage`, logout, and optionally internet-access responses using one authenticated hardware session.
2. Add response fixtures and client parsing/logout tests.
3. Implement V4 storage usage and server logout in `wdnas_client` without changing public behavior for V2/V5.
4. Run the complete client suite, Ruff, syntax/import checks, and diff checks.
5. Integrate optional storage data into the coordinator and existing entity descriptions; add only justified new entities.
6. Add Home Assistant tests for optional failure isolation, session reuse, unload/reload logout, unique-ID stability, and V2/V5 regressions.
7. Consider bounded V4 auto-detection only after cleanup is proven.
8. Defer alerts, disk detail, and all administrative actions to separately scoped work.

## Acceptance criteria

- Sanitized real response shapes support every newly parsed field.
- No credential, cookie, serial, MAC, UUID, raw path, or hostname is logged or committed in fixtures.
- One session is reused; no unnecessary login occurs during normal refresh.
- Close/unload attempts server logout once and always releases local resources.
- Optional endpoint failures do not block established V4 entities.
- No numerical entity is derived from a qualitative word.
- No existing unique ID, entity ID, device identifier, or V2/V5 behavior changes.
- Full client and Home Assistant test suites, lint/format, syntax/import, manifest validation, and `git diff --check` pass before any commit.
