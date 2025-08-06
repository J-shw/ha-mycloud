from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .api import IntegrationBlueprintApiClient
from .const import DOMAIN

PLATFORMS = ["sensor"]  # Add other platforms you support

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up your integration from a config entry."""

    # Get credentials or token from config entry
    if "session_token" in entry.data:
        session_token = entry.data["session_token"]
        username = None
        password = None
    else:
        username = entry.data[CONF_USERNAME]
        password = entry.data[CONF_PASSWORD]
        session_token = None

    host = entry.data[CONF_HOST]

    # Create API client instance using your PyPI module
    api_client = IntegrationBlueprintApiClient(hass, username, password, host)

    # Initialize the client (login will happen here)
    # await api_client.async_initialize_client() # No longer required

    # Store the API client in hass.data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = api_client

    # Forward the setup to the platforms (e.g., sensor)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True