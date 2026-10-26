# WD My Cloud power-action evidence

Date: 2026-10-26
Scope: offline source review and simulated tests; no device action was executed

## WD My Cloud V4

The supplied original web-interface bundle is identified as model `sq`,
firmware `04.06.00-111`. In `Main/aggregate.js`, `doReboot()` and
`doShutdown()` both call the REST 2.1 resource `shutdown` with HTTP `PUT`:

- restart: `PUT /api/2.1/rest/shutdown?state=reboot`
- shutdown: `PUT /api/2.1/rest/shutdown?state=halt`

WD's `$.ajaxAPI` helper appends PUT data to the query string and also passes the
same data to jQuery as request data. The client therefore sends `state` in both
locations. The browser's existing same-origin cookie session authenticates the
request. The source shows no CSRF value, server-issued confirmation token, or
dangerous-operation preflight; confirmation is only a local UI dialog.

The UI uses its 300-second AJAX timeout. On a reboot error it opens the reboot
dialog and starts readiness polling; shutdown errors are displayed. This does
not prove that a dropped connection means success. The Python client therefore
uses its bounded 15-second power timeout, reports timeout/disconnect as an
ambiguous failure, and never retries or switches method.

Home Assistant exposes buttons only when API version 4 reports both
`model_number=sq` and firmware `04.06.00-111`. They retain the existing
`{serial}_restart` and `{serial}_shutdown` unique IDs, reuse the coordinator's
authenticated session, and share its operation lock with polling. Later V4
firmware or another model remains disabled until separately evidenced.

Shutdown may leave the appliance unable to wake through Wake-on-LAN depending
on its power state, firmware, network configuration, and hardware. Neither the
source nor the offline tests prove remote wake behavior. Physical access and a
current backup remain prerequisites for any separately approved hardware test.

## WD My Cloud OS 5

The WD Community post dated 6 November 2020 documents a curl workflow using
`POST /nas/v1/auth`, Base64 password encoding, a persisted cookie jar,
`GET /cgi-bin/system_mgr.cgi?cmd=cgi_shutdown`, and
`DELETE /nas/v1/auth`.

The retained WD OS 5 firmware JavaScript is stronger device-specific evidence
for this project. It first sends `POST /cgi-bin/hd_config.cgi` with
`cmd=cgi_Detect_Dangerous`, then sends `POST /cgi-bin/system_mgr.cgi` with
`cmd=cgi_shutdown` or `cmd=cgi_restart`. The existing OS 5 implementation is
unchanged. It has no GET/POST fallback and never retries a power command.

The community OS 5 request is not evidence for V4. V4 uses a different REST
2.1 API, ordinary V4 login credentials, and a different cookie session.

Sources:

- `docs/research/v4/Main/aggregate.js`: `doReboot()`, `doShutdown()`,
  `$.ajaxAPI`
- `docs/research/v4/Main/(index)`: restart confirmation markup
- `WD-Mirror/resources/function.js`: OS 5 power preflight and POST actions
- <https://community.wd.com/t/my-cloud-os-5-shutdown-via-ssh-rest/257875>
