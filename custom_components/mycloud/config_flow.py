"""Config flow for the My Cloud integration."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from ._vendor.wdnas_client import client as nas_client
from ._vendor.wdnas_client.exceptions import InvalidLoginError, RequestFailedError
from .const import (
    CONFIG_FLOW_TIMEOUT,
    DEFAULT_RELEASE_ADMIN_SESSION,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    HOST,
    MIN_UPDATE_INTERVAL,
    PASSWORD,
    RELEASE_ADMIN_SESSION,
    USERNAME,
)
from .const import (
    VERSION as API_VERSION,
)
from .session import (
    ReauthHandoffCleanupError,
    async_discard_reauth_client,
    store_reauth_client,
)

_HOST_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class ReauthRejected(Exception):
    """The NAS rejected the credentials or already has an exclusive session."""


class ReauthConnectionError(Exception):
    """The NAS could not be reached during reauthentication."""


class ReauthTimeoutError(Exception):
    """The reauthentication request timed out."""


class ReauthUnsupportedError(Exception):
    """The stored API version is unsupported by the client."""


class CannotConnect(Exception):
    """Raised when an OS 2/5 NAS cannot be reached."""


class InvalidAuth(Exception):
    """Raised when an OS 2/5 NAS rejects the credentials."""


class UnsupportedFirmware(Exception):
    """Raised when the selected OS 2/5 API version is unsupported."""


def _validate_host(value: str) -> str:
    """Validate and normalize a plain IP address or hostname."""
    host = value.strip()
    if not host or "://" in host or any(char in host for char in "/?#"):
        raise vol.Invalid("Enter an IP address or hostname without a URL or path")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host.lower()
    except ValueError:
        pass
    if len(host) > 253 or any(
        not _HOST_LABEL.fullmatch(label) for label in host.rstrip(".").split(".")
    ):
        raise vol.Invalid("Invalid hostname")
    return host.rstrip(".").lower()


def _is_auth_error(error: Exception) -> bool:
    """Return whether the client reported rejected authentication."""
    if isinstance(error, InvalidLoginError):
        return True
    return isinstance(error, RequestFailedError) and any(
        status in str(error) for status in ("401", "403")
    )


async def _async_close_legacy_client(client: Any) -> None:
    """Use the historical local-only context cleanup for OS 2 and OS 5."""
    session = getattr(client, "session", None)
    if session is not None and not getattr(session, "closed", False):
        await client.__aexit__(None, None, None)


async def async_validate_input(data: dict[str, Any]) -> dict[str, str]:
    """Run the historical OS 2/5 probe login and return stable identity data."""
    version = data[API_VERSION]
    if version not in (2, 5):
        raise UnsupportedFirmware

    client = nas_client(data[USERNAME], data[PASSWORD], data[HOST], version)
    try:
        async with asyncio.timeout(CONFIG_FLOW_TIMEOUT):
            await client.__aenter__()
            device = await client.device_info()
    except ValueError as error:
        raise UnsupportedFirmware from error
    except Exception as error:
        if _is_auth_error(error):
            raise InvalidAuth from error
        if isinstance(error, (aiohttp.ClientError, TimeoutError, RequestFailedError)):
            raise CannotConnect from error
        raise
    finally:
        await _async_close_legacy_client(client)

    serial = str(device.get("serial_number") or "").strip()
    name = str(device.get("name") or data[HOST]).strip()
    return {
        "title": name,
        "unique_id": serial or f"{version}:{data[HOST]}",
    }


async def _async_validate_reauthentication(data: dict[str, Any]) -> Any:
    """Return one authenticated client for the coordinator to take over."""
    client = nas_client(data[USERNAME], data[PASSWORD], data[HOST], data[API_VERSION])
    try:
        async with asyncio.timeout(CONFIG_FLOW_TIMEOUT):
            await client.__aenter__()
            await client.device_info()
    except TimeoutError as error:
        await _async_discard_failed_client(client)
        raise ReauthTimeoutError from error
    except ValueError as error:
        await _async_discard_failed_client(client)
        raise ReauthUnsupportedError from error
    except Exception as error:
        await _async_discard_failed_client(client)
        if _is_auth_error(error):
            raise ReauthRejected from error
        if isinstance(error, (aiohttp.ClientError, RequestFailedError)):
            raise ReauthConnectionError from error
        raise

    return client


async def _async_discard_failed_client(client: Any) -> None:
    """Best-effort local cleanup without masking the primary login failure."""
    try:
        async with asyncio.timeout(CONFIG_FLOW_TIMEOUT):
            await client.close()
    except Exception:  # noqa: BLE001, S110 - preserve primary login failure
        pass


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the serializable setup schema with explicit API selection."""
    defaults = defaults or {}
    host_key = (
        vol.Required(HOST, default=defaults[HOST])
        if HOST in defaults
        else vol.Required(HOST)
    )
    username_key = (
        vol.Required(USERNAME, default=defaults[USERNAME])
        if USERNAME in defaults
        else vol.Required(USERNAME)
    )
    return vol.Schema(
        {
            host_key: str,
            username_key: str,
            vol.Required(PASSWORD): TextSelector(
                TextSelectorConfig(
                    type=TextSelectorType.PASSWORD,
                    autocomplete="current-password",
                )
            ),
            vol.Required(
                API_VERSION, default=str(defaults.get(API_VERSION, 5))
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value="5", label="WD OS 5"),
                        SelectOptionDict(value="4", label="WD My Cloud V4"),
                        SelectOptionDict(value="2", label="WD OS 2"),
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
        }
    )


class MyCloudOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle polling options."""

    async def async_step_init(self, user_input=None):
        """Manage the existing update-interval option."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.config_entry.options.get(
            "update_interval", DEFAULT_UPDATE_INTERVAL
        )
        options = {
            vol.Optional("update_interval", default=current_interval): vol.All(
                vol.Coerce(int), vol.Range(min=MIN_UPDATE_INTERVAL)
            )
        }
        if self.config_entry.data[API_VERSION] == 5:
            release_session = self.config_entry.options.get(
                RELEASE_ADMIN_SESSION, DEFAULT_RELEASE_ADMIN_SESSION
            )
            options[vol.Optional(RELEASE_ADMIN_SESSION, default=release_session)] = (
                BooleanSelector()
            )
        options_schema = vol.Schema(options)
        return self.async_show_form(step_id="init", data_schema=options_schema)


class MyCloudConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle My Cloud configuration and reauthentication."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return MyCloudOptionsFlowHandler()

    async def async_step_user(self, user_input=None):
        """Create an entry using isolated manual API-version paths."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized_host = _validate_host(user_input[HOST])
            except vol.Invalid:
                errors[HOST] = "invalid_host"
                return self.async_show_form(
                    step_id="user",
                    data_schema=_user_schema(user_input),
                    errors=errors,
                )

            try:
                version = int(user_input.get(API_VERSION))
            except (TypeError, ValueError):
                errors[API_VERSION] = "unsupported_firmware"
                return self.async_show_form(
                    step_id="user",
                    data_schema=_user_schema(user_input),
                    errors=errors,
                )
            if version not in (2, 4, 5):
                errors[API_VERSION] = "unsupported_firmware"
                return self.async_show_form(
                    step_id="user",
                    data_schema=_user_schema(user_input),
                    errors=errors,
                )

            candidate = {
                **user_input,
                HOST: normalized_host,
                API_VERSION: version,
            }
            self._async_abort_entries_match({HOST: candidate[HOST]})
            if version == 4:
                await self.async_set_unique_id(f"{version}:{candidate[HOST]}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=candidate[HOST], data=candidate)

            try:
                info = await async_validate_input(candidate)
            except InvalidAuth:
                errors["base"] = "auth"
            except CannotConnect:
                errors["base"] = "connection"
            except UnsupportedFirmware:
                errors["base"] = "unsupported_firmware"
            except Exception:  # noqa: BLE001 - never expose secrets in flow errors
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info["unique_id"])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=info["title"], data=candidate)

        return self.async_show_form(
            step_id="user", data_schema=_user_schema(user_input), errors=errors
        )

    async def async_step_reauth(self, entry_data):
        """Start reauthentication for an existing entry."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self._reauth_cleanup_failed = False
        if self._reauth_entry.data[API_VERSION] == 4:
            try:
                await async_discard_reauth_client(self.hass, self.context["entry_id"])
            except ReauthHandoffCleanupError:
                self._reauth_cleanup_failed = True
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Verify replacement credentials before saving and reloading."""
        entry = self._reauth_entry
        errors: dict[str, str] = {}
        if self._reauth_cleanup_failed:
            errors["base"] = "logout_failed"
            user_input = None
        if user_input is not None:
            candidate = {
                **entry.data,
                USERNAME: user_input[USERNAME],
                PASSWORD: user_input[PASSWORD],
            }
            if entry.data[API_VERSION] in (2, 5):
                try:
                    info = await async_validate_input(candidate)
                except InvalidAuth:
                    errors["base"] = "auth"
                except CannotConnect:
                    errors["base"] = "connection"
                except UnsupportedFirmware:
                    errors["base"] = "unsupported_firmware"
                except Exception:  # noqa: BLE001 - never expose secrets in flow errors
                    errors["base"] = "unknown"
                else:
                    await self.async_set_unique_id(info["unique_id"])
                    if entry.unique_id is not None:
                        self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=info["unique_id"],
                        data_updates={
                            USERNAME: candidate[USERNAME],
                            PASSWORD: candidate[PASSWORD],
                        },
                    )

                return self.async_show_form(
                    step_id="reauth_confirm",
                    data_schema=self._reauth_schema(entry),
                    errors=errors,
                )

            try:
                client = await _async_validate_reauthentication(candidate)
            except ReauthRejected:
                errors["base"] = "auth_or_session"
            except ReauthTimeoutError:
                errors["base"] = "timeout"
            except ReauthConnectionError:
                errors["base"] = "connection"
            except ReauthUnsupportedError:
                errors["base"] = "unsupported_firmware"
            except Exception:  # noqa: BLE001 - never expose secrets in flow errors
                errors["base"] = "unknown"
            else:
                store_reauth_client(self.hass, entry.entry_id, client)
                try:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            USERNAME: candidate[USERNAME],
                            PASSWORD: candidate[PASSWORD],
                        },
                    )
                except Exception:
                    await async_discard_reauth_client(self.hass, entry.entry_id)
                    raise

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self._reauth_schema(entry),
            errors=errors,
        )

    @staticmethod
    def _reauth_schema(entry) -> vol.Schema:
        """Return the shared serializable credential schema."""
        return vol.Schema(
            {
                vol.Required(USERNAME, default=entry.data[USERNAME]): str,
                vol.Required(PASSWORD): TextSelector(
                    TextSelectorConfig(
                        type=TextSelectorType.PASSWORD,
                        autocomplete="current-password",
                    )
                ),
            }
        )
