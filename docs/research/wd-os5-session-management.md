# WD OS 5 session management review

## Combined test.8: ambiguous administrator-session recovery

Hardware testing of combined test.7 showed that a Dashboard login can make the
next Home Assistant OS 5 login unavailable. The captured firmware JavaScript
contains no read-only preflight for administrator-session ownership, and no
sanitized firmware response has established a distinct status or field for
"account in use" versus "wrong password". String matching or invented status
semantics would therefore be unreliable.

The optional per-poll release mode now treats an ambiguous OS 5 authentication
rejection as a transient update failure. It performs no immediate login retry
and starts no reauthentication flow; the next scheduled coordinator update
makes one new attempt. The default long-lived-session mode retains its existing
authentication behavior.

Combined test.8 was subsequently exercised on the Mirror Gen 2. After the
Dashboard released its administrator session, Home Assistant recovered on a
later scheduled poll without reauthentication. Config-entry disable also
released the OS 5 administrator session successfully on this hardware.

Cleanup also distinguishes an allocated HTTP client from proven server-session
ownership. `DELETE /nas/v1/auth` is allowed only when the current login response
issued a non-empty `PHPSESSID`, or when the same HTTP client subsequently parsed
a protected OS 5 system/device response. Otherwise cleanup closes only local
resources. This prevents a failed or ambiguous login from deliberately logging
out a browser session. It cannot prevent the firmware itself from invalidating
the browser as a side effect of `POST /nas/v1/auth`; no non-disruptive detection
endpoint is known. Users needing uninterrupted Dashboard access should keep the
optional per-poll mode disabled until that firmware behavior is better proven.

## PHPSESSID regression addendum

The historically working client `4e6389f753ef` copied cookies from raw
`Set-Cookie` headers into its cookie jar and did not reject an OS 5 HTTP 200
response merely because `response.cookies` omitted `PHPSESSID`. The later
client at `d6740d37ea67` required `PHPSESSID` specifically in
`response.cookies` and classified its absence as `InvalidLoginError`. That
changed a server-accepted login with a usable raw header into an apparent bad
password and could also prevent the authenticated session from reaching normal
cleanup.

The local correction resolves the OS 5 cookie in this order: the current
response's parsed cookies, all independently parsed `Set-Cookie` headers, then
the current session cookie jar filtered for the exact login URL. A jar value
that was already present before the login is not accepted as proof of the new
authentication. The selected value is stored in both `client.phpsessid` and the
same client's host-scoped cookie jar. IP hosts continue to use an isolated
`aiohttp.CookieJar(unsafe=True)`.

HTTP 401/403 remains an explicit `InvalidLoginError`. HTTP 200 without a usable
current-session cookie is now `SessionEstablishmentError`, because it does not
prove a credential rejection. If the response supplied current cookies but no
usable `PHPSESSID`, cleanup makes at most one `DELETE /nas/v1/auth` attempt with
that same HTTP session; a cleanup failure remains attached to the establishment
error. No login retry or API-version fallback is initiated.

Offline verification after the correction: 74 client tests and 102 Home
Assistant integration tests passed. This includes raw-header-only,
`response.cookies`-only and cookie-jar paths, IP hosts, protected requests,
same-cookie logout, explicit 401/403, ambiguous cleanup failure, and OS 2/V4
regressions. No live NAS request or power action was performed.

### Safe OS 5 login diagnostics

The subsequent test.7 build adds one DEBUG record per OS 5 login attempt. In
Home Assistant it is enabled for
`custom_components.mycloud._vendor.wdnas_client`. The record contains only the
API version, final HTTP status, redirect count, cookie names by source, whether
a current `PHPSESSID` was found, the JSON value type and syntactically safe
top-level field names, the result classification, and cleanup attempt/result
booleans. It never records a cookie value, credential, response body, host,
query string, authorization header, or complete URL.

An HTTP 200 object containing an `error` field but no usable current session is
classified as `http_200_error_field_without_session`; the field value is not
interpreted or logged. This preserves `SessionEstablishmentError` until a real,
redacted device observation establishes the firmware's error contract. A
same-host redirect can supply the session cookie, while cookies from a foreign
redirect host are ignored. Diagnostics do not add login retries, OS 2 fallback,
or extra NAS requests.

## V4 test.5 addendum: attribution of the session conflict

The retained automatic-detection build and the original `0314bdc` source allow
the request count to be reconstructed without contacting a NAS:

- Original setup: the config flow stored the explicit version without logging
  in; the sensor platform then created one client and made one login.
- Successful automatic OS 5 detection: probe client A logged in and fetched
  identity, then cleanup ran; coordinator client B subsequently logged in. This
  was two sequential authentication requests, not two intentionally parallel
  sessions.
- If OS 5 conclusively returned 404/405, the OS 2 probe was attempted next. The
  failed OS 5 request was not an authenticated session. For ambiguous results,
  however, the manual fallback could create another validation client before
  the coordinator client, so as many as three login attempts were possible if
  the NAS had accepted an earlier request despite the ambiguous client result.
- Reauthentication independently used the same two-session pattern: validation
  login, logout, then a fresh coordinator login after the scheduled reload.

It is therefore not proven that version detection alone left the observed NAS
session behind. Both detection and the former reauthentication path depended on
successful server logout before the next login. It is verified that aiohttp's
default cookie jar rejects cookies set by IP-address hosts; before the unsafe,
per-client cookie jar was added, an OS 5 logout could therefore lack the login
cookie. Which particular request held the real device session cannot be proven
without a sanitized trace from that incident. The continued failure seen after
test.4 is consistent with either a pre-existing unexpired NAS session or a NAS
that had not made the just-logged-out administrator slot reusable before the
immediate coordinator login.

Test.5 removes both avoidable multipliers. Initial setup uses an explicit
version and exactly one coordinator login. A successful reauthentication login
is no longer logged out and recreated: ownership of that same authenticated
client is transferred to the scheduled coordinator reload. The coordinator is
marked authenticated and does not call `__aenter__()` again. An unclaimed
handoff expires after 60 seconds and is closed; a stale handoff whose cleanup is
ambiguous blocks another authentication attempt.

Restart and shutdown also reuse this coordinator-owned session. An integration
operation lock prevents polling and a power action from sharing the HTTP session
concurrently, while a separate active flag rejects a second power action instead
of queuing or retrying it.

Date: 2026-10-25
Target: WD My Cloud Mirror Gen 2, firmware 5.33.102
Scope: offline source analysis and simulated tests only

## Result

The most likely cause of the observed administrator-session conflict was a
missing server-side logout. `wdnas_client.client.__aexit__()` previously called
only `aiohttp.ClientSession.close()`. That releases sockets and other local
resources, but it does not tell the NAS to invalidate its authenticated PHP
session.

The automatic OS 5 probe authenticated successfully, read device identity, and
then performed only that local close. Home Assistant subsequently created the
coordinator, which opened a second administrator login. On a device that permits
only one administrator web session, the first session could therefore block the
second login and the official WD web UI until the server expired it. This is a
source-supported explanation and matches the reported timing, but the exact NAS
timeout and its raw "account already in use" response have not been captured.

## WD source findings

### Login and session data

The client uses these established login protocols:

- OS 5: `POST /nas/v1/auth` with JSON containing the lowercase username and a
  Base64-encoded password. A successful response must provide `PHPSESSID`.
- OS 2: `POST /cgi-bin/login_mgr.cgi` with the existing `wd_login` form data. A
  successful response must provide both `PHPSESSID` and `WD-CSRF-TOKEN`.

The collected JavaScript does not contain the OS 5 login form implementation,
so no additional login fields are inferred from it.

### Proven OS 5 logout

`WD-Mirror/resources/eula.js`, lines 589-621, defines `eula_logout()`. Its AJAX
request is:

```text
DELETE /nas/v1/auth
Content-Type: application/json
```

It sends no request body and no username/session parameter. The request relies
on the current browser session cookies. Although line 597 reads a `username`
cookie into `login_name`, that variable is not added to the request. The client
therefore also sends no invented parameter and can invalidate only the session
represented by its own cookie jar.

`WD-Mirror/resources/web_idle_timeout.js`, lines 48-64, uses
`PUT /nas/v1/auth` as the authenticated keep-alive and calls `do_logout()` after
HTTP 403. `init.js`, lines 159-165, also routes the visible Logout action to
`do_logout()`. A definition of the general `do_logout()` helper is not present
in the collected resource set; the independently present `eula_logout()` still
provides direct evidence for the HTTP method and endpoint.

No corresponding OS 2 server-side logout endpoint was found. It has not been
guessed or synthesized.

### Additional WD Community evidence

A WD Community post dated 6 November 2020 documents the same OS 5 session
sequence from an independent user perspective: `POST /nas/v1/auth` with a
Base64-encoded password, persistence and reuse of the response cookies with
curl's `-c`/`-b` options, and `DELETE /nas/v1/auth` for logout. This is useful
corroborating evidence that an HTTP 200 login is not sufficient by itself: the
issued session cookies must be retained and sent on protected requests and on
logout. The post is community evidence rather than an official API contract:
<https://community.wd.com/t/my-cloud-os-5-shutdown-via-ssh-rest/257875>.

The same post invokes shutdown as an authenticated
`GET /cgi-bin/system_mgr.cgi?cmd=cgi_shutdown`. In contrast, the firmware
JavaScript retained in `WD-Mirror/resources/function.js` (lines 1646-1679)
performs the dangerous-operation preflight first and then sends
`POST /cgi-bin/system_mgr.cgi` with form data `cmd=cgi_shutdown`. The client
keeps that source-derived POST implementation and its preflight. It does not
add a GET fallback or retry with another method: either could issue a second
device action after an ambiguous response. No corresponding community restart
request is used or inferred.

## Client changes

The local client now separates three operations:

- `logout()` performs the proven OS 5 `DELETE /nas/v1/auth` using only the
  already-authenticated session. It never logs in. Explicit OS 2 logout raises
  `ValueError` because a server endpoint is not established.
- `close()` attempts OS 5 server logout and always closes the local aiohttp
  session. Logout HTTP errors, disconnects, and the 15-second timeout remain
  errors; they are not reported as successful logout.
- `__aexit__()` uses `close()`. If cleanup itself fails while another exception
  is already propagating, the original exception remains primary.

An `asyncio.Lock` and one-attempt state make concurrent/repeated cleanup safe:
only one DELETE is sent. A repeated call observes the stored failure instead of
pretending the failed logout succeeded. Credentials and local authentication
tokens are cleared when the local session is closed.

If `__aenter__()` fails, it now closes the newly created aiohttp session before
re-raising. A timeout after the NAS accepted a login but before any session
cookie reached the client cannot be conclusively invalidated; there is no safe
session identifier available in that edge case.

OS 2 keeps its previous authentication protocol. `close()` remains a local-only
cleanup for OS 2.

## Historical config flow and API detection (superseded)

The following section records the earlier automatic-detection implementation
for comparison. It is not the behavior packaged in V4 test.5.

Temporary validation and probe clients now call `client.close()`, not the old
context exit that only closed the transport. A successful OS 5 probe therefore
logs out before a config entry is created and before the coordinator starts.

Detection remains bounded and sequential:

1. Try OS 5 once.
2. Try OS 2 only if the OS 5 login endpoint explicitly returns HTTP 404 or 405.
3. Do not try another protocol for authentication failures, timeouts, arbitrary
   HTTP failures, or ambiguous responses.
4. Offer the explicit OS 2/OS 5 fallback only for an ambiguous result.

Home Assistant does perform another login when it sets up the newly created
entry. Reusing a live config-flow client across the flow/runtime ownership
boundary would require global hand-off state and creates leak and restart risks.
The safer implementation performs one temporary login, a confirmed logout, and
then one coordinator login. These logins are sequential, not simultaneous.

If successful flow cleanup fails, entry creation is stopped with a connection
error, because starting another login with uncertain server state could recreate
the conflict. If validation already failed, cleanup is still attempted and a
secondary cleanup failure is logged only by exception type so it cannot mask the
original error or expose response data. Merely opening or abandoning the initial
form creates no client and no NAS session. Password selectors remain masked and
Probatio-serializable.

Existing config entries retain their stored API version. Reauthentication uses
that stored version rather than probing both protocols.

## Coordinator lifecycle

The coordinator owns the long-lived client used for setup and all updates. It
does not log in on every poll. Session open/close operations are serialized by a
lock, so concurrent refresh requests cannot start parallel logins and concurrent
cleanup cannot issue duplicate logout calls.

Reload, unload, and the registered Home Assistant shutdown cleanup call the
client's server-aware `close()`. Setup failure also cleans up. If logout fails
during an expired-session recovery, the coordinator closes local resources but
does not immediately attempt a new login with uncertain NAS session state. This
avoids stacking another administrator session; Home Assistant receives an
`UpdateFailed` result instead.

No background task was added. Existing DataUpdateCoordinator timers are stopped
by `async_shutdown()` before session cleanup.

## Account-in-use classification

No raw HTTP status, response body, header, or stable error code from the NAS has
been captured for the "account already in use" case. Consequently no
`AccountInUseError` and no speculative string matching were added. Such a
response can currently still be classified as a generic authentication or
connection failure according to its HTTP-level behavior. Adding a dedicated
exception requires a redacted raw response from a controlled future test.

## Automated tests

Client coverage includes successful login/logout, local close, repeated and
concurrent close, login failure cleanup, logout timeout, disconnect, invalid
session, server HTTP failure, preservation of an original exception, no login
for logout, and OS 2 compatibility.

Home Assistant coverage includes automatic OS 5 detection, the bounded OS 2
fallback, ambiguous manual fallback, abandoned and repeatedly submitted forms,
reauthentication, setup failure cleanup, reload/unload, coordinator shutdown,
concurrent refresh/login/cleanup calls, no per-update login, and prevention of
automatic relogin after failed logout.

Final results: 44 client tests and 86 Home Assistant tests passed. All tests use
mocked responses; no login, logout, power action, or other request was sent to a
NAS.

## Packaging status

The reviewed client fix was subsequently committed and pushed as
`d6740d37ea67405ca76ee27268536a03362bc16d`. The private HA manifest is pinned
to that complete immutable hash. The private ZIP is built only after the Home
Assistant suite has passed with the same Git dependency; it is not a public
release. The resulting `ha-mycloud-v1.3.0-beta.1-haos-test.zip` has SHA-256
`679F6D5DBA45E3123FDCB6DF7A884FCDB17AE86974711B203F1DC76D5D554F63`.

## Recommended safe live test

1. Verify the ZIP checksum above before installation.
2. Ensure a current external HAOS backup exists and close every WD web UI tab.
3. Wait for any old NAS administrator session to expire before starting.
4. Install the private integration and perform one setup attempt only.
5. Confirm setup and normal polling, then unload the integration once.
6. Immediately verify that the official WD web UI can log in again. If it
   cannot, stop testing and retain a redacted HTTP capture of the failed login
   response; do not repeat automated attempts.

Do not test reboot or shutdown as part of session validation.
