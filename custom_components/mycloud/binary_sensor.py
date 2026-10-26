"""Binary sensor platform for the My Cloud integration."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import VERSION
from .coordinator import MyCloudDataUpdateCoordinator
from .entity import (
    disk_device_info,
    find_system_volume,
    main_device_info,
    owned_entity_id,
    resolve_volume_unique_id,
    stable_raid_token,
    volume_device_info,
)

_LOGGER = logging.getLogger(__name__)

OVER_TEMPERATURE_DESCRIPTION = BinarySensorEntityDescription(
    key="over_temperature",
    name="Over Temperature",
    icon="mdi:thermometer-alert",
    device_class=BinarySensorDeviceClass.PROBLEM,
    entity_category=EntityCategory.DIAGNOSTIC,
)
INTERNET_ACCESS_DESCRIPTION = BinarySensorEntityDescription(
    key="internet_access",
    name="Internet Access",
    icon="mdi:web-check",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)
RAID_PROBLEM_DESCRIPTION = BinarySensorEntityDescription(
    key="problem",
    name="RAID Problem",
    icon="mdi:harddisk-alert",
    device_class=BinarySensorDeviceClass.PROBLEM,
    entity_category=EntityCategory.DIAGNOSTIC,
)
V4_HEALTH_DESCRIPTIONS = (
    BinarySensorEntityDescription(
        key="smart",
        name="SMART Problem",
        icon="mdi:harddisk-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="volume",
        name="Volume Problem",
        icon="mdi:database-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="free_space",
        name="Free Space Problem",
        icon="mdi:database-alert",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

RAID_OK_STATES = {
    "clean",
    "resync",
    "resync5",
    "recovery",
    "migrate",
    "reshape",
    "expansion",
}
RAID_PROBLEM_STATES = {"degraded", "damaged"}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the existing My Cloud binary sensors."""
    async_add_entities(_build_binary_entities(hass, config_entry, legacy_only=False))


def legacy_sensor_entities(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> list[BinarySensorEntity]:
    """Return binary entities that must retain historical sensor-domain IDs."""
    return _build_binary_entities(hass, config_entry, legacy_only=True)


def _select_unique_id(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    legacy_unique_id: str,
    scoped_unique_id: str,
    *,
    legacy_only: bool,
) -> str | None:
    """Route one entity to its legacy or proper platform without mutation."""
    legacy_entity_id = owned_entity_id(hass, config_entry, "sensor", legacy_unique_id)
    binary_entity_id = owned_entity_id(
        hass, config_entry, "binary_sensor", legacy_unique_id
    ) or owned_entity_id(hass, config_entry, "binary_sensor", scoped_unique_id)

    if legacy_entity_id is not None and binary_entity_id is not None:
        _LOGGER.error(
            "Conflicting legacy and binary registry entries exist for a My Cloud "
            "entity; neither registration will be loaded automatically"
        )
        return None
    if legacy_only:
        return legacy_unique_id if legacy_entity_id is not None else None
    if legacy_entity_id is not None:
        return None
    return resolve_volume_unique_id(
        hass,
        config_entry,
        "binary_sensor",
        legacy_unique_id,
        scoped_unique_id,
    )


def _build_binary_entities(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    *,
    legacy_only: bool,
) -> list[BinarySensorEntity]:
    """Build current or non-destructive legacy binary entities."""
    coordinator: MyCloudDataUpdateCoordinator = config_entry.runtime_data
    data = coordinator.data
    device_data = data["device_info"]
    device_name = device_data["name"]
    serial_number = device_data["serial_number"]
    entities: list[BinarySensorEntity] = []

    for disk in data["system_info"]["disks"]:
        disk_name = f"{device_name} Disk {disk['name']}"
        disk_device = disk_device_info(hass, config_entry, data, disk)
        for entity_class, suffix in (
            (MyCloudDiskHealthySensor, "healthy"),
            (MyCloudDiskSleepSensor, "sleep"),
            (MyCloudDiskFailedSensor, "failed"),
            (MyCloudDiskOverTempSensor, "over_temp"),
        ):
            unique_id = f"{disk['sn']}_disk_{suffix}"
            selected_unique_id = _select_unique_id(
                hass,
                config_entry,
                unique_id,
                unique_id,
                legacy_only=legacy_only,
            )
            if selected_unique_id is not None:
                entities.append(
                    entity_class(coordinator, disk_device, disk["sn"], disk_name, disk)
                )

    for volume in data["system_info"]["volumes"]:
        volume_name = f"{device_name} {volume['label']}"
        volume_device = volume_device_info(hass, config_entry, data, volume)
        for entity_class, suffix in (
            (MyCloudVolumeMountedSensor, "mounted"),
            (MyCloudVolumeUnlockedSensor, "unlocked"),
            (MyCloudVolumeEncryptedSensor, "encrypted"),
        ):
            legacy_unique_id = f"{volume_name}_volume_{suffix}"
            unique_id = _select_unique_id(
                hass,
                config_entry,
                legacy_unique_id,
                f"{serial_number}_{volume['id']}_volume_{suffix}",
                legacy_only=legacy_only,
            )
            if unique_id is not None:
                entities.append(
                    entity_class(
                        coordinator, volume_device, volume_name, volume, unique_id
                    )
                )

    if not legacy_only and config_entry.data[VERSION] == 5:
        main_device = main_device_info(data)
        entities.extend(
            (
                MyCloudOS5BinarySensor(
                    coordinator,
                    main_device,
                    serial_number,
                    device_name,
                    OVER_TEMPERATURE_DESCRIPTION,
                ),
                MyCloudOS5BinarySensor(
                    coordinator,
                    main_device,
                    serial_number,
                    device_name,
                    INTERNET_ACCESS_DESCRIPTION,
                ),
            )
        )
        diagnostics = data.get("diagnostics") or {}
        for index, raid_volume in enumerate(
            diagnostics.get("raid", {}).get("volumes", [])
        ):
            token = stable_raid_token(raid_volume, index)
            system_volume = find_system_volume(
                data, raid_volume.get("volume") or raid_volume.get("id")
            )
            raid_device = (
                volume_device_info(hass, config_entry, data, system_volume)
                if system_volume is not None
                else main_device
            )
            entities.append(
                MyCloudRAIDProblemSensor(
                    coordinator,
                    raid_device,
                    serial_number,
                    device_name,
                    token,
                )
            )

    if not legacy_only and config_entry.data[VERSION] == 4:
        main_device = main_device_info(data)
        entities.extend(
            MyCloudV4HealthBinarySensor(
                coordinator,
                main_device,
                serial_number,
                description,
            )
            for description in V4_HEALTH_DESCRIPTIONS
        )
        entities.append(
            MyCloudOS5BinarySensor(
                coordinator,
                main_device,
                serial_number,
                device_name,
                INTERNET_ACCESS_DESCRIPTION,
            )
        )

    return entities


class MyCloudV4HealthBinarySensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], BinarySensorEntity
):
    """Expose one qualitative V4 health field as a problem indicator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device_info, serial, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial}_v4_{description.key}_problem"
        self._attr_name = description.name

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get(
            "optional_available", {}
        ).get("system_status", False)

    @property
    def is_on(self):
        value = self.coordinator.data.get("system_status", {}).get(
            self.entity_description.key
        )
        if value is None:
            return None
        return str(value).strip().casefold() != "good"


class MyCloudOS5BinarySensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], BinarySensorEntity
):
    """Expose one tri-state OS 5 system condition."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device_info, serial, device_name, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_name = description.name

    @property
    def available(self) -> bool:
        endpoint = (
            "system_warning"
            if self.entity_description.key == "over_temperature"
            else "internet_access"
        )
        return super().available and self.coordinator.data.get(
            "optional_available", {}
        ).get(endpoint, False)

    @property
    def is_on(self):
        if self.entity_description.key == "internet_access":
            value = self.coordinator.data.get("internet_access")
            return value if isinstance(value, bool) else None

        warning = self.coordinator.data.get("system_warning") or {}
        code = warning.get("code")
        if code == 1:
            return True
        if code in (0, 2):
            return False
        return None


class MyCloudRAIDProblemSensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], BinarySensorEntity
):
    """Report only confirmed RAID failures as problems."""

    _attr_has_entity_name = True
    entity_description = RAID_PROBLEM_DESCRIPTION

    def __init__(self, coordinator, device_info, serial, device_name, token):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._token = token
        self._attr_unique_id = f"{serial}_raid_{token}_problem"
        self._attr_name = f"RAID {token} Problem"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get(
            "optional_available", {}
        ).get("diagnostics", False)

    @property
    def is_on(self):
        volumes = (
            (self.coordinator.data.get("diagnostics") or {})
            .get("raid", {})
            .get("volumes", [])
        )
        for index, volume in enumerate(volumes):
            if stable_raid_token(volume, index) != self._token:
                continue
            state = str(volume.get("state", "")).lower()
            if state in RAID_PROBLEM_STATES:
                return True
            if state in RAID_OK_STATES:
                return False
            return None
        return None


class MyCloudDiskBinarySensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], BinarySensorEntity
):
    """Base class retaining existing disk binary-sensor identities."""

    def __init__(
        self,
        coordinator,
        device_info,
        serial_number,
        disk_name,
        disk,
        suffix,
        name,
        icon,
        field,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_disk_{suffix}"
        self._attr_name = f"{disk_name} {name}"
        self._attr_icon = icon
        self._disk_name = disk["name"]
        self._field = field

    @property
    def is_on(self):
        """Return the existing disk boolean value."""
        for disk in self.coordinator.data.get("system_info", {}).get("disks", []):
            if disk.get("name") == self._disk_name:
                return disk.get(self._field)
        return None


class MyCloudDiskHealthySensor(MyCloudDiskBinarySensor):
    def __init__(
        self, coordinator, device_info, serial_number, disk_name, disk
    ) -> None:
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            disk_name,
            disk,
            "healthy",
            "Healthy",
            "mdi:shield-check",
            "healthy",
        )


class MyCloudDiskSleepSensor(MyCloudDiskBinarySensor):
    def __init__(
        self, coordinator, device_info, serial_number, disk_name, disk
    ) -> None:
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            disk_name,
            disk,
            "sleep",
            "Sleeping",
            "mdi:sleep",
            "sleep",
        )


class MyCloudDiskFailedSensor(MyCloudDiskBinarySensor):
    def __init__(
        self, coordinator, device_info, serial_number, disk_name, disk
    ) -> None:
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            disk_name,
            disk,
            "failed",
            "Failed",
            "mdi:alert",
            "failed",
        )


class MyCloudDiskOverTempSensor(MyCloudDiskBinarySensor):
    def __init__(
        self, coordinator, device_info, serial_number, disk_name, disk
    ) -> None:
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            disk_name,
            disk,
            "over_temp",
            "Over Temperature",
            "mdi:thermometer-alert",
            "over_temp",
        )


class MyCloudVolumeBinarySensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], BinarySensorEntity
):
    """Base for the existing volume binary sensors."""

    def __init__(
        self,
        coordinator,
        device_info,
        volume_name,
        volume,
        unique_id,
        name,
        icon,
        field,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_name = f"{volume_name} {name}"
        self._attr_icon = icon
        self._volume_name = volume["name"]
        self._field = field

    @property
    def is_on(self):
        """Return the existing volume boolean value."""
        volumes = self.coordinator.data.get("system_info", {}).get("volumes", [])
        for volume in volumes:
            if volume.get("name") == self._volume_name:
                return volume.get(self._field)
        return None


class MyCloudVolumeMountedSensor(MyCloudVolumeBinarySensor):
    def __init__(
        self, coordinator, device_info, volume_name, volume, unique_id
    ) -> None:
        super().__init__(
            coordinator,
            device_info,
            volume_name,
            volume,
            unique_id,
            "Mounted",
            "mdi:folder-pound",
            "mounted",
        )


class MyCloudVolumeUnlockedSensor(MyCloudVolumeBinarySensor):
    def __init__(
        self, coordinator, device_info, volume_name, volume, unique_id
    ) -> None:
        super().__init__(
            coordinator,
            device_info,
            volume_name,
            volume,
            unique_id,
            "Unlocked",
            "mdi:lock-open",
            "unlocked",
        )


class MyCloudVolumeEncryptedSensor(MyCloudVolumeBinarySensor):
    def __init__(
        self, coordinator, device_info, volume_name, volume, unique_id
    ) -> None:
        super().__init__(
            coordinator,
            device_info,
            volume_name,
            volume,
            unique_id,
            "Encrypted",
            "mdi:lock",
            "encrypted",
        )
