import logging
from datetime import timedelta
from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed, CoordinatorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from wdnas_client import client as nas_client

from .const import DOMAIN


_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=600)

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Set up the WD My Cloud sensor platform."""
    host = config_entry.data["Host"]
    username = config_entry.data["Username"]
    password = config_entry.data["Password"]

    client = nas_client(username, password, host)
    
    await client.__aenter__()

    async def async_update_data():
        """Fetch data from the device."""
        try:
            data = {
                "system_info": await client.system_info(),
                "share_names": await client.share_names(),
                "system_status": await client.system_status(),
                "network_info": await client.network_info(),
                "device_info": await client.device_info(),
                "system_version": await client.system_version(),
                "latest_version": await client.latest_version(),
                "accounts": await client.accounts(),
                "alerts": await client.alerts(),
            }
            return data
        except Exception as err:
            _LOGGER.error("Error fetching data: %s", err)
            raise UpdateFailed(f"Error fetching data: {err}")

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="mycloud_coordinator",
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    await coordinator.async_refresh()

    device_info_data = coordinator.data["device_info"]
    system_version_data = coordinator.data["system_version"]
    serial_number = device_info_data["serial_number"]
    device_name = device_info_data["name"]

    device = DeviceInfo(
        identifiers={(DOMAIN, serial_number)},
        name=device_name,
        manufacturer="Western Digital",
        model=device_info_data["description"],
        sw_version=system_version_data["firmware"]
    )

    sensors_to_add = [
        MyCloudCPUSensor(coordinator, device, serial_number, device_name),
        MyCloudMemorySensor(coordinator, device, serial_number, device_name)
    ]

    disks = coordinator.data["system_info"]["disks"]
    for disk in disks:
        disk_serial = disk["sn"]
        disk_name = f"{device_name} Disk {disk['name']}"
        disk_model = disk["model"]

        disk_device = DeviceInfo(
            identifiers={(DOMAIN, disk_serial)},
            name=disk_name,
            manufacturer="Western Digital",
            model=disk_model,
            sw_version=system_version_data["firmware"],
            hw_version=disk["rev"],
            via_device=(DOMAIN, serial_number)
        )

        sensors_to_add.extend([
            MyCloudDiskTempSensor(coordinator, disk_device, disk_serial, disk_name, disk),
            MyCloudDiskHealthySensor(coordinator, disk_device, disk_serial, disk_name, disk),
            MyCloudDiskSleepSensor(coordinator, disk_device, disk_serial, disk_name, disk),
            MyCloudDiskFailedSensor(coordinator, disk_device, disk_serial, disk_name, disk),
            MyCloudDiskoverTempSensor(coordinator, disk_device, disk_serial, disk_name, disk)
        ])

    async_add_entities(sensors_to_add, True)

    hass.data[DOMAIN]["client_cleanup"] = client.__aexit__


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload a config entry."""
    client_cleanup = hass.data[DOMAIN].get("client_cleanup")
    if client_cleanup:
        await client_cleanup(None, None, None)
    return True

class MyCloudSensorBase(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, device_info, serial_number, device_name, key, name, unit=None, device_class=None):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_{key}"
        self._attr_name = f"{device_name} {name}"
        self._attr_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT

class MyCloudCPUSensor(MyCloudSensorBase):
    def __init__(self, coordinator, device_info, serial_number, device_name):
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            device_name,
            "cpu_usage",
            "CPU Usage",
            unit="%"
        )
        self._attr_icon = "mdi:cpu-64-bit"

    @property
    def state(self):
        return self.coordinator.data["system_status"]["cpu"]

class MyCloudMemorySensor(MyCloudSensorBase):
    def __init__(self, coordinator, device_info, serial_number, device_name):
        super().__init__(
            coordinator,
            device_info,
            serial_number,
            device_name,
            "memory_usage",
            "Memory Usage",
            unit="%"
        )
        self._attr_icon = "mdi:memory"

    @property
    def state(self):
        mem_data = self.coordinator.data["system_status"]["memory"]
        total = mem_data["total"]
        used = total - mem_data["unused"]
        if total > 0:
            return round((used / total) * 100, 2)
        return None

class MyCloudDiskTempSensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_unit_of_measurement = "°C"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator, device_info, serial_number, disk_name, disk):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_disk_temp"
        self._attr_name = f"{disk_name} Temperature"
        self._disk_name = disk['name']

    @property
    def state(self):
        disks = self.coordinator.data["system_info"]["disks"]
        for disk in disks:
            if disk["name"] == self._disk_name:
                return disk["temp"]
        return None
    
class MyCloudDiskHealthySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_icon = "mdi:shield-check"
    def __init__(self, coordinator, device_info, serial_number, disk_name, disk):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_disk_healthy"
        self._attr_name = f"{disk_name} Healthy"
        self._disk_name = disk['name']

    @property
    def is_on(self):
        disks = self.coordinator.data["system_info"]["disks"]
        for disk in disks:
            if disk["name"] == self._disk_name:
                return disk["healthy"]
        return False

class MyCloudDiskSleepSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_icon = "mdi:sleep"
    def __init__(self, coordinator, device_info, serial_number, disk_name, disk):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_disk_sleep"
        self._attr_name = f"{disk_name} Sleeping"
        self._disk_name = disk['name']

    @property
    def is_on(self):
        disks = self.coordinator.data["system_info"]["disks"]
        for disk in disks:
            if disk["name"] == self._disk_name:
                return disk["sleep"]
        return False

class MyCloudDiskFailedSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_icon = "mdi:alert"
    def __init__(self, coordinator, device_info, serial_number, disk_name, disk):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_disk_failed"
        self._attr_name = f"{disk_name} Failed"
        self._disk_name = disk['name']

    @property
    def is_on(self):
        disks = self.coordinator.data["system_info"]["disks"]
        for disk in disks:
            if disk["name"] == self._disk_name:
                return disk["failed"]
        return False

class MyCloudDiskoverTempSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_icon = "mdi:thermometer-alert"
    def __init__(self, coordinator, device_info, serial_number, disk_name, disk):
        super().__init__(coordinator)
        self._attr_device_info = device_info
        self._attr_unique_id = f"{serial_number}_disk_over_temp"
        self._attr_name = f"{disk_name} Over Temperature"
        self._disk_name = disk['name']

    @property
    def is_on(self):
        disks = self.coordinator.data["system_info"]["disks"]
        for disk in disks:
            if disk["name"] == self._disk_name:
                return disk["over_temp"]
        return False