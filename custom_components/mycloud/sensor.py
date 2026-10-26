"""Sensor platform for the My Cloud integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .binary_sensor import legacy_sensor_entities
from .const import VERSION
from .coordinator import MyCloudDataUpdateCoordinator
from .entity import (
    disk_device_info,
    find_system_volume,
    main_device_info,
    resolve_volume_unique_id,
    stable_raid_token,
    volume_device_info,
)

WARNING_OPTIONS = ["normal", "over_temperature", "system_busy_hotplug", "unknown"]
WARNING_STATUS_BY_CODE = {
    0: "normal",
    1: "over_temperature",
    2: "system_busy_hotplug",
}
RAID_STATE_OPTIONS = [
    "clean",
    "degraded",
    "damaged",
    "resync",
    "resync5",
    "recovery",
    "migrate",
    "reshape",
    "expansion",
    "unknown",
]

OS5_SYSTEM_DESCRIPTIONS = (
    SensorEntityDescription(
        key="process_count",
        name="Process Count",
        icon="mdi:application-cog-outline",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="memory_cached",
        name="Memory Cached",
        icon="mdi:memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.KIBIBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="memory_buffers",
        name="Memory Buffers",
        icon="mdi:memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.KIBIBYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="system_warning",
        name="System Warning",
        icon="mdi:alert-circle-outline",
        device_class=SensorDeviceClass.ENUM,
        options=WARNING_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

RAID_DESCRIPTIONS = (
    SensorEntityDescription(
        key="state",
        name="RAID State",
        icon="mdi:harddisk-plus",
        device_class=SensorDeviceClass.ENUM,
        options=RAID_STATE_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="mode",
        name="RAID Mode",
        icon="mdi:harddisk-plus",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

FILESYSTEM_DESCRIPTION = SensorEntityDescription(
    key="filesystem",
    name="File System",
    icon="mdi:file-tree-outline",
    entity_category=EntityCategory.DIAGNOSTIC,
)

V4_STATE_DESCRIPTIONS = (
    SensorEntityDescription(
        key="status",
        name="System Status",
        icon="mdi:nas",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="temperature",
        name="Temperature Health",
        icon="mdi:thermometer-check",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

STORAGE_USAGE_DESCRIPTION = SensorEntityDescription(
    key="storage_usage",
    name="Storage Usage",
    icon="mdi:percent",
    native_unit_of_measurement=PERCENTAGE,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the existing My Cloud sensors from coordinator data."""
    coordinator: MyCloudDataUpdateCoordinator = config_entry.runtime_data
    data = coordinator.data
    device_data = data["device_info"]
    serial_number = device_data["serial_number"]
    device_name = device_data["name"]
    device = main_device_info(data)

    version = config_entry.data[VERSION]
    if version == 4:
        entities: list[SensorEntity] = [
            MyCloudV4StateSensor(coordinator, device, serial_number, description)
            for description in V4_STATE_DESCRIPTIONS
        ]
        if data["system_info"].get("size", {}).get("total") is not None:
            entities.append(
                MyCloudTotalStorageSensor(
                    coordinator, device, serial_number, device_name
                )
            )
        entities.extend(
            (
                MyCloudUsedStorageSensor(
                    coordinator, device, serial_number, device_name
                ),
                MyCloudUnusedStorageSensor(
                    coordinator, device, serial_number, device_name
                ),
                MyCloudStorageUsageSensor(
                    coordinator, device, serial_number, device_name
                ),
            )
        )
    else:
        entities = [
            MyCloudCPUSensor(coordinator, device, serial_number, device_name),
            MyCloudMemorySensor(coordinator, device, serial_number, device_name),
            MyCloudTotalStorageSensor(coordinator, device, serial_number, device_name),
            MyCloudUsedStorageSensor(coordinator, device, serial_number, device_name),
            MyCloudUnusedStorageSensor(coordinator, device, serial_number, device_name),
        ]

        for disk in data["system_info"]["disks"]:
            disk_name = f"{device_name} Disk {disk['name']}"
            disk_device = disk_device_info(hass, config_entry, data, disk)
            entities.extend(
                (
                    MyCloudDiskTempSensor(
                        coordinator, disk_device, disk["sn"], disk_name, disk
                    ),
                    MyCloudDiskSizeSensor(
                        coordinator, disk_device, disk["sn"], disk_name, disk
                    ),
                )
            )

        for volume in data["system_info"]["volumes"]:
            volume_name = f"{device_name} {volume['label']}"
            legacy_unique_id = f"{volume['id']}_volume_size"
            unique_id = resolve_volume_unique_id(
                hass,
                config_entry,
                "sensor",
                legacy_unique_id,
                f"{serial_number}_{legacy_unique_id}",
            )
            entities.append(
                MyCloudVolumeSizeSensor(
                    coordinator,
                    volume_device_info(hass, config_entry, data, volume),
                    volume_name,
                    volume,
                    unique_id,
                )
            )

    if version == 5:
        entities.extend(
            MyCloudOS5SystemSensor(
                coordinator, device, serial_number, device_name, description
            )
            for description in OS5_SYSTEM_DESCRIPTIONS
        )

        diagnostics = data.get("diagnostics") or {}
        raid_volumes = diagnostics.get("raid", {}).get("volumes", [])
        for index, raid_volume in enumerate(raid_volumes):
            token = stable_raid_token(raid_volume, index)
            system_volume = find_system_volume(
                data, raid_volume.get("volume") or raid_volume.get("id")
            )
            raid_device = (
                volume_device_info(hass, config_entry, data, system_volume)
                if system_volume is not None
                else device
            )
            for description in RAID_DESCRIPTIONS:
                entities.append(
                    MyCloudRAIDSensor(
                        coordinator,
                        raid_device,
                        serial_number,
                        device_name,
                        token,
                        description,
                    )
                )

        for index, status in enumerate(data.get("volume_status") or []):
            reference = status.get("volume") or index
            system_volume = find_system_volume(data, reference)
            status_device = (
                volume_device_info(hass, config_entry, data, system_volume)
                if system_volume is not None
                else device
            )
            token = stable_raid_token({"id": reference}, index)
            entities.append(
                MyCloudFilesystemSensor(
                    coordinator,
                    status_device,
                    serial_number,
                    device_name,
                    token,
                )
            )

    entities.extend(legacy_sensor_entities(hass, config_entry))
    async_add_entities(entities)


class MyCloudV4StateSensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], SensorEntity
):
    """Expose one qualitative V4 state without converting it to a measurement."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device_info, serial, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial}_v4_{description.key}"
        self._attr_name = description.name

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get(
            "optional_available", {}
        ).get("system_status", False)

    @property
    def native_value(self):
        return self.coordinator.data.get("system_status", {}).get(
            self.entity_description.key
        )


class MyCloudOS5SystemSensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], SensorEntity
):
    """Expose safe OS 5 values from shared coordinator data."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, device_info, serial, device_name, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_name = description.name

    @property
    def available(self) -> bool:
        """Require the warning endpoint only for its derived entity."""
        if self.entity_description.key == "system_warning":
            return super().available and self.coordinator.data.get(
                "optional_available", {}
            ).get("system_warning", False)
        return super().available

    @property
    def native_value(self):
        """Return one normalized OS 5 system value."""
        key = self.entity_description.key
        status = self.coordinator.data.get("system_status", {})
        if key == "process_count":
            return status.get("process_count")
        if key == "memory_cached":
            return status.get("memory", {}).get("cached")
        if key == "memory_buffers":
            return status.get("memory", {}).get("buffers")
        warning = self.coordinator.data.get("system_warning") or {}
        return WARNING_STATUS_BY_CODE.get(warning.get("code"), "unknown")


class MyCloudRAIDSensor(CoordinatorEntity[MyCloudDataUpdateCoordinator], SensorEntity):
    """Expose one safe field for one OS 5 RAID volume."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator, device_info, serial, device_name, token, description
    ):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_device_info = device_info
        self._token = token
        self._attr_unique_id = f"{serial}_raid_{token}_{description.key}"
        self._attr_name = f"RAID {token} {description.name}"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get(
            "optional_available", {}
        ).get("diagnostics", False)

    def _volume(self) -> dict | None:
        volumes = (
            (self.coordinator.data.get("diagnostics") or {})
            .get("raid", {})
            .get("volumes", [])
        )
        for index, volume in enumerate(volumes):
            if stable_raid_token(volume, index) == self._token:
                return volume
        return None

    @property
    def native_value(self):
        volume = self._volume()
        if volume is None:
            return None
        value = volume.get(self.entity_description.key)
        if self.entity_description.key == "state":
            normalized = str(value).lower() if value is not None else "unknown"
            return normalized if normalized in RAID_STATE_OPTIONS else "unknown"
        return value


class MyCloudFilesystemSensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], SensorEntity
):
    """Expose the file-system type without leaking volume internals."""

    _attr_has_entity_name = True
    entity_description = FILESYSTEM_DESCRIPTION

    def __init__(self, coordinator, device_info, serial, device_name, token):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._token = token
        self._attr_unique_id = f"{serial}_volume_{token}_filesystem"
        self._attr_name = "File System"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.get(
            "optional_available", {}
        ).get("volume_status", False)

    @property
    def native_value(self):
        for index, volume in enumerate(
            self.coordinator.data.get("volume_status") or []
        ):
            token = stable_raid_token({"id": volume.get("volume") or index}, index)
            if token == self._token:
                return volume.get("file_type")
        return None


class MyCloudSensorBase(CoordinatorEntity[MyCloudDataUpdateCoordinator], SensorEntity):
    """Base class retaining the existing system entity identity."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: MyCloudDataUpdateCoordinator,
        device_info: DeviceInfo,
        serial_number: str,
        device_name: str,
        key: str,
        name: str,
        unit: str | None = None,
        device_class: SensorDeviceClass | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_{key}"
        self._attr_name = f"{device_name} {name}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class


class MyCloudCPUSensor(MyCloudSensorBase):
    """Existing CPU usage sensor."""

    _attr_icon = "mdi:cpu-64-bit"

    def __init__(self, coordinator, device_info, serial_number, device_name) -> None:
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            device_name,
            "cpu_usage",
            "CPU Usage",
            unit="%",
        )

    @property
    def native_value(self):
        """Return CPU usage."""
        return self.coordinator.data["system_status"]["cpu"]


class MyCloudMemorySensor(MyCloudSensorBase):
    """Existing memory usage sensor."""

    _attr_icon = "mdi:memory"

    def __init__(self, coordinator, device_info, serial_number, device_name) -> None:
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            device_name,
            "memory_usage",
            "Memory Usage",
            unit="%",
        )

    @property
    def native_value(self):
        """Return the existing memory calculation unchanged."""
        memory = self.coordinator.data["system_status"]["memory"]
        total = memory["total"]
        if total > 0:
            return round(((total - memory["unused"]) / total) * 100, 2)
        return None


class MyCloudStorageSensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], SensorEntity
):
    """Base for existing system storage sensors."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.TERABYTES

    def __init__(
        self, coordinator, device_info, serial_number, device_name, key, name, icon
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_{key}"
        self._attr_name = f"{device_name} {name}"
        self._attr_icon = icon
        self._size_key = key.removesuffix("_storage")

    @property
    def native_value(self):
        """Return the existing storage value."""
        value = self.coordinator.data["system_info"]["size"].get(self._size_key)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class MyCloudTotalStorageSensor(MyCloudStorageSensor):
    def __init__(self, coordinator, device_info, serial_number, device_name) -> None:
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            device_name,
            "total_storage",
            "Total Storage",
            "mdi:database",
        )


class MyCloudUsedStorageSensor(MyCloudStorageSensor):
    def __init__(self, coordinator, device_info, serial_number, device_name) -> None:
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            device_name,
            "used_storage",
            "Used Storage",
            "mdi:database-minus",
        )


class MyCloudUnusedStorageSensor(MyCloudStorageSensor):
    def __init__(self, coordinator, device_info, serial_number, device_name) -> None:
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            device_name,
            "unused_storage",
            "Unused Storage",
            "mdi:database-plus",
        )


class MyCloudStorageUsageSensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], SensorEntity
):
    """Expose the V4 byte-derived storage utilization without rounding it."""

    entity_description = STORAGE_USAGE_DESCRIPTION

    def __init__(self, coordinator, device_info, serial_number, device_name) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_storage_usage"
        self._attr_name = f"{device_name} {self.entity_description.name}"

    @property
    def available(self) -> bool:
        return super().available and bool(
            self.coordinator.data.get("system_info", {}).get(
                "storage_usage_available", False
            )
        )

    @property
    def native_value(self):
        return (
            self.coordinator.data.get("system_info", {})
            .get("size", {})
            .get("used_percentage")
        )


class MyCloudDiskTempSensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], SensorEntity
):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer"

    def __init__(
        self, coordinator, device_info, serial_number, disk_name, disk
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_disk_temp"
        self._attr_name = f"{disk_name} Temperature"
        self._disk_name = disk["name"]

    @property
    def native_value(self):
        """Return disk temperature, or unknown for malformed optional data."""
        for disk in self.coordinator.data.get("system_info", {}).get("disks", []):
            if disk.get("name") == self._disk_name:
                try:
                    return float(disk.get("temp"))
                except (TypeError, ValueError):
                    return None
        return None


class MyCloudDiskSizeSensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], SensorEntity
):
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.TERABYTES
    _attr_icon = "mdi:harddisk"

    def __init__(
        self, coordinator, device_info, serial_number, disk_name, disk
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_disk_size"
        self._attr_name = f"{disk_name} Size"
        self._disk_name = disk["name"]

    @property
    def native_value(self):
        """Return disk size."""
        for disk in self.coordinator.data.get("system_info", {}).get("disks", []):
            if disk.get("name") == self._disk_name:
                try:
                    return int(disk.get("size"))
                except (TypeError, ValueError):
                    return None
        return None


class MyCloudVolumeSizeSensor(
    CoordinatorEntity[MyCloudDataUpdateCoordinator], SensorEntity
):
    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfInformation.BYTES
    _attr_suggested_unit_of_measurement = UnitOfInformation.TERABYTES
    _attr_icon = "mdi:harddisk"

    def __init__(
        self, coordinator, device_info, volume_name, volume, unique_id
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = unique_id
        self._attr_name = f"{volume_name} Size"
        self._volume_id = volume["id"]

    @property
    def native_value(self):
        """Return volume size."""
        volumes = self.coordinator.data.get("system_info", {}).get("volumes", [])
        for volume in volumes:
            if volume.get("id") == self._volume_id:
                try:
                    return int(volume.get("size"))
                except (TypeError, ValueError):
                    return None
        return None
