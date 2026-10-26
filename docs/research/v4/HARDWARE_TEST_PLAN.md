# WD My Cloud V4 safe hardware test plan

## Purpose

Validate the source-discovered, read-only V4 endpoints on NAS02 (`sq`, firmware `04.06.00-111`) while reusing a single authenticated client session. This plan does not authorize a live test by itself. It explicitly excludes power actions, configuration changes, firmware operations, RAID operations, factory restore, USB actions, share/user changes, safepoint operations, and starting SMART diagnostics.

## Preconditions

- Run from the same trusted LAN as NAS02; do not use the known non-working VPN path.
- Use an isolated virtual environment and the current local `wdnas_client` V4 implementation, not a PyPI package or previously published commit.
- Confirm the imported module path points to the intended local checkout before entering credentials.
- Use `version=4` explicitly. Do not probe other API generations in this test.
- Obtain the host, username, and password interactively. Use `getpass` for the password.
- Do not print request URLs containing query data, request headers, cookies, credentials, or raw exception objects that may embed them.
- Ensure no Home Assistant production files, config entries, registries, or running containers are changed.
- Agree on a maximum per-request timeout of 15 seconds and no automatic credential retry.
- Store reports only after sanitization. Prefer an in-memory result followed by an explicit sanitized export.

## Session lifecycle

1. Create exactly one client and one underlying HTTP session.
2. Authenticate once with `version=4`.
3. Record only sanitized status: success/failure, HTTP status if available, elapsed time, exception class, and a redacted response-structure summary.
4. Execute the approved GET calls sequentially through the same authenticated session.
5. Verify session reuse by instrumenting/counting client login calls locally; do not inspect or print the cookie value. Expected count: one.
6. If any endpoint fails, record it independently and continue with the remaining approved read-only calls unless authentication/session safety is uncertain.
7. At the end, perform the separately listed logout check if approved. Regardless of its result, close the local HTTP session exactly once.
8. Do not retry an invalid credential. Stop authentication testing and report sanitized HTTP status and exception type.

## Sanitization rules

Before display or persistence, replace:

- hostname/IP with `<HOST>`;
- username with `<USER>`;
- serial numbers and device IDs with stable placeholders such as `<SERIAL_1>`;
- MAC addresses with `<MAC_1>`;
- UUIDs with `<UUID_1>`;
- filesystem/share/device paths with `<PATH_1>`;
- share names, user names, machine descriptions, DNS servers, gateway and IP addresses with typed placeholders;
- cookies, authorization values and passwords with `<REDACTED>` or omit them entirely.

Do not preserve hashes of passwords, cookies, or session tokens. For identifiers, a run-local numbered placeholder is safer than a reusable hash. XML element names, nesting, value types, string lengths, and non-identifying health/status words may be retained.

## Test matrix

### Baseline: already hardware-confirmed calls

These calls establish that the current V4 adapter remains functional. Do not change their implementation during this research.

| Order | Request | Expected evidence | Record |
|---:|---|---|---|
| 1 | Existing authentication using `GET local_login` | One authenticated session, no repeated login | Success/failure, status, exception class, login-call count |
| 2 | `GET system_information` | Parsed mapping/object; model resolves to `sq`; capacity present | Top-level/nested keys, value types, redacted identifiers, capacity magnitude/unit hypothesis |
| 3 | `GET firmware_info` | Parsed mapping/object; firmware `04.06.00-111` | Keys/types and firmware string |
| 4 | `GET system_state` | Parsed mapping/object with expected qualitative status fields | Presence and values of `status`, `temperature`, `smart`, `volume`, `free_space`, `reported_status`; no numeric interpretation |

Pass criteria: XML parses without lossy structural ambiguity, model and firmware match the target, expected state fields are recognizable, and only one login was used.

### Candidate A: storage usage (Priority 1)

Request: `GET /api/2.1/rest/storage_usage`.

Record:

- HTTP status and elapsed time;
- XML root and child-element names;
- whether `size`, `usage`, `photos`, `music`, `video`, and `other` are present;
- raw numeric strings after confirming they contain no identifier;
- parsed Python types and any whitespace/empty-value behavior;
- computed checks, without changing raw values:
  - `size >= 0`;
  - `usage >= 0`;
  - `usage <= size`;
  - candidate free value `size - usage`;
  - candidate used percentage `usage / size * 100` when `size > 0`;
  - comparison between `size` and the already reported total capacity;
  - whether media categories approximately sum to `usage` (informational only).

Do not assume units until the magnitude is compared with the known physical capacity and the WD UI's formatting behavior. Preserve the raw fields even if totals disagree.

Pass criteria: stable XML shape and plausible, internally consistent numeric values. Partial pass is allowed when optional media-category fields are absent.

### Candidate B: internet access (Priority 1/optional)

Request: `GET /api/2.1/rest/internet_access` only. Do not call the captured PUT form.

Record the root/keys and exact non-sensitive status token. Confirm whether the value is the string `true`/`false`, a number, or another representation. Compare only with known NAS internet connectivity at test time; do not equate it with LAN reachability.

Pass criteria: a stable `internet_access.status` value whose semantics match the observed network condition.

### Candidate C: network configuration (Priority 2, optional approval)

Request: `GET /api/2.1/rest/network_configuration` only.

Record only field names and types. Redact every address/value. Specifically check the presence of `proto`, `ip`, `netmask`, `gateway`, `dns0`, `dns1`, and `dns2`. Do not call PUT and do not compare or change device configuration.

Pass criteria: parsed structure is understood and the stored artifact contains no network identifiers.

### Candidate D: existing diagnostic status (Priority 2, optional approval)

Requests: `GET smart_test`, followed by `GET smart_test2` only if the first response and approval justify it.

These calls must only observe current status. Do not call `PUT smart_test` and do not start, stop, schedule, or resume a diagnostic. Redact drive locations and paths. Record whether a test is idle/running and the element names/types; do not keep serials.

Pass criteria: GET is observed to be side-effect-free and its status schema is clear. If any response suggests the GET initiates work, stop immediately and classify it as unsafe.

### Candidate E: server logout (Priority 1 lifecycle check)

At the very end of the session, and only once: `GET /api/2.1/rest/logout` using the same cookie session.

Record status, elapsed time, response root/key names or empty-body status, and exception class. Never print cookies. After the request, close the local session regardless of result.

An optional proof that the server session is invalidated would require a subsequent protected GET, but that creates an extra request and may trigger reauthentication in current client code. Perform it only with a low-level, non-reauthenticating request and explicit approval; otherwise mark server invalidation as unverified.

Pass criteria for client implementation: a reproducible success response is understood. A UI-style local transition after failure is not proof of logout.

## Independent failure handling

For each endpoint, classify outcomes as:

- **PASS**: expected status, XML parsed, required structure present, values semantically plausible.
- **PARTIAL**: endpoint works but optional fields/semantics remain uncertain.
- **UNSUPPORTED**: explicit 404/known not-supported response.
- **AUTH FAILURE**: 401/403 or confirmed authentication error; stop further protected calls to avoid relogin loops.
- **NETWORK FAILURE**: timeout, DNS/routing failure, or connection error, recorded by class only.
- **PARSE FAILURE**: response received but XML/root/field shape differs; retain only a sanitized structural sample.
- **UNSAFE/AMBIGUOUS**: any indication of a side effect or unclear operation; stop that test path.

One optional endpoint failure must not erase successful results for the others. Do not repeatedly retry. A single retry is not part of this plan.

## Polling/load limits

This is a one-shot validation, not a soak test. Each approved endpoint is called once. Do not reproduce the WD UI's 5-second or 15-second polling. If later coordinator validation is needed, use the integration's normal interval for a short, separately approved observation and confirm no repeated login.

## Explicitly prohibited requests

- `PUT shutdown` with either state;
- any `POST system_factory_restore`;
- `PUT smart_test`;
- firmware upload/update routes;
- RAID conversion/status-changing `/settings/*` routes;
- user/share/device-user mutations;
- network, SSH, workgroup, LED, standby, date/time, privacy, or language PUTs;
- USB unlock/eject;
- safepoint create/update/restore/delete;
- media database rebuild/rescan or iTunes scan;
- support-log creation/upload/download;
- alert dismissal.

## Result record template

```text
Run ID: <local non-device identifier>
Date/time zone: <timestamp, Europe/Berlin>
Client source revision: <commit plus dirty-state marker; no patch contents>
Imported client path: <local sanitized path>
Target: <HOST>, expected model sq, expected firmware 04.06.00-111
Login attempts: 1

Endpoint: storage_usage
Method: GET
Result: PASS | PARTIAL | UNSUPPORTED | AUTH FAILURE | NETWORK FAILURE | PARSE FAILURE
HTTP status: <status or unavailable>
Elapsed: <seconds>
Response root: <name>
Fields/types: <sanitized mapping>
Relevant measurements: <safe numeric values only>
Notes: <no identifiers, URLs with credentials, cookies or raw exceptions>

Session reuse verified: yes/no, observed login call count <n>
Server logout attempted: yes/no
Server logout result: <sanitized status>
Local session closed: yes/no
```

## Readiness decision

The client is ready for the next Home Assistant V4 monitoring iteration when:

- baseline calls still pass for `sq`/`04.06.00-111`;
- `storage_usage` has a stable, sanitized fixture and validated unit/consistency rules;
- one login serves every request and local close succeeds;
- the V4 logout response is understood, or its remaining limitation is explicitly accepted;
- optional endpoint failures do not trigger repeated authentication;
- no sensitive value appears in console output, test artifacts, fixtures, or reports;
- all proposed parsing is backed by captured structure rather than endpoint-name inference.

Power management readiness is a separate decision and is not established by this read-only plan.
