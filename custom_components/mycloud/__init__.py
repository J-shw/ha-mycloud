import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from .const import DOMAIN, HOST, USERNAME, PASSWORD


_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.info("Setting up My Cloud integration")

    host = entry.data["Host"]
    username = entry.data["Username"]
    password = entry.data["Password"]
    hass.data.setdefault(DOMAIN, {})[HOST] = host
    hass.data.setdefault(DOMAIN, {})[USERNAME] = username
    hass.data.setdefault(DOMAIN, {})[PASSWORD] = password

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    return True