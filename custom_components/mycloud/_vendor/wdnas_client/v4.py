"""Adapter for the legacy WD My Cloud REST 2.1 XML API."""

from __future__ import annotations

import asyncio
import re
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

import aiohttp

from .const import POWER_ACTION_TIMEOUT, SCHEME, V4_ENDPOINTS
from .exceptions import InvalidLoginError, RequestFailedError

_CAPACITY_UNITS = {
    "B": 1,
    "KB": 1000,
    "MB": 1000**2,
    "GB": 1000**3,
    "TB": 1000**4,
}


def _text(element: ElementTree.Element, path: str) -> str | None:
    """Return stripped XML text without inventing a fallback value."""
    value = element.findtext(path)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _flat_xml(element: ElementTree.Element) -> dict[str, str | None]:
    """Preserve direct XML fields used by the legacy REST resources."""
    return {child.tag: _text(element, child.tag) for child in element}


def parse_capacity(value: str | None) -> int | None:
    """Convert a documented decimal capacity label such as ``2TB`` to bytes."""
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([KMGT]?B)\s*", value, re.IGNORECASE)
    if match is None:
        return None
    try:
        return int(Decimal(match.group(1)) * _CAPACITY_UNITS[match.group(2).upper()])
    except InvalidOperation:
        return None


def _required_nonnegative_int(element: ElementTree.Element, field: str) -> int:
    """Parse one required non-negative byte counter."""
    value = _text(element, field)
    if value is None:
        raise ValueError(f"V4 storage usage has no {field} value")
    try:
        parsed = int(value)
    except ValueError:
        raise ValueError(f"Invalid V4 storage usage {field} value") from None
    if parsed < 0:
        raise ValueError(f"Invalid negative V4 storage usage {field} value")
    return parsed


def _optional_nonnegative_int(element: ElementTree.Element, field: str) -> int | None:
    """Parse one optional media byte counter without inventing a value."""
    value = _text(element, field)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


class V4Adapter:
    """Use one existing aiohttp session for the V4 REST resources."""

    def __init__(self, owner) -> None:
        self._owner = owner
        self._system_information_cache: dict | None = None
        self._firmware_cache: dict | None = None

    @property
    def _session(self):
        return self._owner.session

    def reset(self) -> None:
        """Drop data tied to the previous authenticated session."""
        self._system_information_cache = None
        self._firmware_cache = None

    def _url(self, endpoint: str) -> str:
        return f"{SCHEME}{self._owner.host}{V4_ENDPOINTS[endpoint]}"

    async def login(self) -> None:
        """Open a provisional V4 session using the firmware's query parameters."""
        try:
            async with self._session.get(
                self._url("login"),
                params={
                    "username": self._owner.username,
                    "password": self._owner.password,
                },
                headers={"Accept": "application/xml", "Host": self._owner.host},
            ) as response:
                if response.status in (401, 403):
                    raise InvalidLoginError(
                        "The NAS rejected the credentials", response.status
                    )
                if response.status != 200:
                    raise RequestFailedError(response.status)
                await response.text()
        except aiohttp.ClientError:
            # aiohttp exceptions may include the full query URL. Do not expose it.
            raise aiohttp.ClientConnectionError("V4 login request failed") from None

    async def _get_xml(self, endpoint: str, expected_root: str) -> ElementTree.Element:
        try:
            async with self._session.get(
                self._url(endpoint),
                headers={"Accept": "application/xml", "Host": self._owner.host},
            ) as response:
                if response.status in (401, 403):
                    raise InvalidLoginError(
                        "The NAS session is not authenticated", response.status
                    )
                if response.status != 200:
                    raise RequestFailedError(response.status)
                root = ElementTree.fromstring(await response.text())
        except aiohttp.ClientError:
            raise aiohttp.ClientConnectionError(
                f"V4 {endpoint} request failed"
            ) from None

        if root.tag != expected_root:
            raise ValueError(f"Unexpected V4 XML root for {endpoint}: {root.tag}")
        return root

    async def system_information(self) -> dict:
        """Return the verified V4 system-information fields."""
        if self._system_information_cache is None:
            root = await self._get_xml("system_information", "system_information")
            self._system_information_cache = _flat_xml(root)
        return dict(self._system_information_cache)

    async def firmware_info(self) -> dict:
        """Return the verified current firmware package fields."""
        if self._firmware_cache is None:
            root = await self._get_xml("firmware_info", "firmware_info")
            package = root.find("./current_firmware/package")
            self._firmware_cache = (
                {
                    "name": _text(package, "name"),
                    "version": _text(package, "version"),
                    "description": _text(package, "description"),
                }
                if package is not None
                else {"name": None, "version": None, "description": None}
            )
        return dict(self._firmware_cache)

    async def system_state(self) -> dict:
        """Return qualitative state strings without treating them as measurements."""
        root = await self._get_xml("system_state", "system_state")
        values = _flat_xml(root)
        return {
            "status": values.pop("status", None),
            "temperature": values.pop("temperature", None),
            "smart": values.pop("smart", None),
            "volume": values.pop("volume", None),
            "free_space": values.pop("free_space", None),
            "reported_status": values.pop("reported_status", None),
            "extra": values,
        }

    async def storage_usage(self) -> dict:
        """Return verified byte counters from the V4 storage-usage resource."""
        root = await self._get_xml("storage_usage", "storage_usage")
        size = _required_nonnegative_int(root, "size")
        usage = _required_nonnegative_int(root, "usage")
        if usage > size:
            raise ValueError("V4 storage usage exceeds capacity")

        return {
            "size": size,
            "usage": usage,
            "free": size - usage,
            "used_percentage": usage * 100 / size if size > 0 else None,
            "video": _optional_nonnegative_int(root, "video"),
            "photos": _optional_nonnegative_int(root, "photos"),
            "music": _optional_nonnegative_int(root, "music"),
            "other": _optional_nonnegative_int(root, "other"),
        }

    async def internet_access(self) -> bool:
        """Return the NAS-reported internet connectivity state."""
        root = await self._get_xml("internet_access", "internet_access")
        value = _text(root, "connectivity")
        if value is None:
            raise ValueError("V4 internet access has no connectivity value")
        normalized = value.casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
        raise ValueError("Invalid V4 internet connectivity value")

    async def power_action(self, state: str) -> None:
        """Send one source-confirmed V4 power action without retrying it."""
        if state not in ("reboot", "halt"):
            raise ValueError("Unsupported V4 power state")
        if (
            self._session is None
            or getattr(self._session, "closed", False)
            or not self._owner._authenticated
        ):
            raise InvalidLoginError("An authenticated V4 NAS session is required")

        parameters = {"state": state}
        async with asyncio.timeout(POWER_ACTION_TIMEOUT):
            async with self._session.put(
                self._url("shutdown"),
                params=parameters,
                data=parameters,
                headers={"Accept": "application/xml", "Host": self._owner.host},
            ) as response:
                if response.status in (401, 403):
                    raise InvalidLoginError(
                        "The V4 NAS session is not authenticated", response.status
                    )
                if response.status != 200:
                    raise RequestFailedError(response.status)
                await response.text()

    async def system_info(self) -> dict:
        """Adapt V4 capacity to the existing public system-info structure."""
        information = await self.system_information()
        storage = None
        try:
            storage = await self.storage_usage()
        except InvalidLoginError:
            raise
        except (
            aiohttp.ClientError,
            RequestFailedError,
            ElementTree.ParseError,
            TimeoutError,
            ValueError,
        ):
            # This firmware resource is optional. Retain the established capacity
            # label fallback when it is absent or returns unusable data.
            pass

        return {
            "disks": [],
            "volumes": [],
            "size": {
                "total": (
                    storage["size"]
                    if storage is not None
                    else parse_capacity(information.get("capacity"))
                ),
                "used": storage["usage"] if storage is not None else None,
                "unused": storage["free"] if storage is not None else None,
                "used_percentage": (
                    storage["used_percentage"] if storage is not None else None
                ),
            },
            "capacity_label": information.get("capacity"),
            "storage_usage": storage,
            "storage_usage_available": storage is not None,
        }

    async def device_info(self) -> dict:
        """Adapt V4 identity to the established client response keys."""
        information = await self.system_information()
        return {
            "serial_number": information.get("serial_number"),
            "name": information.get("host_name"),
            "description": information.get("model_description"),
            "model_name": information.get("model_name"),
            "model_number": information.get("model_number"),
            "manufacturer": information.get("manufacturer"),
        }

    async def system_version(self) -> dict:
        """Adapt V4 package data to the established firmware response keys."""
        firmware = await self.firmware_info()
        return {
            "firmware": firmware.get("version"),
            "oled": None,
            "name": firmware.get("name"),
            "description": firmware.get("description"),
        }
