# WD My Cloud V4 API inventory

## Scope and evidence conventions

This inventory is based exclusively on the supplied WD web-interface files for model `sq`, firmware `04.06.00-111`. No NAS request was made during this analysis. The REST base used by the application is `/api/2.1/rest/` (`Main/aggregate.js:54`, global `apiUrlPrefix`). The same minified application bundle also exists as `login/aggregate.js`; both copies have the same SHA-256 hash. References to `Main/aggregate.js:54` therefore apply to both copies. Because almost all application code is minified onto that one physical line, the function or handler name is included as the stable semantic locator.

Unless a row says otherwise, `$.ajaxAPI` parses successful text responses as XML with `XML.ObjTree` and supplies a JavaScript object to the caller (`Main/aggregate.js:54`, `$.ajaxAPI`). Parameters shown for `PUT` and `DELETE` are appended to the URL by `$.ajaxAPI` and are also passed to jQuery as request data. HTML form actions and methods are useful evidence, but an initialized `restForm` may issue an additional read-only `GET` when refreshed (`Main/aggregate.js:54`, `restForm.refreshForm`).

Verification labels:

- **Hardware-tested**: already confirmed by the existing V4 hardware test described in the task.
- **Source-only**: proved by the supplied web-interface source, but not called during this analysis.
- **Form-only**: an HTML form proves the route/method, but no response consumer was captured.

Authentication is described as **cookie session** where the request is made after `local_login`; the browser automatically retains and sends the cookie. The source does not disclose the cookie name or value.

## 1. Authentication and session management

| Endpoint | HTTP method | Parameters | Authentication | Response | Purpose | Evidence | Verification | Risk |
|---|---|---|---|---|---|---|---|---|
| `/api/2.1/rest/eula_acceptance` | GET | None | Pre-login | XML | Determine whether the EULA gate must be shown | `Main/aggregate.js:54`, `authentication.checkEula()` | Source-only | Read-only |
| `/api/2.1/rest/eula_acceptance` | POST | Form/query `accepted=yes` | Pre-login | XML | Accept EULA | `Main/aggregate.js:54`, `authentication.acceptEula()`; the form at `Main/(index):203` declares PUT, but the invoked handler explicitly sends POST | Source-only | Modifies state |
| `/api/2.1/rest/users` | GET | None | Available during login discovery; normal use is authenticated | XML: `users.user`, including `username`, `is_admin`, `is_password` | Discover administrative users and whether a password is configured | `Main/aggregate.js:54`, `authentication.checkUsers()` | Source-only | Read-only; privacy-sensitive |
| `/api/2.1/rest/local_login` | GET | Query/form `username`, `password` | Establishes session | XML/text on HTTP success; browser retains response cookies | Authenticate an administrator | `Main/aggregate.js:54`, `authentication.authenticate()` | **Hardware-tested** | Credential-bearing; never log URL/data |
| `/api/2.1/rest/logout` | GET | None | Cookie session | XML/text ignored by UI | End the current session | `Main/aggregate.js:54`, `authentication.logout()` | Source-only | Ends session |

### Login/session behavior

- Credentials are passed to jQuery as ordinary `username` and `password` values. No Base64, hashing, challenge response, token, or custom authorization header is applied by the application (`authentication.authenticate`). Transport security is therefore essential; a plain HTTP connection exposes credentials to the network.
- Session persistence is cookie-based and implicit in the browser's same-origin cookie jar. No cookie name, CSRF token, bearer token, or explicit session identifier appears in the supplied sources.
- The UI first checks `eula_acceptance`, then calls `users`, validates the selected name against discovered administrators, and finally calls `local_login`.
- The original success callback treats an HTTP-successful `local_login` response as successful without examining an XML error field. The current adapter's hardware-proven behavior should therefore be preserved until a captured failure response demonstrates a safer content check.
- `$.ajaxAPI` intercepts HTTP 401 for every endpoint except `local_login`, queues the failed request, starts one guarded authentication flow, and replays queued requests after login. HTTP 403 is not handled specially. The current Python adapter's acceptance of both 401 and 403 as authentication failures is a conservative improvement.
- `logout()` shows the login dialog in its completion callback even when the request fails. Thus UI behavior does not prove server-side logout succeeded. A hardware test must distinguish a successful HTTP response from local cleanup.
- Session expiry duration, maximum concurrent sessions, cookie attributes, and whether a second login invalidates the first cannot be derived from these files.

## 2. Device and firmware information

| Endpoint | HTTP method | Parameters | Authentication | Response | Purpose | Evidence | Verification | Risk |
|---|---|---|---|---|---|---|---|---|
| `/api/2.1/rest/system_information` | GET | None | Cookie session | XML `system_information` | Model, serial, capacity, identity information | `Main/aggregate.js:54`, `initDeviceTitle()`; refreshable form `Main/(index):1802` | **Hardware-tested** | Read-only; identifiers are sensitive |
| `/api/2.1/rest/system_information` | PUT | Form present, no visible named value in captured form | Cookie session | XML | Administrative system-information action | `Main/(index):1802` | Form-only | Modifies state; purpose/parameters incomplete |
| `/api/2.1/rest/firmware_info` | GET | None | Cookie session | XML `firmware_info` | Installed firmware details | `Main/aggregate.js:54`, dashboard firmware handler | **Hardware-tested** | Read-only |
| `/api/2.1/rest/firmware_info` | PUT | `image` in the captured form | Cookie session | XML | Firmware availability/update-related action | `Main/(index):2410`; `Main/aggregate.js:54`, firmware-check handler | Source-only | Administrative; potentially disruptive |
| `/api/2.1/rest/device_description` | GET | None | Cookie session | XML `device_description` with `machine_name`, `machine_desc` | Read device name and description | `Main/aggregate.js:54`, refreshed device-description form; `Main/(index):1749` | Source-only | Read-only |
| `/api/2.1/rest/device_description` | PUT | `machine_name`, `machine_desc` | Cookie session | XML | Change name/description | `Main/(index):1779-1790` | Form-only | Modifies state |
| `/api/2.1/rest/device` | GET | None | Cookie session | XML | Retrieve device/remote-access information | `Main/aggregate.js:54`, device/remote-access handlers | Source-only | Read-only; may contain identifiers |
| `/api/2.1/rest/firmware_update` | GET, PUT | Update-dependent values; upload flow uses `format=json` | Cookie session | XML for REST calls; JSON for upload flow | Inspect or initiate firmware update | `Main/aggregate.js:54`, firmware-update handlers | Source-only | Administrative/disruptive |
| `/api/2.1/rest/firmware_update_configuration` | PUT | Form fields from automatic-update settings | Cookie session | XML | Change firmware-update policy | `Main/(index):2332`; `Main/aggregate.js:54`, initialized form | Form-only | Modifies state |

## 3. System monitoring

| Endpoint | HTTP method | Parameters | Authentication | Response | Purpose | Evidence | Verification | Risk |
|---|---|---|---|---|---|---|---|---|
| `/api/2.1/rest/system_state` | GET | None | Cookie session | XML `system_state`: observed consumers include `status`, `temperature`, `smart`, `volume`, `free_space`, `reported_status` | Overall and qualitative health | `Main/(index):2316`; `Main/aggregate.js:54`, dashboard health/status handlers | **Hardware-tested** | Read-only |
| `/api/2.1/rest/update_counts` | GET | None | Cookie session | XML counters | Detect changes and refresh UI sections | `Main/aggregate.js:54`, `updateCountHandler` and polling setup | Source-only | Read-only, but unnecessary polling load |
| `/api/2.1/rest/update_counts?ping` | GET | Query flag `ping` (no value in source) | Cookie session | XML/text | Readiness/ping check during UI startup | `Main/aggregate.js:54`, device-ready polling | Source-only | Read-only |
| `/api/2.1/rest/date_time_configuration` | GET | None | Cookie session | XML date/time/time-zone values | Display current time configuration | `Main/aggregate.js:54`, `initDatetime()` and refreshed forms | Source-only | Read-only |
| `/api/2.1/rest/date_time_configuration` | PUT | Date/time, NTP and time-zone fields from forms | Cookie session | XML | Change time configuration | `Main/(index):1861,1968,2779,2888` | Form-only | Modifies state |
| `/api/2.1/rest/hdd_standby_time` | GET, PUT | PUT form contains standby settings | Cookie session | XML | Read/change disk standby configuration | `Main/(index):2013`; `Main/aggregate.js:54`, refreshed `restForm` | Source-only | GET read-only; PUT modifies state |
| `/api/2.1/rest/led_configuration` | GET, PUT | PUT form contains LED state/settings | Cookie session | XML | Read/change LED configuration | `Main/aggregate.js:54`, initialized/refreshed LED form | Source-only | GET read-only; PUT modifies state |
| `/api/2.1/rest/privacy_options` | GET, PUT | PUT form contains privacy/telemetry selection | Cookie session | XML | Read/change privacy options | `Main/aggregate.js:54`, `get_new_privacy()` and privacy form | Source-only | GET read-only; PUT modifies state |
| `/api/2.1/rest/language_configuration` | GET, PUT | Language selection on PUT | Cookie session | XML | Read/change UI language | `Main/aggregate.js:54`, refreshed language form | Source-only | GET read-only; PUT modifies state |

The supplied JavaScript does **not** prove numerical CPU usage, RAM usage, uptime, or temperature in degrees Celsius. `system_state.temperature` is interpreted only as a qualitative state. Those measurements must not be inferred or exposed as numerical sensors.

## 4. Storage and volumes

| Endpoint | HTTP method | Parameters | Authentication | Response | Purpose | Evidence | Verification | Risk |
|---|---|---|---|---|---|---|---|---|
| `/api/2.1/rest/storage_usage` | GET | None | Cookie session | XML `storage_usage`: `size`, `usage`, `photos`, `music`, `video`, `other` | Dashboard capacity and media breakdown | `Main/aggregate.js:54`, dashboard `updateStorage`/`updateDashboardHandler` | Source-only | Read-only |
| `/api/2.1/rest/usb_drives` | GET | None | Cookie session | XML `usb_drives.usb_drive` with handle, name, vendor, model, serial, revision, capacity, usage, lock state | List USB disks and capacity | `Main/(index):36,1349`; `Main/aggregate.js:54`, `initUSB()`/`addUSBtoList()` | Source-only | Read-only; serials/paths sensitive |
| `/api/2.1/rest/usb_drive` | PUT | `drive`, password-related values depending on form | Cookie session | XML | USB administration | `Main/(index):1418` | Form-only | Modifies state |
| `/api/2.1/rest/usb_drive/{handle}` | PUT | `drive_password` (Base64), `save`, `encoding=base64` | Cookie session | XML | Unlock USB disk | `Main/aggregate.js:54`, `unlockUSB()` | Source-only | Modifies state; credential-bearing |
| `/api/2.1/rest/usb_drive/{handle}` | DELETE | Path parameter `handle` | Cookie session | XML | Eject USB disk | `Main/aggregate.js:54`, `ejectUSB()` | Source-only | Disruptive |
| `/settings/get_raid_details` | POST (outside REST base) | UI-selected RAID context | Cookie session | JSON: `internal_drive[]` including manufacturer, model, serial, drive size, SMART status, location | Populate RAID drive details | `Main/aggregate.js:54`, `doSelectRaidDrive()`; explicit non-REST URL prefix | Source-only | Read-only but model-specific and identifier-sensitive |

For `storage_usage`, the WD UI treats `size` and `usage` as byte-like quantities, formats them with `ByteSize`, and calculates free storage as `size - usage` and free percentage as `(size - usage) / size`. This is strong implementation evidence, but a raw hardware sample is still required to confirm units and edge cases on `sq`. The media category fields are also treated as quantities; whether they sum exactly to `usage` is not established.

No general V4 REST endpoint exposing per-disk numerical temperature, ATA SMART attributes, or internal-drive model/capacity was found. The RAID-details JSON call is not evidence that it works on this single-volume `sq` model.

## 5. Disk health and diagnostics

| Endpoint | HTTP method | Parameters | Authentication | Response | Purpose | Evidence | Verification | Risk |
|---|---|---|---|---|---|---|---|---|
| `/api/2.1/rest/smart_test` | GET | None | Cookie session | XML smart-test status/progress | Read diagnostic state | `Main/(index):2254`; `Main/aggregate.js:54`, SMART-test handlers | Source-only | Read-only |
| `/api/2.1/rest/smart_test2` | GET | None | Cookie session | XML; consumer reads `internal_drive[].status` and `.location` | Show per-drive failure/status details | `Main/(index):2255`; `Main/aggregate.js:54`, SMART result handler | Source-only | Read-only; paths/locations sensitive |
| `/api/2.1/rest/smart_test` | PUT | `test` | Cookie session | XML | Start/alter a disk diagnostic | `Main/(index):2233`; `Main/aggregate.js:54`, SMART-test form | Source-only | Stateful diagnostic; do not call without approval |

`system_state.smart` and `system_state.volume` remain the only hardware-confirmed health signals. They are qualitative, not raw SMART attributes or error counters.

## 6. Network information

| Endpoint | HTTP method | Parameters | Authentication | Response | Purpose | Evidence | Verification | Risk |
|---|---|---|---|---|---|---|---|---|
| `/api/2.1/rest/network_configuration` | GET | None | Cookie session | XML; consumers include `proto`, `ip`, `netmask`, `gateway`, `dns0`, `dns1`, `dns2` | Display network configuration | `Main/(index):1752,2090,2526`; `Main/aggregate.js:54`, refreshed network forms | Source-only | Read-only; addresses are sensitive |
| `/api/2.1/rest/network_configuration` | PUT | Network form fields | Cookie session | XML | Change network configuration | Same forms/handlers as above | Form-only | Disruptive; may sever access |
| `/api/2.1/rest/internet_access` | GET | None | Cookie session | XML `internet_access.status` (`"true"` is tested by UI) | Determine internet connectivity before firmware check | `Main/aggregate.js:54`, `checkForFirmwareAvailable()` | Source-only | Read-only |
| `/api/2.1/rest/internet_access` | PUT | No explicit parameter in captured form | Cookie session | XML | Trigger/update internet-access state/check | `Main/(index):2071` | Form-only | Potentially stateful; avoid |
| `/api/2.1/rest/network_services_configuration` | GET, PUT | Service toggles on PUT | Cookie session | XML | Read/change network services | `Main/(index):2113`; `Main/aggregate.js:54`, refreshed form | Source-only | GET read-only; PUT modifies state |
| `/api/2.1/rest/network_workgroup` | GET, PUT | Workgroup settings on PUT | Cookie session | XML | Read/change SMB workgroup | `Main/(index):2127`; `Main/aggregate.js:54`, refreshed form | Source-only | GET read-only; PUT modifies state |
| `/api/2.1/rest/ssh_configuration` | GET, PUT | SSH enable/settings on PUT | Cookie session | XML | Read/change SSH service | `Main/(index):2104`; `Main/aggregate.js:54`, refreshed form | Source-only | GET read-only; PUT security-sensitive |

## 7. Shares and users

| Endpoint | HTTP method | Parameters | Authentication | Response | Purpose | Evidence | Verification | Risk |
|---|---|---|---|---|---|---|---|---|
| `/api/2.1/rest/users` | GET, POST, PUT, DELETE | User fields; target user may be in path/form depending on action | Cookie session except login discovery GET | XML | List/create/update/delete users | `Main/(index):231,1018,1048,1050,3363`; `Main/aggregate.js:54`, user handlers | Source-only | GET privacy-sensitive; others destructive/state-changing |
| `/api/2.1/rest/user` | PUT | User form fields | Cookie session | XML | User update action; singular route as captured | `Main/(index):254` | Form-only | Modifies state |
| `/api/2.1/rest/shares` | GET, POST, PUT, DELETE | Share fields and selected share | Cookie session | XML | List/create/update/delete shares | `Main/(index):670,3253,3283`; `Main/aggregate.js:54`, share handlers | Source-only | GET privacy-sensitive; others destructive/state-changing |
| `/api/2.1/rest/device_user` | GET, POST | Device/user association data | Cookie session | XML | List/create remote device-user association | `Main/aggregate.js:54`, device-user handlers | Source-only | GET privacy-sensitive; POST modifies state |
| `/api/2.1/rest/device_user/{id}` | DELETE | Path parameter `id` | Cookie session | XML | Remove association | `Main/aggregate.js:54`, device-user removal handler | Source-only | Destructive |
| `/api/2.1/rest/share_access/{share}` | POST | Access-control form values | Cookie session | XML | Change share permissions | `Main/aggregate.js:54`, share-access handler | Source-only | Security-sensitive/state-changing |

These endpoints can expose usernames, share names, paths, and access policy. They are not recommended as Home Assistant entities.

## 8. Power management

| Endpoint | HTTP method | Parameters | Authentication | Response | Purpose | Evidence | Verification | Risk |
|---|---|---|---|---|---|---|---|---|
| `/api/2.1/rest/shutdown?state=reboot` | PUT | `state=reboot`; `$.ajaxAPI` appends PUT data to query and also supplies it as request data | Cookie session | XML/text if the connection remains available | Restart NAS | `Main/aggregate.js:54`, `doReboot()`; confirmation dialog markup `Main/(index):2691-2700` | Source-only; deliberately not executed | Disruptive |
| `/api/2.1/rest/shutdown?state=halt` | PUT | `state=halt`; same serialization behavior | Cookie session | XML/text if available | Shut down NAS | `Main/aggregate.js:54`, `doShutdown()`; shutdown dialog/link handlers in same bundle | Source-only; deliberately not executed | Disruptive |

The suspected routes are confirmed. No server-issued confirmation token, CSRF value, dangerous-action preflight, or other prerequisite is present in the captured code. The only confirmation is a local UI dialog; the request is sent after the user clicks its confirmation button. Both operations use the authenticated cookie session and `ajaxTimeout` (300 seconds). A dropped connection is plausible but the source does not define every acceptable outcome. A client implementation must never convert every timeout or connection error into success and must never retry automatically.

## 9. Other administrative operations

| Endpoint | HTTP method | Parameters | Authentication | Response | Purpose | Evidence | Verification | Risk |
|---|---|---|---|---|---|---|---|---|
| `/api/2.1/rest/system_factory_restore` | GET | None | Cookie session | XML | Read restore status/options | `Main/(index):2281`; `Main/aggregate.js:54`, restore handlers | Source-only | Read-only |
| `/api/2.1/rest/system_factory_restore` | POST | Restore selection | Cookie session | XML | Start factory restore | `Main/(index):2265` | Form-only | **Destructive** |
| `/api/2.1/rest/system_log` | POST | None shown | Cookie session | XML `system_log.logfilename` | Generate support archive | `Main/aggregate.js:54`, `requestAutomatedSupport()` | Source-only | Stateful; may collect sensitive data |
| `/api/2.1/rest/system_log?attach_file=true` | GET | `attach_file=true` | Cookie session | Downloaded file | Download support archive | `Main/aggregate.js:54`, `createAndSaveSystemLog()` | Source-only | Read-only retrieval of highly sensitive data |
| `/api/2.1/rest/alerts` | DELETE | Dismiss-all form; no additional captured parameter | Cookie session | XML | Dismiss all alerts | `Main/(index):831`; `Main/aggregate.js:54`, alerts form initialization | Form-only | Modifies state; never call from monitoring |
| `/api/2.1/rest/media_server_configuration` | GET, PUT | Media-server settings on PUT | Cookie session | XML | Read/change media server | `Main/(index):2159`; `Main/aggregate.js:54`, media handlers | Source-only | GET read-only; PUT modifies state |
| `/api/2.1/rest/media_server_connected_list` | GET, PUT | List/control values on PUT | Cookie session | XML | Connected media clients | `Main/(index):2481`; `Main/aggregate.js:54` | Source-only | GET read-only; PUT modifies state |
| `/api/2.1/rest/media_server_database` | GET, PUT | PUT actions include rescan/rebuild | Cookie session | XML | Inspect or rebuild media index | `Main/aggregate.js:54`, media database handlers | Source-only | PUT potentially expensive/stateful |
| `/api/2.1/rest/mediacrawler_status` | GET | None | Cookie session | XML | Media indexing status | `Main/aggregate.js:54`, dashboard handler | Source-only | Read-only |
| `/api/2.1/rest/itunes_configuration` | GET, PUT | iTunes settings on PUT | Cookie session | XML | Read/change iTunes server | `Main/(index):2208`; `Main/aggregate.js:54` | Source-only | GET read-only; PUT modifies state |
| `/api/2.1/rest/itunes_scan` | PUT | `scan=now` | Cookie session | XML | Start iTunes scan | `Main/aggregate.js:54`, scan handler | Source-only | Stateful/resource-intensive |
| `/api/2.1/rest/time_machine` | GET, PUT | PUT: `backup_enabled`, `backup_share`, `backup_size_limit` | Cookie session | XML | Read/change Time Machine backup configuration | `Main/(index):2043-2050,2833`; `Main/aggregate.js:54`, initialized/refreshed Time Machine forms | Source-only | GET read-only; PUT modifies state |
| `/api/2.1/rest/safepoint_getlist?option=inprogress` | GET | `option=inprogress` | Cookie session | XML | List safepoints/in-progress work | `Main/(index):1169`; `Main/aggregate.js:54`, safepoint handlers | Source-only | Read-only |
| `/api/2.1/rest/safepoint_getstatus` | GET | Safepoint context | Cookie session | XML | Read backup/safepoint status | `Main/(index):461,1159` | Source-only | Read-only |
| `/api/2.1/rest/safepoint_getschedule` | GET, PUT | Safepoint context/schedule | Cookie session | XML | Read schedule (UI also contains an unusual PUT form) | `Main/(index):325,1300`; `Main/aggregate.js:54` | Source-only | GET read-only; PUT may modify state |
| `/api/2.1/rest/safepoint_discover` | GET | Discovery context | Cookie session | XML | Discover safepoint targets | `Main/aggregate.js:54`, discovery handler | Source-only | Read-only/network discovery |
| `/api/2.1/rest/nas_discover` | GET | Discovery context | Cookie session | XML | Discover destination NAS devices | `Main/(index):1357,1360`; `Main/aggregate.js:54` | Source-only | Read-only/network discovery |
| `/api/2.1/rest/share_discover` | GET | Destination context | Cookie session | XML | Discover destination shares | `Main/(index):1432`; `Main/aggregate.js:54` | Source-only | Read-only; share names sensitive |
| `/api/2.1/rest/nas_authenticate` | POST | Destination NAS credentials | Cookie session | XML | Authenticate to backup target | `Main/(index):1401`; `Main/aggregate.js:54` | Source-only | Credential-bearing/stateful |
| `/api/2.1/rest/share_authenticate` | POST | Destination share credentials | Cookie session | XML | Authenticate to backup share | `Main/(index):1439`; `Main/aggregate.js:54` | Source-only | Credential-bearing/stateful |
| `/api/2.1/rest/safepoint_create` | POST | Safepoint configuration | Cookie session | XML | Create safepoint | `Main/(index):1487,1497` | Form-only | Stateful/resource-intensive |
| `/api/2.1/rest/safepoint_update` | PUT | Safepoint context | Cookie session | XML | Run/update safepoint | `Main/(index):1154` | Form-only | Stateful/resource-intensive |
| `/api/2.1/rest/safepoint_setinfo` | PUT | Safepoint metadata | Cookie session | XML | Change safepoint settings | `Main/(index):1221,1232` | Form-only | Modifies state |
| `/api/2.1/rest/safepoint_setschedule` | PUT | Schedule fields | Cookie session | XML | Change schedule | `Main/(index):1527` | Form-only | Modifies state |
| `/api/2.1/rest/safepoint_restore` | POST | Restore selection | Cookie session | XML | Restore data | `Main/(index):1732` | Form-only | **Destructive/high impact** |
| `/api/2.1/rest/safepoint_destroy` | DELETE | Safepoint context | Cookie session | XML | Delete safepoint | `Main/(index):1175` | Form-only | Destructive |
| `/api/2.1/rest/safepoint_deleteschedule` | DELETE | Schedule context | Cookie session | XML | Delete schedule | `Main/(index):321` | Form-only | Modifies state |
| `/settings/get_raid_details` | POST outside REST base | Current RAID/UI context | Cookie session | JSON | Read internal-drive details | `Main/aggregate.js:54`, `doSelectRaidDrive()` | Source-only | Read-only, model-specific, identifier-sensitive |
| `/settings/start_raid_conversion` | POST outside REST base | Serialized RAID edit form | Cookie session | JSON | Start RAID conversion | `Main/aggregate.js:54`, `doRAIDConversion()` | Source-only | Potentially **destructive** |
| `/settings/get_raidconversion_status` | POST outside REST base | None shown | Cookie session | JSON | Poll RAID conversion | `Main/aggregate.js:54`, `processRaidConversion()` | Source-only | Administrative/status polling |
| `/settings/update_raid_status` | POST outside REST base | None shown | Cookie session | JSON | Refresh/update RAID status | `Main/aggregate.js:54`, `autoUpdateRAIDStatus()` | Source-only | Ambiguous/stateful; avoid |
| `/settings/raid_status` | POST outside REST base | None shown | Cookie session | JSON | Check whether RAID conversion is active | `Main/aggregate.js:54`, `checkForRAIDConversion()` | Source-only | Administrative/status check |

### Auxiliary non-REST calls and placeholders

These calls are part of the UI but are not `/api/2.1/rest/` endpoints:

- `POST lib/privacy.php` reads a small XML `status` value (`Main/aggregate.js:54`, `get_new_privacy()`).
- `POST lib/cloud_notification.php` reads notification status and, with `agree=true`, records acknowledgement (`Main/aggregate.js:54`, `get_cloud_notification()` and `alert_cloud_notification_msg()`). The acknowledgement helper retries recursively on error, behavior that must not be copied into an integration.
- The safepoint share-selection form initially declares `POST /itdepends` and contains destination address, username, and password fields (`Main/(index):1472-1477`). This is explicitly a placeholder rather than evidence of a real endpoint; runtime code supplies the actual workflow. It must not be called or added to a client.
- The external support form posts to `http://websupport.wdc.com/rn/nassupport.asp`; this is not a NAS API and may transmit device/support metadata. It is out of scope.
- Literal values `TBD` and `&cid=` found in the bundle are placeholders/fragments, not API paths.

### Alerts discovered with incomplete route evidence

The bundle initializes `alert_level_form`, `alert_events_form`, and `alerts_form` and consumes these response structures:

- `alert_configuration.min_level_rss`
- `alert_events.alerts.alert[]` with `code` and `severity`
- RSS-like `rss.channel.item[]` with `title`, `description`, `pubDate`, and `link`

The corresponding form markup/actions were not present in the captured `Main/(index)` file. Only `DELETE /api/2.1/rest/alerts` is directly evidenced by the dismiss-all form at `Main/(index):831`. Therefore GET endpoint names must not be invented from element IDs. The missing page fragment or an authorized browser-network capture is needed before alerts can be implemented.

## Polling and error handling

- General polling interval: 60 seconds (`pollingTimeout=60000`).
- Update counters: 15 seconds.
- Dashboard media updates: 60 seconds.
- Initial system-state readiness check: 5 seconds, only during initialization.
- SMART-test progress: 5 seconds while a test is active.
- Post-reboot availability ping: begins after 15 seconds and repeats every 3 seconds. This is a special reboot workflow and is unsuitable for normal Home Assistant polling.
- UI timeout constants include 300 seconds (general long action), 600 seconds (very long), 15 seconds (fast), and 60 seconds (network/polling). These values describe the browser UI, not safe defaults for a Home Assistant coordinator.
- XML error payloads are inspected by `restForm.parseDataForErrors`, generally looking recursively for `error_id`. Direct `$.ajaxAPI` calls do not automatically apply that check.
- HTTP 401 triggers queued reauthentication. Other HTTP errors are delegated to the caller. HTTP 403 is not special-cased.

## Source files inspected and limitations

| File | Result |
|---|---|
| `Main/(index)` | Main page/form markup; REST form actions, methods and field names |
| `Main/aggregate.js` | Complete minified UI/application bundle; principal API evidence |
| `login/aggregate.js` | Byte-identical copy of the main aggregate bundle |
| `Main/dictionary.php`, `login/dictionary.php` | Localized UI/error strings; no additional reliable API contract |
| `Main/date-de_DE.js`, `login/date-de_DE.js` | Date localization only; identical copies |
| `Main/jquery.timezone.js`, `login/jquery.timezone.js` | Time-zone UI library; identical copies |
| `Main/jquery.ui.datepicker-de_DE.min.js`, `login/jquery.ui.datepicker-de_DE.min.js` | Date-picker localization only; identical copies |
| `login/_anonymous code_` | Browser/security-extension injection, not WD application source |
| `login/injection-topics.js` | Browser-extension injection, not WD application source |

The page references `js/html5.js` and comments out `js/errors.php`, but neither file is supplied. Several dynamically loaded/page-fragment forms, especially the alert forms, are absent. Minification prevents useful per-statement line numbers; function names plus physical line 54 are the most precise source locator available. Response schemas are inferred only from fields actually consumed by the code, so unconsumed fields may exist.
