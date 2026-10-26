# WD My Cloud V4 support review

Date: 2026-10-25
Target: original WD My Cloud, model number `sq`, firmware `04.06.00-111`
Project identifier: V4 (`version=4`)
WD product family: My Cloud OS 3

## Evidence and scope

The following REST 2.1 XML resources returned HTTP 200 when called from an
already authenticated browser session:

- `GET /api/2.1/rest/system_information`
- `GET /api/2.1/rest/firmware_info`
- `GET /api/2.1/rest/system_state`

The legacy interface identifies its login as
`GET /api/2.1/rest/local_login` with `username` and `password` query
parameters. Programmatic authentication outside the browser is not yet proven;
a previous PowerShell attempt may have been affected by VPN or network routing.
The implementation is therefore provisional and is covered only by simulated
HTTP responses at this stage.

The initial review did not have original V4 JavaScript containing the proposed
shutdown request. A later supplied capture under `docs/research/v4/Main` now
provides the missing source for the exact target model and firmware. It proves
`PUT /api/2.1/rest/shutdown` with `state=reboot` or `state=halt`, using the
existing cookie session and no server-side safety preflight. Test build 8
implements these calls offline and gates its buttons to model `sq`, firmware
`04.06.00-111`. No power request was sent to NAS02.

## Client implementation

`wdnas_client.v4.V4Adapter` owns the REST 2.1-specific request and XML parsing
logic while reusing the existing client session and lifecycle.

- One `aiohttp.ClientSession` retains authentication cookies.
- Re-entering an authenticated client does not log in again.
- HTTP 401/403 during login or reads raises `InvalidLoginError`.
- Other HTTP failures retain `RequestFailedError`.
- Login transport errors are replaced with a sanitized error so a query URL
  containing credentials cannot escape through the exception text.
- Closing V4 is local-only because no server-side logout has been established.
- Stable system-information and firmware responses are cached only for the
  lifetime of the authenticated client session and cleared on close/relogin.

Available V4 methods:

- `system_information()`
- `firmware_info()`
- `system_state()`
- compatible adapters through `system_info()`, `device_info()`,
  `system_version()`, and `system_status()`

The verified `capacity` label is converted from decimal units such as `2TB` to
bytes. Unknown labels remain `None`. The API evidence does not contain CPU,
memory, used/free byte counts, numeric temperature, detailed disks, or detailed
volumes. Those values remain absent rather than inferred.

## Home Assistant behavior

Automatic detection still probes OS 5 first and OS 2 only when the OS 5
endpoint is absent. If both known protocols are absent, the serializable manual
selector offers V5, V4, and V2. V4 is not automatically authenticated during
the config flow: without a proven server logout, a disposable validation login
could block the coordinator on a single-admin-session device. The coordinator
performs the only setup login. V4 reauthentication similarly updates the
credentials and lets the reloaded coordinator perform the single login.

Existing V2/V5 config entries and unique IDs are unchanged. New V4 entities use
the real NAS serial number when supplied by `system_information`; the existing
config-entry identity is a fallback only when an incomplete response omits it.

Implemented V4 entities:

- system status sensor (`ready` in the verified sample)
- temperature health sensor (`good`, not degrees Celsius)
- total storage sensor when the capacity label parses safely
- SMART problem binary sensor
- volume problem binary sensor
- free-space problem binary sensor
- firmware version in Home Assistant device information

No CPU, memory, numeric disk-temperature, topology, or volume-size entities are
created for V4. Test build 8 adds source-confirmed restart and shutdown buttons
only for model `sq` with firmware `04.06.00-111`.

Firmware and system-state calls are optional in the coordinator. Their failure
does not discard core device identity and capacity. Authentication failures are
not treated as optional and trigger the existing bounded reauthentication path.

## Security limitations

The firmware uses plain HTTP and places credentials in query parameters. HTTP
does not encrypt the URL, so credentials can be observed by network devices and
may be captured by generic proxy/access logging. Use only on a trusted local
network, disable URL logging, and never expose the management API to the
internet. The integration itself does not log the request URL, credentials,
cookies, serial number, or response payload.

## Offline validation

The client suite covers V4 login construction, authentication errors,
cookie/session reuse, sanitized connection failure, 401/403 expiry, all three
verified XML shapes, incomplete/malformed XML, capacity conversion, and
explicit rejection of unverified operations.

The Home Assistant suite covers manual V4 selection, direct validation support,
single-login setup and reauthentication design, optional endpoint failures,
expired-session retry, V4 entity creation and states, the absence of unsupported
entities/buttons, Probatio serialization, and all previous V2/V5 behavior.

At the time of this report, 51 client tests and 94 Home Assistant tests pass.

## Required controlled hardware validation

1. Ensure VPN software is disconnected or known not to route the NAS subnet.
2. Close all WD web-interface tabs and wait for any prior administrator session
   to expire.
3. Use an isolated script or Home Assistant test instance with request logging
   disabled.
4. Perform exactly one V4 login, then read the three verified endpoints.
5. Unload/close the client and confirm whether a second login is immediately
   possible. Capture only sanitized status codes, headers, cookie names, and XML
   element names.
6. If login fails, record the redacted status and response shape before making
   another attempt. Do not classify an account-in-use response without this
   evidence.
7. Supply the original firmware JavaScript for the shutdown UI before enabling
   restart or shutdown, then test request construction offline first.

No power operation should be executed against NAS02 without separate explicit
approval and a current backup.
