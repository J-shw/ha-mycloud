"""Button platform for explicit WD My Cloud device actions."""

from __future__ import annotations

import logging
import re
from typing import Literal

import aiohttp
from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._vendor.wdnas_client import POWER_GUARD_REVISION
from ._vendor.wdnas_client.exceptions import PowerActionBlockedError
from .const import VERSION
from .coordinator import (
    MyCloudDataUpdateCoordinator,
    PowerActionInProgressError,
    PowerActionUnavailableError,
)
from .entity import main_device_info

_LOGGER = logging.getLogger(__name__)

V4_POWER_TARGET = ("sq", "04.06.00-111")
OS5_POWER_TARGET = ("wdmycloudmirror", "5.33.102")

RESTART_DESCRIPTION = ButtonEntityDescription(
    key="restart",
    name="Restart",
    icon="mdi:restart",
    device_class=ButtonDeviceClass.RESTART,
    translation_key="restart",
)
SHUTDOWN_DESCRIPTION = ButtonEntityDescription(
    key="shutdown",
    name="Shutdown",
    icon="mdi:power",
    translation_key="shutdown",
)


def _normalized_identifier(value: object) -> str:
    """Normalize only model identifiers, without broad fuzzy matching."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _supports_power_actions(config_entry: ConfigEntry, data: dict) -> bool:
    """Restrict actions to devices proven by their own WD UI source."""
    version = config_entry.data.get(VERSION)
    device = data.get("device_info", {})
    firmware = str(data.get("system_version", {}).get("firmware") or "").strip()
    if version == 4:
        return (
            _normalized_identifier(device.get("model_number")),
            firmware,
        ) == V4_POWER_TARGET

    model_identifiers = {
        _normalized_identifier(device.get(key))
        for key in ("description", "model_name", "model_number")
    }
    return (
        version == 5
        and OS5_POWER_TARGET[0] in model_identifiers
        and firmware == OS5_POWER_TARGET[1]
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up explicit power buttons for a confirmed supported NAS."""
    coordinator: MyCloudDataUpdateCoordinator = config_entry.runtime_data
    _LOGGER.info(
        "My Cloud power platform initialized (api_version=%s, client_guard=%s)",
        config_entry.data.get(VERSION),
        POWER_GUARD_REVISION,
    )
    if not _supports_power_actions(config_entry, coordinator.data):
        return

    serial = coordinator.data["device_info"]["serial_number"]
    device = main_device_info(coordinator.data)
    async_add_entities(
        (
            MyCloudPowerButton(
                coordinator,
                device,
                serial,
                RESTART_DESCRIPTION,
                "reboot",
            ),
            MyCloudPowerButton(
                coordinator,
                device,
                serial,
                SHUTDOWN_DESCRIPTION,
                "shutdown",
            ),
        )
    )


class MyCloudPowerButton(CoordinatorEntity[MyCloudDataUpdateCoordinator], ButtonEntity):
    """A user-triggered NAS power action."""

    entity_description: ButtonEntityDescription

    def __init__(
        self,
        coordinator: MyCloudDataUpdateCoordinator,
        device: DeviceInfo,
        serial: str,
        description: ButtonEntityDescription,
        action: Literal["reboot", "shutdown"],
    ) -> None:
        """Initialize one NAS-scoped power button."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device
        self._action = action

    async def async_press(self) -> None:
        """Run the power action only in response to an explicit press."""
        try:
            await self.coordinator.async_execute_power_action(self._action)
        except PowerActionBlockedError as error:
            raise HomeAssistantError(
                f"NAS {self.entity_description.key} was blocked by its safety check"
            ) from error
        except PowerActionInProgressError as error:
            raise HomeAssistantError(
                "A NAS power action is already in progress"
            ) from error
        except PowerActionUnavailableError as error:
            raise HomeAssistantError(
                "The authenticated NAS session is unavailable"
            ) from error
        except (TimeoutError, ConnectionError, aiohttp.ClientError) as error:
            raise HomeAssistantError(
                f"NAS {self.entity_description.key} outcome is unknown; the action was "
                "not retried"
            ) from error
        except Exception as error:
            _LOGGER.error(
                "NAS %s action failed (%s)",
                self.entity_description.key,
                type(error).__name__,
            )
            raise HomeAssistantError(
                f"NAS {self.entity_description.key} action failed"
            ) from error
