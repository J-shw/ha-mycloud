# WD My Cloud device actions and API detection

Date: 2026-10-25

Scope: local development branches `ha-mycloud/feature/os5-monitoring` and
`wdnas_client/feature/my-cloud-mirror-os5`. No live power action, commit, push,
release, or productive Home Assistant change was performed.

## Confirmed CGI commands

The supplied WD My Cloud Mirror firmware 5.33.102 dashboard source
`<WD-Mirror>/resources/function.js` contains the
following sequence:

| Source | Purpose | Request |
| --- | --- | --- |
| `function.js:1600-1643` | Restart dialog and action | first `POST /cgi-bin/hd_config.cgi` with `cmd=cgi_Detect_Dangerous`; only when XML `res` is `0`, `POST /cgi-bin/system_mgr.cgi` with `cmd=cgi_restart` |
| `function.js:1646-1685` | Shutdown dialog and action | the same safety check; only when XML `res` is `0`, `POST /cgi-bin/system_mgr.cgi` with `cmd=cgi_shutdown` |

The dashboard comments state that a non-zero safety result blocks shutdown or
restart while formatting, firmware upload, or another dangerous operation is
active. The implementation preserves this ordering. No command in this section
was sent to a real NAS.

## Python client

`wdnas_client` now provides asynchronous OS 5 methods `reboot()` and
`shutdown()`. Both:

- reuse the existing authenticated `aiohttp` session;
- reject API version 2 before any request;
- enforce a 15-second timeout;
- require a successful `cgi_Detect_Dangerous` response with numeric `res=0`;
- raise `PowerActionBlockedError` for a non-zero result;
- propagate HTTP errors, timeouts, malformed XML, and unknown transport errors;
- accept only `aiohttp.ServerDisconnectedError` during the already-issued
  action request as the expected immediate device transition.

`RequestFailedError` now exposes its existing HTTP value through
`status_code`, while its message and exception compatibility remain unchanged.
API and supported-model documentation were updated. OS 2 behavior and all
existing read methods remain unchanged.

## Home Assistant buttons

The new `button` platform registers two NAS-scoped entities:

- Restart: `ButtonDeviceClass.RESTART`, configuration category;
- Shutdown: generic button, `mdi:power`, configuration category.

Unique IDs are `<NAS serial>_restart` and `<NAS serial>_shutdown`, and both use
the established main NAS device identifier. Registration is deliberately
limited to API version 5 plus a device description containing
`My Cloud Mirror`. OS 2 and unverified OS 5 models receive no buttons. Setup,
polling, and coordinator refreshes never invoke either action.

The private HAOS manifest is pinned to the immutable client commit
`261805666f3decee3901fa3077871951e9a8d66a`, which contains the tested power
methods. The former compatibility adapter has been removed. Buttons now call
only `await coordinator.client.reboot()` or
`await coordinator.client.shutdown()`.

## Config flow and password handling

The normal setup form contains host, username, and a Home Assistant
`TextSelector` configured as `PASSWORD` with
`autocomplete="current-password"`. Reauthentication uses the same password
selector. There is currently no reconfigure step. Password values are not
included in action or config-flow log messages.

The setup form no longer requires an API version. Detection uses protocol
evidence rather than firmware text:

1. It attempts one OS 5 authentication against the version-specific
   `/nas/v1/auth` endpoint, requires the OS 5 `PHPSESSID`, and then performs the
   read-only `device_info()` call.
2. It tries OS 2 only when the OS 5 login endpoint conclusively returns HTTP
   404 or 405.
3. A successful probe stores integer API version 5 or 2 in the unchanged
   config-entry data key `Version`.
4. HTTP 401/403 and client login failures are authentication errors. Transport
   errors and timeouts are connection errors. Both login endpoints returning
   404/405 is unsupported firmware.
5. Other HTTP responses are not guessed; the flow presents a serializable
   manual OS 5/OS 2 selector and validates that explicit choice.

Probes are sequential, bounded, and each temporary session is closed. Existing
config entries keep their stored version, and reauthentication continues to use
that version without detection or migration. Duplicate matching now uses the
normalized host before detection and the stable device unique ID afterwards.

## Compatibility correction found by HA 2026.10.4

The target-version test exposed the deprecated multi-owner
`DeviceEntry.config_entries` property in the existing volume compatibility
path. The code now uses the single-owner `config_entry_id` property on current
Home Assistant and retains a guarded fallback for older versions. It does not
modify registry entries or change legacy unique IDs.

## Offline validation

- Home Assistant Core 2026.10.4, Probatio 0.13.0,
  `pytest-homeassistant-custom-component` 0.13.375 under isolated WSL2:
  **79 passed**.
- Windows development environment: **79 passed**.
- `wdnas_client` unit tests: **33 passed**.
- Regression coverage includes OS 2 and OS 5 detection, missing endpoints,
  invalid credentials, unreachable devices, ambiguous responses, existing
  config entries, password selector serialization, manual fallback
  serialization, supported/unsupported button registration, explicit press,
  action failures, safety blocks, no setup/poll actions, and unique IDs.
- Direct HA 2026.10.4 serialization confirms the user and fallback forms are
  accepted by Probatio through Home Assistant's real `cv.custom_serializer`.
- Ruff, syntax/import, JSON/YAML, ZIP-content, and `git diff --check` results are
  recorded in the task handoff after the final package build.

## Remaining risks

- Restart and shutdown are source-confirmed and mock-tested but deliberately
  not hardware-tested. The exact success body and disconnect timing therefore
  remain unverified on the physical Mirror.
- Capability gating depends on the device description returned by the NAS. A
  supported Mirror with an unexpected localized/model string will safely omit
  the buttons until explicitly added.
- A manual fallback cannot make an actually broken endpoint work; it prevents
  unsafe guessing and then reports the normal validation error.
- Installation requires Git access from Home Assistant so it can resolve the
  immutable client requirement. No PyPI version contains these methods yet.
