import logging
from datetime import timedelta
from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .wdnas_client import AsyncClient

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=600)

async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry, async_add_entities):
    """Set up the WD My Cloud sensor platform."""
    host = config_entry.data["Host"]
    username = config_entry.data["Username"]
    password = config_entry.data["Password"]

    client = AsyncClient(username, password, host)
    
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

    device = DeviceInfo(
        identifiers={(DOMAIN, device_info_data["serial_number"])},
        name=device_info_data["name"],
        manufacturer="Western Digital",
        model=device_info_data["description"],
        sw_version=coordinator.system_version_data["firmware"]
    )

    sensors_to_add = [
        MyCloudSensor(coordinator, device, "cpu_usage", "CPU Usage"),
        MyCloudSensor(coordinator, device, "memory_usage", "Memory Usage")
    ]

    async_add_entities(sensors_to_add)

    hass.data[DOMAIN]["client_cleanup"] = client.__aexit__


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload a config entry."""
    client_cleanup = hass.data[DOMAIN].get("client_cleanup")
    if client_cleanup:
        await client_cleanup(None, None, None)
    return True

class MyCloudSensor(CoordinatorEntity, SensorEntity):
    """Representation of a WD My Cloud sensor."""
    
    def __init__(self, coordinator, device_info, sensor_type, name):
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_info = device_info
        self._sensor_type = sensor_type
        self._name = f"{device_info['name']} - {name}"
        self._state = None
        self._unit_of_measurement = None

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self):
        """Return a unique ID for the sensor."""
        return f"{self._device_info['identifiers'][0][1]}_{self._sensor_type}"

    @property
    def device_info(self):
        """Return the device info."""
        return self._device_info

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def state_class(self):
        """Return the state class."""
        return SensorStateClass.MEASUREMENT
        
    def _handle_coordinator_update(self):
        """Handle updated data from the coordinator."""
        if self._sensor_type == "cpu_usage":
            cpu_data = self.coordinator.data["system_status"]["cpu"]
            self._state = cpu_data
            self._unit_of_measurement = "%"
            
        elif self._sensor_type == "memory_usage":
            mem_data = self.coordinator.data["system_status"]["memory"]
            total = mem_data["total"]
            used = total - mem_data["unused"]
            if total > 0:
                self._state = round((used / total) * 100, 2)
            else:
                self._state = None
            self._unit_of_measurement = "%"
        
        self.async_write_ha_state()