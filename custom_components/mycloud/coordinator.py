"""Data coordinator for the My Cloud integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from time import monotonic
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ._vendor.wdnas_client import client as nas_client
from ._vendor.wdnas_client.exceptions import InvalidLoginError, RequestFailedError
from .const import (
    DEFAULT_RELEASE_ADMIN_SESSION,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    HOST,
    PASSWORD,
    RELEASE_ADMIN_SESSION,
    UPDATE_TIMEOUT,
    USERNAME,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

OPTIONAL_API_INTERVALS = {
    "diagnostics": 600,
    "system_warning": 600,
    "internet_access": 600,
    "volume_status": 3600,
}


class PowerActionInProgressError(RuntimeError):
    """A NAS power action is already running."""


class PowerActionUnavailableError(RuntimeError):
    """The authenticated coordinator session is unavailable."""


def _is_auth_error(error: Exception) -> bool:
    """Return whether an older client exception represents an auth failure."""
    if isinstance(error, InvalidLoginError):
        return True
    return isinstance(error, RequestFailedError) and any(
        status in str(error) for status in ("401", "403")
    )


class MyCloudDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate one authenticated session and shared NAS polling."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: Any | None = None,
        *,
        authenticated: bool = False,
    ) -> None:
        """Initialize the coordinator."""
        self.config_entry = config_entry
        self.client = client or nas_client(
            config_entry.data[USERNAME],
            config_entry.data[PASSWORD],
            config_entry.data[HOST],
            config_entry.data[VERSION],
        )
        self._optional_cache: dict[str, Any] = {}
        self._optional_available = {key: False for key in OPTIONAL_API_INTERVALS}
        self._optional_last_attempt: dict[str, float] = {}
        self._session_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._power_action_active = False
        self._session_ready = authenticated
        self._has_successful_update = False
        self._release_admin_session = config_entry.data[
            VERSION
        ] == 5 and config_entry.options.get(
            RELEASE_ADMIN_SESSION, DEFAULT_RELEASE_ADMIN_SESSION
        )
        update_interval = config_entry.options.get(
            "update_interval", DEFAULT_UPDATE_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{config_entry.entry_id}",
            update_interval=timedelta(seconds=update_interval),
        )

    async def _async_setup(self) -> None:
        """Open and authenticate the shared client session once."""
        await self._async_login(transient_auth_failure=self._release_admin_session)

    async def _async_login(self, *, transient_auth_failure: bool = False) -> None:
        """Authenticate, translating failures into Home Assistant semantics."""
        if self.config_entry.data[VERSION] == 5:
            try:
                async with asyncio.timeout(UPDATE_TIMEOUT):
                    await self.client.__aenter__()
                self._session_ready = True
            except Exception as error:
                await self._async_close_client()
                if _is_auth_error(error):
                    if transient_auth_failure:
                        raise UpdateFailed(
                            "OS 5 administrator session is temporarily unavailable"
                        ) from error
                    raise ConfigEntryAuthFailed("Invalid NAS credentials") from error
                if isinstance(
                    error, (aiohttp.ClientError, TimeoutError, RequestFailedError)
                ):
                    raise UpdateFailed(
                        f"Unable to connect to the NAS: {error}"
                    ) from error
                raise UpdateFailed(
                    f"Unable to initialize the NAS client: {error}"
                ) from error
            return

        async with self._session_lock:
            if self._session_ready:
                return
            try:
                async with asyncio.timeout(UPDATE_TIMEOUT):
                    await self.client.__aenter__()
                self._session_ready = True
            except Exception as error:
                await self._async_close_client_unlocked()
                if _is_auth_error(error):
                    raise ConfigEntryAuthFailed("Invalid NAS credentials") from error
                if isinstance(
                    error, (aiohttp.ClientError, TimeoutError, RequestFailedError)
                ):
                    raise UpdateFailed(
                        f"Unable to connect to the NAS: {error}"
                    ) from error
                raise UpdateFailed(
                    f"Unable to initialize the NAS client: {error}"
                ) from error

    async def _async_fetch_optional(self, key: str, now: float) -> Any:
        """Fetch one rate-limited OS 5 endpoint without failing the base update."""
        last_attempt = self._optional_last_attempt.get(key)
        if (
            last_attempt is not None
            and now - last_attempt < OPTIONAL_API_INTERVALS[key]
        ):
            return self._optional_cache.get(key)

        self._optional_last_attempt[key] = now
        try:
            method = getattr(self.client, key)
            async with asyncio.timeout(UPDATE_TIMEOUT):
                value = await method()
        except Exception as error:
            if _is_auth_error(error):
                self._optional_last_attempt.pop(key, None)
                raise
            self._optional_available[key] = False
            _LOGGER.warning(
                "Optional NAS monitoring endpoint %s is unavailable (%s)",
                key,
                type(error).__name__,
            )
            return self._optional_cache.get(key)

        self._optional_cache[key] = value
        self._optional_available[key] = True
        return value

    async def _async_fetch_v4_optional(self, key: str, default: Any) -> Any:
        """Fetch one V4 resource without blocking the core identity data."""
        try:
            method = getattr(self.client, key)
            async with asyncio.timeout(UPDATE_TIMEOUT):
                value = await method()
        except Exception as error:
            if _is_auth_error(error):
                raise
            self._optional_available[key] = False
            _LOGGER.warning(
                "Optional V4 NAS endpoint %s is unavailable (%s)",
                key,
                type(error).__name__,
            )
            return self._optional_cache.get(key, default)

        self._optional_cache[key] = value
        self._optional_available[key] = True
        return value

    async def _async_fetch_data(self) -> dict[str, Any]:
        """Fetch established data and independently collect optional OS 5 data."""
        if self.config_entry.data[VERSION] == 4:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                system_info = await self.client.system_info()
                device_info = await self.client.device_info()

            device_info["serial_number"] = (
                device_info.get("serial_number")
                or self.config_entry.unique_id
                or f"4:{self.config_entry.data[HOST]}"
            )
            device_info["name"] = (
                device_info.get("name") or self.config_entry.data[HOST]
            )
            device_info["description"] = device_info.get("description") or "WD My Cloud"
            data = {
                "system_info": system_info,
                "device_info": device_info,
                "system_version": await self._async_fetch_v4_optional(
                    "system_version", {"firmware": None, "oled": None}
                ),
                "system_status": await self._async_fetch_v4_optional(
                    "system_status", {}
                ),
                "internet_access": await self._async_fetch_v4_optional(
                    "internet_access", None
                ),
            }
            data["optional_available"] = self._optional_available.copy()
            return data

        async with asyncio.timeout(UPDATE_TIMEOUT):
            data = {
                "system_info": await self.client.system_info(),
                "system_status": await self.client.system_status(),
                "device_info": await self.client.device_info(),
                "system_version": await self.client.system_version(),
            }

        if self.config_entry.data[VERSION] == 5:
            now = monotonic()
            for key in OPTIONAL_API_INTERVALS:
                data[key] = await self._async_fetch_optional(key, now)
            data["optional_available"] = self._optional_available.copy()

        return data

    async def _async_update_data(self) -> dict[str, Any]:
        """Serialize polling with explicit device actions."""
        async with self._operation_lock:
            if not self._release_admin_session:
                return await self._async_update_data_locked()

            try:
                if not self._session_ready:
                    await self._async_login(transient_auth_failure=True)
                return await self._async_update_data_locked()
            finally:
                await self._async_close_client()

    async def _async_update_data_locked(self) -> dict[str, Any]:
        """Fetch data and retry once after an expired authenticated session."""
        if self.config_entry.data[VERSION] == 5:
            try:
                data = await self._async_fetch_data()
                self._has_successful_update = True
                return data
            except Exception as error:
                if _is_auth_error(error):
                    if self._release_admin_session:
                        raise UpdateFailed(
                            "OS 5 administrator session is temporarily unavailable"
                        ) from error
                    _LOGGER.debug("NAS session expired; re-authenticating once")
                    try:
                        await self._async_close_client()
                        await self._async_login()
                        data = await self._async_fetch_data()
                        self._has_successful_update = True
                        return data
                    except ConfigEntryAuthFailed:
                        raise
                    except Exception as retry_error:
                        if _is_auth_error(retry_error):
                            raise ConfigEntryAuthFailed(
                                "NAS credentials were rejected during reauthentication"
                            ) from retry_error
                        raise UpdateFailed(
                            "Failed to fetch NAS data after reauthentication: "
                            f"{retry_error}"
                        ) from retry_error

                if isinstance(
                    error, (aiohttp.ClientError, TimeoutError, RequestFailedError)
                ):
                    raise UpdateFailed(f"Error fetching NAS data: {error}") from error
                raise UpdateFailed(f"Unexpected NAS response: {error}") from error

        try:
            data = await self._async_fetch_data()
            self._has_successful_update = True
            return data
        except Exception as error:
            if _is_auth_error(error):
                if not self._has_successful_update:
                    await self._async_close_client()
                    raise ConfigEntryAuthFailed(
                        "NAS rejected the initial authenticated session; login was not "
                        "retried"
                    ) from error
                _LOGGER.debug("NAS session expired; re-authenticating once")
                try:
                    if not await self._async_close_client():
                        raise UpdateFailed(
                            "NAS session cleanup failed; automatic login was not retried"
                        )
                    await self._async_login()
                    data = await self._async_fetch_data()
                    self._has_successful_update = True
                    return data
                except ConfigEntryAuthFailed:
                    raise
                except Exception as retry_error:
                    if _is_auth_error(retry_error):
                        raise ConfigEntryAuthFailed(
                            "NAS credentials were rejected during reauthentication"
                        ) from retry_error
                    raise UpdateFailed(
                        f"Failed to fetch NAS data after reauthentication: {retry_error}"
                    ) from retry_error

            if isinstance(
                error, (aiohttp.ClientError, TimeoutError, RequestFailedError)
            ):
                raise UpdateFailed(f"Error fetching NAS data: {error}") from error
            raise UpdateFailed(f"Unexpected NAS response: {error}") from error

    async def async_execute_power_action(self, action: str) -> None:
        """Run one supported power action without login or automatic retry."""
        if self.config_entry.data[VERSION] not in (4, 5) or action not in (
            "reboot",
            "shutdown",
        ):
            raise PowerActionUnavailableError
        if (
            not self.last_update_success
            or not self._session_ready
            and not self._release_admin_session
        ):
            raise PowerActionUnavailableError
        if self._power_action_active:
            raise PowerActionInProgressError

        self._power_action_active = True
        try:
            async with self._operation_lock:
                if not self._session_ready and self._release_admin_session:
                    await self._async_login(transient_auth_failure=True)
                if not self._session_ready:
                    raise PowerActionUnavailableError
                try:
                    await getattr(self.client, action)()
                finally:
                    if self._release_admin_session:
                        await self._async_close_client()
        finally:
            self._power_action_active = False

    async def async_shutdown(self) -> None:
        """Stop coordinator timers and close the shared client session."""
        await super().async_shutdown()
        async with self._operation_lock:
            await self._async_close_client()

    async def _async_close_client(self) -> bool:
        """Serialize logout and local session cleanup."""
        async with self._session_lock:
            return await self._async_close_client_unlocked()

    async def _async_close_client_unlocked(self) -> bool:
        """Log out and close without masking coordinator errors."""
        self._session_ready = False
        session = getattr(self.client, "session", None)
        if session is not None and not getattr(session, "closed", False):
            try:
                await self.client.close()
            except Exception as error:  # noqa: BLE001 - cleanup must not mask unload
                _LOGGER.warning("NAS session cleanup failed (%s)", type(error).__name__)
                return False
        return True
