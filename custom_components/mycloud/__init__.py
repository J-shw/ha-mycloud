"""The My Cloud integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant

from .const import PLATFORMS, VERSION
from .coordinator import MyCloudDataUpdateCoordinator
from .session import take_reauth_client


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up My Cloud from a config entry."""
    # Only V4 uses the newer verified-session handoff.  OS 2/5 retain the
    # historical config-flow close followed by a fresh coordinator login.
    reauth_client = (
        take_reauth_client(hass, entry.entry_id) if entry.data[VERSION] == 4 else None
    )
    coordinator = MyCloudDataUpdateCoordinator(
        hass,
        entry,
        client=reauth_client,
        authenticated=reauth_client is not None,
    )

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_shutdown()
        raise

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    entry.async_on_unload(coordinator.async_shutdown)

    async def _async_shutdown_on_stop(_event) -> None:
        await coordinator.async_shutdown()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_shutdown_on_stop)
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload a config entry after its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
