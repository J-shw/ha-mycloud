import asyncio
import base64
import http.cookies
import json
import logging
import re
from datetime import timedelta
from xml.etree import ElementTree

import aiohttp
from yarl import URL

from .const import (
    ENDPOINTS,
    POWER_ACTION_TIMEOUT,
    SCHEME,
    SESSION_ACTION_TIMEOUT,
    V2_RAW_LOGIN_STRING,
    V4_ENDPOINTS,
)
from .exceptions import (
    InvalidLoginError,
    PowerActionBlockedError,
    RequestFailedError,
    UptimeUnavailableError,
)
from .v4 import V4Adapter

_LOGGER = logging.getLogger(__name__)
POWER_GUARD_REVISION = "os5-power-open-session-v3"


def _optional_int(value):
    """Return an integer for numeric XML text, preserving other values."""
    if value is None:
        return None
    value = value.strip()
    try:
        return int(value)
    except ValueError:
        return value


def _optional_number(value):
    """Return an int/float for numeric XML text, preserving other values."""
    value = _optional_int(value)
    if not isinstance(value, str):
        return value
    try:
        return float(value)
    except ValueError:
        return value


def _xml_value(element):
    """Convert arbitrary XML into JSON-compatible data without dropping fields."""
    children = list(element)
    if not children:
        value = _optional_int(element.text)
    else:
        value = _xml_children(element)

    if element.attrib:
        if not isinstance(value, dict):
            value = {"value": value}
        value["attributes"] = dict(element.attrib)
    return value


def _xml_children(element, excluded=()):
    """Return selected XML children while preserving repeated tag names."""
    result = {}
    for child in element:
        if child.tag in excluded:
            continue
        value = _xml_value(child)
        if child.tag in result:
            if not isinstance(result[child.tag], list):
                result[child.tag] = [result[child.tag]]
            result[child.tag].append(value)
        else:
            result[child.tag] = value
    return result


class client:
    """
    A client class for interacting with the WD NAS device APIs (versions 2, 4 or 5).

    :host: The hostname or IP address of the device.
    :username: The username used for authentication (stored in lowercase).
    :password: The password used for authentication.
    :version: The API version being used (2, 4 or 5).
    :session: An aiohttp.ClientSession object for making async requests (initially None).
    :phpsessid: The PHP session ID token (initially None; set after login).
    :wd_csrf_token: The CSRF token required for v2 requests (initially None; set after login).
    """

    def __init__(self, username: str, password: str, host: str, version: int):
        if version not in [2, 4, 5]:
            raise ValueError("Unsupported/invalid version. Must be 2, 4 or 5.")

        self.host = host
        self.username = username.lower()
        self.password = password
        self.version = version
        self.session = None
        self.phpsessid = None
        self.wd_csrf_token = None
        self._authenticated = False
        self._server_session_owned = False
        self._lifecycle_lock = asyncio.Lock()
        self._logout_attempted = False
        self._logout_error = None
        self._v4 = V4Adapter(self)

    async def __aenter__(self):
        # Protected historical OS 5 path from client commit 4e6389f.  In
        # particular, keep aiohttp's default CookieJar and the historical
        # login/cookie handling. The lifecycle flags only make final cleanup
        # and the source-confirmed server-side logout idempotent.
        if self.version == 5:
            self._authenticated = False
            self._server_session_owned = False
            self._logout_attempted = False
            self._logout_error = None
            self.session = aiohttp.ClientSession()
            await self.login()
            self._authenticated = True
            return self

        async with self._lifecycle_lock:
            if (
                self.session is not None
                and not getattr(self.session, "closed", False)
                and self._authenticated
            ):
                return self

            if self.session is not None and not getattr(self.session, "closed", False):
                await self.session.close()

            # NAS devices are commonly configured by IP address. aiohttp's
            # default CookieJar rejects Set-Cookie headers from IP hosts, which
            # would make authenticated follow-up requests and logout lose the
            # session cookie. This jar remains isolated to this one NAS client.
            self.session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True)
            )
            self.phpsessid = None
            self.wd_csrf_token = None
            self._authenticated = False
            self._logout_attempted = False
            self._logout_error = None
            self._v4.reset()
            try:
                await self.login()
            except BaseException:
                try:
                    await self.session.close()
                except BaseException:  # noqa: BLE001, S110 - preserve login failure
                    pass
                self.phpsessid = None
                self.wd_csrf_token = None
                self._authenticated = False
                raise
            return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self.close()
        except Exception:
            if exc_type is None:
                raise

    async def logout(self) -> None:
        """End an authenticated V4 or OS 5 session without opening a new one."""
        if self.version not in (4, 5):
            raise ValueError("Server-side logout is only enabled for API versions 4/5")
        async with self._lifecycle_lock:
            await self._logout_unlocked()

    async def _logout_unlocked(self) -> None:
        """Perform at most one supported server-side logout attempt."""
        if self._logout_attempted:
            if self._logout_error is not None:
                raise self._logout_error
            return

        if self.session is None:
            return
        if getattr(self.session, "closed", False):
            return
        if not self._authenticated:
            return
        if self.version == 5 and not self._server_session_owned:
            return

        self._logout_attempted = True
        if self.version == 4:
            url = f"{SCHEME}{self.host}{V4_ENDPOINTS['logout']}"
            request = self.session.get
            headers = {"Accept": "application/xml", "Host": self.host}
        else:
            url = f"{SCHEME}{self.host}{ENDPOINTS[5]['login']}"
            request = self.session.delete
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Host": self.host,
            }
        try:
            async with asyncio.timeout(SESSION_ACTION_TIMEOUT):
                async with request(url, headers=headers) as response:
                    if not 200 <= response.status < 300:
                        raise RequestFailedError(response.status)
        except BaseException as error:
            self._logout_error = error
            raise
        else:
            self.phpsessid = None
            self.wd_csrf_token = None
            self._authenticated = False
            self._server_session_owned = False

    async def close(self) -> None:
        """End the NAS session when supported, then close local HTTP resources."""
        async with self._lifecycle_lock:
            logout_error = None
            try:
                if self.version in (4, 5):
                    await self._logout_unlocked()
            except BaseException as error:  # noqa: BLE001 - preserve cancellation
                logout_error = error
            finally:
                if self.session is not None and not getattr(
                    self.session, "closed", False
                ):
                    await self.session.close()
                self.phpsessid = None
                self.wd_csrf_token = None
                self._authenticated = False
                self._server_session_owned = False
                self._v4.reset()

            if logout_error is not None:
                raise logout_error

    async def login(self) -> None:
        """Login to device"""
        if self.version == 4:
            await self._v4.login()
            self._authenticated = True
            return

        if self.version == 5:
            await self._login_os5()
            return

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['login']}"
        enc_password = base64.b64encode(self.password.encode("utf-8")).decode("utf-8")

        data = V2_RAW_LOGIN_STRING.format(
            username=self.username, enc_password=enc_password
        )
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Host": self.host,
        }
        async with self.session.post(url, data=data, headers=headers) as response:
            pass

        if response.status == 200:
            cookies = response.cookies
            if "PHPSESSID" in cookies and "WD-CSRF-TOKEN" in cookies:
                self.phpsessid = cookies["PHPSESSID"].value
                self.wd_csrf_token = cookies["WD-CSRF-TOKEN"].value
            else:
                raise InvalidLoginError("Invalid Username/Password or missing cookies")
            self._authenticated = True
        else:
            raise RequestFailedError(response.status)

    async def _login_os5(self) -> None:
        """Authenticate using the protected historical OS 5 implementation."""
        url = f"{SCHEME}{self.host}{ENDPOINTS[5]['login']}"
        enc_password = base64.b64encode(self.password.encode("utf-8")).decode("utf-8")
        json_payload = {"username": self.username, "password": enc_password}
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Host": self.host,
        }
        async with self.session.post(
            url, json=json_payload, headers=headers
        ) as response:
            pass

        if response.status == 200:
            set_cookies = response.headers.getall("Set-Cookie", [])
            for cookie_str in set_cookies:
                cookie = http.cookies.SimpleCookie(cookie_str)
                for key, morsel in cookie.items():
                    self.session.cookie_jar.update_cookies({key: morsel.value})
                    if key == "PHPSESSID" and morsel.value:
                        self._server_session_owned = True

            cookies = response.cookies
            if "PHPSESSID" in cookies:
                self.phpsessid = cookies["PHPSESSID"].value
                if self.phpsessid:
                    self._server_session_owned = True
        else:
            raise RequestFailedError(response.status)

    def _confirm_os5_session_ownership(self) -> None:
        """Record ownership after a protected OS 5 response was parsed."""
        if self.version == 5 and self._authenticated:
            self._server_session_owned = True

    async def _power_action(self, command: str) -> None:
        """Run a JavaScript-proven OS 5 power command after its safety check."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        safety_url = f"{SCHEME}{self.host}{ENDPOINTS[5]['power_safety']}"
        action_url = f"{SCHEME}{self.host}{ENDPOINTS[5]['power_action']}"
        session_present = self.session is not None
        session_closed = (
            getattr(self.session, "closed", False) if session_present else False
        )
        if not session_present or session_closed:
            _LOGGER.warning(
                "OS 5 power diagnostic: api_version=%s session_present=%s "
                "session_closed=%s phpsessid_present=%s "
                "preflight_started=False phase=session_guard "
                "exception_type=InvalidLoginError",
                self.version,
                session_present,
                session_closed,
                bool(self.phpsessid),
            )
            raise InvalidLoginError("An authenticated NAS session is required")

        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        _LOGGER.debug(
            "OS 5 power diagnostic: api_version=%s session_present=True "
            "session_closed=False phpsessid_present=%s "
            "preflight_started=True phase=preflight",
            self.version,
            bool(self.phpsessid),
        )

        async with asyncio.timeout(POWER_ACTION_TIMEOUT):
            try:
                async with self.session.post(
                    safety_url, data="cmd=cgi_Detect_Dangerous", headers=headers
                ) as response:
                    if response.status != 200:
                        raise RequestFailedError(response.status)
                    root = ElementTree.fromstring(await response.text())
                    result = _optional_int(root.findtext(".//res"))
                    if not isinstance(result, int):
                        raise TypeError(
                            "Power safety response did not contain a numeric res"
                        )
                    if result != 0:
                        raise PowerActionBlockedError(result)
            except Exception as error:
                _LOGGER.warning(
                    "OS 5 power diagnostic: api_version=%s preflight_started=True "
                    "phase=preflight exception_type=%s",
                    self.version,
                    type(error).__name__,
                )
                raise

            try:
                async with self.session.post(
                    action_url, data=f"cmd={command}", headers=headers
                ) as response:
                    if response.status != 200:
                        raise RequestFailedError(response.status)
                    await response.text()
            except aiohttp.ServerDisconnectedError:
                # The confirmed command may tear down HTTP before a response body.
                return
            except Exception as error:
                _LOGGER.warning(
                    "OS 5 power diagnostic: api_version=%s preflight_started=True "
                    "phase=action exception_type=%s",
                    self.version,
                    type(error).__name__,
                )
                raise

    def _has_current_os5_session_cookie(self, target_url: str) -> bool:
        """Return whether this client's current HTTP session can send PHPSESSID."""
        if self.session is None or getattr(self.session, "closed", False):
            return False
        cookies = self.session.cookie_jar.filter_cookies(URL(target_url))
        session_cookie = cookies.get("PHPSESSID")
        return bool(session_cookie is not None and session_cookie.value)

    async def reboot(self) -> None:
        """Restart a supported NAS using its authenticated HTTP session."""
        if self.version == 4:
            await self._v4.power_action("reboot")
            return
        await self._power_action("cgi_restart")

    async def shutdown(self) -> None:
        """Shut down a supported NAS using its authenticated HTTP session."""
        if self.version == 4:
            await self._v4.power_action("halt")
            return
        await self._power_action("cgi_shutdown")

    async def system_information(self) -> dict:
        """Return raw V4 system-information fields from the REST 2.1 API."""
        if self.version != 4:
            raise ValueError("Unsupported/invalid version. Must be 4.")
        return await self._v4.system_information()

    async def firmware_info(self) -> dict:
        """Return the current V4 firmware package."""
        if self.version != 4:
            raise ValueError("Unsupported/invalid version. Must be 4.")
        return await self._v4.firmware_info()

    async def system_state(self) -> dict:
        """Return qualitative V4 health and readiness states."""
        if self.version != 4:
            raise ValueError("Unsupported/invalid version. Must be 4.")
        return await self._v4.system_state()

    async def storage_usage(self) -> dict:
        """Return V4 storage byte counters and calculated utilization."""
        if self.version != 4:
            raise ValueError("Unsupported/invalid version. Must be 4.")
        return await self._v4.storage_usage()

    async def system_info(self) -> dict:
        """
        Fetches system information from the device.

        This includes data about physical **disks**, logical **volumes**, and overall
        storage **size** (total, used, and unused).
        """
        if self.version == 4:
            return await self._v4.system_info()

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['system_info']}"
        if self.version == 2:
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
            }
        elif self.version == 5:
            headers = {"Host": self.host}
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                device_info = ElementTree.fromstring(content)
                device_info_json = {"disks": [], "volumes": [], "size": {}}
                for disk in device_info.iter("disk"):
                    device_info_json["disks"].append(
                        {
                            "id": disk.attrib["id"],
                            "name": disk.findtext("name"),
                            "connected": bool(int(disk.findtext("connected"))),
                            "vendor": disk.findtext("vendor"),
                            "model": disk.findtext("model"),
                            "rev": disk.findtext("rev"),
                            "sn": disk.findtext("sn"),
                            "size": int(disk.findtext("size")),
                            "failed": bool(int(disk.findtext("failed"))),
                            "healthy": bool(int(disk.findtext("healthy"))),
                            "removable": bool(int(disk.findtext("removable"))),
                            "over_temp": bool(int(disk.findtext("over_temp"))),
                            "temp": int(disk.findtext("temp")),
                            "sleep": bool(int(disk.findtext("sleep"))),
                        }
                    )
                for disk in device_info.iter("vol"):
                    device_info_json["volumes"].append(
                        {
                            "id": disk.attrib["id"],
                            "name": disk.findtext("name"),
                            "label": disk.findtext("label"),
                            "encrypted": bool(int(disk.findtext("encrypted"))),
                            "unlocked": bool(int(disk.findtext("unlocked"))),
                            "mounted": bool(int(disk.findtext("mounted"))),
                            "size": int(disk.findtext("size")),
                        }
                    )
                device_info_json["size"]["total"] = int(
                    device_info.find(".//total_size").text
                )
                device_info_json["size"]["used"] = int(
                    device_info.find(".//total_used_size").text
                )
                device_info_json["size"]["unused"] = int(
                    device_info.find(".//total_unused_size").text
                )
                self._confirm_os5_session_ownership()
                return device_info_json
            else:
                raise RequestFailedError(response.status)

    async def share_names(self) -> list:
        """Fetches a list of all share names configured on the device."""
        if self.version == 4:
            raise ValueError("Share enumeration is not established for version 4.")
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['share_names']}"

        if self.version == 2:
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
            }
        elif self.version == 5:
            headers = {"Host": self.host}
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.post(url, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                json_content = json.loads(content)
                if json_content["success"]:
                    return json_content["item"]
                else:
                    raise RequestFailedError(response.status)
            else:
                raise RequestFailedError(response.status)

    async def system_status(self) -> dict:
        """
        Fetches system status from the device.

        This includes data about **CPU** and **memory** usage.
        """
        if self.version == 4:
            return await self._v4.system_state()

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['system_status']}"

        if self.version == 2:
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            data = "cmd=resource"
        elif self.version == 5:
            headers = {
                "Host": self.host,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
            data = "cmd=resource"
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                device_status = ElementTree.fromstring(content)
                json_device_status = {"memory": {}, "cpu": None}
                json_device_status["cpu"] = int(
                    device_status.find(".//cpu").text.strip("%")
                )
                json_device_status["memory"]["total"] = int(
                    device_status.find(".//mem_total").text
                )
                json_device_status["memory"]["unused"] = int(
                    device_status.find(".//mem_free").text
                )
                json_device_status["memory"]["simple"] = device_status.find(
                    ".//mem2_total"
                ).text

                for xml_name, output_name in (
                    ("buffers", "buffers"),
                    ("cached", "cached"),
                ):
                    value = device_status.findtext(f".//{xml_name}")
                    if value is not None:
                        json_device_status["memory"][output_name] = _optional_int(value)

                for xml_name, output_name in (
                    ("bonding", "bonding"),
                    ("now_time", "now_time"),
                ):
                    value = device_status.findtext(f".//{xml_name}")
                    if value is not None:
                        json_device_status[output_name] = _optional_int(value)

                network_fields = {
                    "lan_r_speed": ("lan", "received_raw"),
                    "lan_t_speed": ("lan", "transmitted_raw"),
                    "lan2_r_speed": ("lan2", "received_raw"),
                    "lan2_t_speed": ("lan2", "transmitted_raw"),
                }
                network = {}
                for xml_name, (interface, output_name) in network_fields.items():
                    value = device_status.findtext(f".//{xml_name}")
                    if value is not None:
                        network.setdefault(interface, {})[output_name] = _optional_int(
                            value
                        )
                if network:
                    json_device_status["network"] = network

                processes = device_status.findall(".//process_main")
                if processes:
                    json_device_status["process_count"] = len(processes)

                known_fields = {
                    "bonding",
                    "buffers",
                    "cached",
                    "cpu",
                    "lan_r_speed",
                    "lan_t_speed",
                    "lan2_r_speed",
                    "lan2_t_speed",
                    "mem_total",
                    "mem2_total",
                    "mem_free",
                    "now_time",
                    "process_main",
                }
                extra = _xml_children(device_status, known_fields)
                if extra:
                    json_device_status["extra"] = extra
                return json_device_status
            else:
                raise RequestFailedError(response.status)

    async def network_info(self) -> dict:
        """
        Fetches network information from the device.

        This includes device **IP**, **MAC Address** and **Speed**.
        """
        if self.version == 4:
            raise ValueError("Network details are not established for version 4.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['network_info']}"

        if self.version == 2:
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
            }
        elif self.version == 5:
            headers = {"Host": self.host}
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                network_info = ElementTree.fromstring(content)
                json_network_info = {}
                for lan in network_info.iter("lan"):
                    mac = lan.findtext("mac")
                    if mac != "No found.":
                        json_network_info[mac] = {
                            "speed": lan.findtext("speed"),
                            "dhcp_enable": bool(int(lan.findtext("dhcp_enable"))),
                            "dns_manual": bool(int(lan.findtext("dns_manual"))),
                            "ip": lan.findtext("ip"),
                            "netmask": lan.findtext("netmask"),
                            "gateway": lan.findtext("gateway"),
                            "lan_speed": lan.findtext("lan_speed"),
                            "lan_enabled": bool(int(lan.findtext("lan_status"))),
                            "dns1": lan.findtext("dns1"),
                            "dns2": lan.findtext("dns2"),
                            "dns3": lan.findtext("dns3"),
                        }
                return json_network_info
            else:
                raise RequestFailedError(response.status)

    async def device_info(self) -> dict:
        """
        Fetches device information from the device.

        Data such as **Serial Number**, **Name** and **Description** are returned.
        """
        if self.version == 4:
            return await self._v4.device_info()

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['device_info']}"
        if self.version == 2:
            data = "cmd=cgi_get_device_info"
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        elif self.version == 5:
            data = "cmd=cgi_get_device_info"
            headers = {
                "Host": self.host,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                device_info = ElementTree.fromstring(content)

                json_device_info = {
                    "serial_number": None,
                    "name": None,
                    "description": None,
                }

                serial_number_node = device_info.find(".//serial_number")
                name_node = device_info.find(".//name")
                description_node = device_info.find(".//description")

                json_device_info["serial_number"] = serial_number_node.text
                json_device_info["name"] = name_node.text
                json_device_info["description"] = description_node.text

                self._confirm_os5_session_ownership()
                return json_device_info
            else:
                raise RequestFailedError(response.status)

    async def system_version(self) -> dict:
        """Fetches system firmware version from the device."""
        if self.version == 4:
            return await self._v4.system_version()

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['system_version']}"
        if self.version == 2:
            data = "cmd=get_firm_v_xml"
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        elif self.version == 5:
            data = "cmd=get_firm_v_xml"
            headers = {
                "Host": self.host,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                device_version = ElementTree.fromstring(content)
                json_device_version = {"firmware": None, "oled": None}

                firmware_node = device_version.find(".//fw")
                oled_node = device_version.find(".//oled")

                json_device_version["firmware"] = firmware_node.text

                if oled_node.text is not None:
                    json_device_version["oled"] = oled_node.text.strip("\n")

                return json_device_version
            else:
                raise RequestFailedError(response.status)

    async def latest_version(self) -> dict:
        """
        Fetches the latest firmware version from the device.

        Only devices with **V2** OS are supported.
        """
        if self.version != 2:
            raise ValueError("Unsupported/invalid version. Must be 2.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['device_info']}"
        data = "cmd=get_auto_fw_version"
        headers = {
            "Host": self.host,
            "X-CSRF-Token": self.wd_csrf_token,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                latest_version = ElementTree.fromstring(content)
                json_latest_version = {"new": None, "details": {}}

                new_node = latest_version.find(".//new")
                version_node = latest_version.find(".//version")
                path_node = latest_version.find(".//path")
                releasenote_node = latest_version.find(".//releasenote")

                if new_node.text is not None:
                    json_latest_version["new"] = bool(int(new_node.text))
                else:
                    json_latest_version["new"] = False

                json_latest_version["details"]["version"] = version_node.text
                json_latest_version["details"]["path"] = path_node.text
                json_latest_version["details"]["releasenote"] = releasenote_node.text

                return json_latest_version
            else:
                raise RequestFailedError(response.status)

    async def accounts(self) -> dict:
        """Fetches account information from the device."""
        if self.version == 4:
            raise ValueError("Account enumeration is not established for version 4.")
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['accounts']}"
        if self.version == 2:
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
            }
        elif self.version == 5:
            headers = {
                "Host": self.host,
            }
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.post(url, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                accounts = ElementTree.fromstring(content)
                json_accounts = {"users": [], "groups": {}}
                for user in accounts.iter("item"):
                    uid = user.findtext("uid")
                    if user.findtext("pwd") is not None:
                        password_bool = bool(int(user.findtext("pwd")))
                    else:
                        password_bool = False
                    last_name_list = []
                    for lastName in user.iter("last_name"):
                        last_name_list.append(lastName.text)
                    json_accounts["users"].append(
                        {
                            "uid": uid,
                            "name": user.findtext("name"),
                            "email": user.findtext("email"),
                            "pwd": password_bool,
                            "gid": user.findtext("gid"),
                            "first_name": user.findtext("first_name"),
                            "last_name": last_name_list,
                            "hint": user.findtext("hint"),
                        }
                    )
                for group in accounts.iter("item"):
                    gid = group.findtext("gid")
                    json_accounts["groups"][gid] = {
                        "name": group.findtext("name"),
                        "user_cnt": user.findtext("user_cnt"),
                    }
                    json_accounts["groups"][gid]["users"] = []
                    for user in group.iter("users"):
                        json_accounts["groups"][gid]["users"].append(
                            user.findtext("user")
                        )
                return json_accounts
            else:
                raise RequestFailedError(response.status)

    async def alerts(self) -> tuple:
        """Retrieves the current system alerts and notifications from the device."""
        if self.version == 4:
            raise ValueError("Alert retrieval is not established for version 4.")
        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['alerts']}"
        data = "cmd=cgi_get_alert"
        if self.version == 2:
            headers = {
                "Host": self.host,
                "X-CSRF-Token": self.wd_csrf_token,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        elif self.version == 5:
            headers = {
                "Host": self.host,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        else:
            raise ValueError("Unsupported/invalid version.")

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                alerts_xml = ElementTree.fromstring(content)
                alerts_list = []
                for entry in alerts_xml.iter("alerts"):
                    alerts_list.append(
                        {
                            "code": entry.findtext("code"),
                            "seq_num": entry.findtext("seq_num"),
                            "level": entry.findtext("level"),
                            "msg": entry.findtext("msg"),
                            "desc": entry.findtext("desc"),
                            "time": entry.findtext("time"),
                        }
                    )

                return len(alerts_list), alerts_list
            else:
                raise RequestFailedError(response.status)

    async def diagnostics(self) -> dict:
        """Fetch disk, fan, board, and RAID diagnostic information."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['diagnostics']}"
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        async with self.session.post(url, data="cmd=2", headers=headers) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            known_root_fields = {"board_temperature", "fan", "hd_status", "raid"}
            hd_status = root.find("hd_status")
            raid = root.find("raid")
            result = {
                "board_temperature": _optional_int(root.findtext("board_temperature")),
                "fan_rpm": _optional_int(root.findtext("fan")),
                "disk_statuses": [
                    disk.text for disk in root.findall("./hd_status/disk")
                ],
                "disk_status_extra": (
                    _xml_children(hd_status, {"disk"}) if hd_status is not None else {}
                ),
                "raid": {
                    "volume_count": _optional_int(raid.findtext("cntVol"))
                    if raid is not None
                    else None,
                    "result": _optional_int(raid.findtext("res"))
                    if raid is not None
                    else None,
                    "disk_count": _optional_int(raid.findtext("cntDisk"))
                    if raid is not None
                    else None,
                    "volumes": [],
                    "extra": {},
                },
                "extra": {},
            }

            if raid is not None:
                for item in raid.findall("item"):
                    known_item_fields = {"num", "vol", "raidmode", "state"}
                    result["raid"]["volumes"].append(
                        {
                            "id": item.get("id"),
                            "number": _optional_int(item.findtext("num")),
                            "volume": item.findtext("vol"),
                            "mode": item.findtext("raidmode"),
                            "state": item.findtext("state"),
                            "extra": _xml_children(item, known_item_fields),
                        }
                    )
                result["raid"]["extra"] = _xml_children(
                    raid, {"cntVol", "res", "cntDisk", "item"}
                )

            result["extra"] = _xml_children(root, known_root_fields)
            return result

    async def capacity_usage(self) -> list:
        """Return the API's raw per-volume capacity and media-category values."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['capacity_usage']}"
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        async with self.session.post(
            url, data="cmd=cgi_Status_CapacityUsage", headers=headers
        ) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            field_names = {
                "volume": "volume",
                "total_size": "total_size",
                "use_size": "used_size",
                "free_size": "free_size",
                "used_rate": "used_rate",
                "Shared_Pictures": "shared_pictures",
                "Shared_Music": "shared_music",
                "Shared_Videos": "shared_videos",
            }
            volumes = []
            for item in root.findall(".//item"):
                volume = {
                    output_name: _optional_int(item.findtext(xml_name))
                    for xml_name, output_name in field_names.items()
                }
                volume["extra"] = _xml_children(item, field_names)
                volumes.append(volume)
            return volumes

    async def volume_status(self) -> list:
        """Return detailed read-only volume, filesystem, and RAID status."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['volume_status']}"
        headers = {"Host": self.host}
        async with self.session.get(url, headers=headers) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            numeric_fields = {
                "volume",
                "web_cmd",
                "sdx3_flag",
                "raid_status",
                "size",
                "used_size",
                "free_size",
                "percentage",
                "dev_num",
                "volume_encrypt",
                "scsi_mapping",
                "is_roaming_volume",
            }
            known_fields = {
                "volume",
                "raid_mode",
                "raid_status",
                "file_type",
                "size",
                "used_size",
                "free_size",
                "percentage",
                "device",
                "dev_num",
                "mount",
                "mount_status",
                "volume_encrypt",
                "is_roaming_volume",
                "scsi_mapping",
                "spare",
                "to_be_sync",
                "web_cmd",
                "sdx3_flag",
                "used_device",
                "raid_uuid",
                "scsi0_serial",
                "scsi1_serial",
            }
            volume_info = (
                root if root.tag == "volume_info" else root.find(".//volume_info")
            )
            volumes = []
            items = volume_info.findall("item") if volume_info is not None else []
            for item in items:
                volume = {}
                for field in known_fields:
                    value = item.findtext(field)
                    volume[field] = (
                        _optional_int(value) if field in numeric_fields else value
                    )
                volume["extra"] = _xml_children(item, known_fields)
                volumes.append(volume)
            return volumes

    async def system_warning(self) -> dict:
        """Return the OS 5 temperature/busy warning code and known meaning."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['system_warning']}"
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        async with self.session.post(url, data="cmd=8", headers=headers) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            code = _optional_int(root.findtext(".//res"))
            statuses = {
                0: "normal",
                1: "over_temperature",
                2: "system_busy_hotplug",
            }
            return {"code": code, "status": statuses.get(code, "unknown")}

    async def internet_access(self) -> bool:
        """Return only the internet-connectivity assessment reported by the NAS."""
        if self.version == 4:
            return await self._v4.internet_access()
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 4 or 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['internet_access']}"
        async with self.session.get(url, headers={"Host": self.host}) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            value = root.findtext(".//connectivity")
            if value is None:
                raise ValueError("NAS response has no connectivity value")
            normalized = value.strip().lower()
            if normalized == "true":
                return True
            if normalized == "false":
                return False
            raise ValueError(f"Unknown NAS connectivity value: {value!r}")

    async def process_info(self) -> list:
        """Return the diagnostic process list reported by OS 5."""
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}{ENDPOINTS[self.version]['process_info']}"
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        async with self.session.post(
            url, data="cmd=cgi_process", headers=headers
        ) as response:
            if response.status != 200:
                raise RequestFailedError(response.status)

            root = ElementTree.fromstring(await response.text())
            processes = []
            for item in root.findall(".//process_main"):
                known_fields = {"command", "user", "pid", "cpu", "mem"}
                processes.append(
                    {
                        "command": item.findtext("command"),
                        "user": item.findtext("user"),
                        "pid": _optional_int(item.findtext("pid")),
                        "cpu": _optional_number(item.findtext("cpu")),
                        "mem": _optional_number(item.findtext("mem")),
                        "extra": _xml_children(item, known_fields),
                    }
                )
            return processes

    async def cloud_access(self) -> dict:
        """
        Fetches cloud access information from the device.

        Only devices with **V5** OS are supported.
        """
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}/web/restSDK/cloudAccess.php"

        data = "cmd=getCloudAccess"
        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        async with self.session.post(url, data=data, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                json_content = json.loads(content)
                return json_content
            else:
                raise RequestFailedError(response.status)

    async def usb_info(self) -> dict:
        """
        Fetches connected USB devices from the device.

        Only devices with **V5** OS are supported.
        """
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}/web/get_usb_info.php"

        headers = {"Host": self.host}

        async with self.session.get(url, headers=headers) as response:
            if response.status == 200:
                content = await response.text()
                json_content = json.loads(content)
                return json_content
            else:
                raise RequestFailedError(response.status)

    async def uptime(self) -> int:
        """
        Returns the device uptime in seconds (parsed).

        Only devices with **V5** OS are supported.
        """
        if self.version != 5:
            raise ValueError("Unsupported/invalid version. Must be 5.")

        url = f"{SCHEME}{self.host}/cgi-bin/status_mgr.cgi"

        headers = {
            "Host": self.host,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

        data = "cmd=cgi_get_uptime"

        async with self.session.post(url, data=data, headers=headers) as response:
            text = await response.text()

            if response.status != 200:
                if response.status == 500 and not text.strip():
                    raise UptimeUnavailableError(response.status)
                raise RequestFailedError(response.status)

            match = re.search(r"<uptime>(.*?)</uptime>", text, re.DOTALL)
            if not match:
                raise UptimeUnavailableError()

            uptime_raw = match.group(1).strip()

            match = re.search(
                r"(\d+)\s*days?\s*(\d+)\s*hour[s]?\s*(\d+)\s*minute[s]?",
                uptime_raw,
                re.IGNORECASE,
            )

            if not match:
                raise UptimeUnavailableError()

            d, h, m = map(int, match.groups())
            return int(timedelta(days=d, hours=h, minutes=m).total_seconds())
