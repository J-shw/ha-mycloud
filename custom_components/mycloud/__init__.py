import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from .const import DOMAIN, CONF_API_KEY

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    _LOGGER.info("Setting up AirVPN integration")

    api_key = entry.data["api_key"]
    hass.data.setdefault(DOMAIN, {})[CONF_API_KEY] = api_key

    await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])

    return True