"""Short-lived ownership transfer for a verified reauthentication session."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_HANDOFF_KEY = "reauth_client_handoffs"
_HANDOFF_TIMEOUT = 60


class ReauthHandoffCleanupError(RuntimeError):
    """A previously verified session could not be safely released."""


@dataclass
class _ReauthClientHandoff:
    """An authenticated client waiting for the scheduled entry reload."""

    client: Any
    cleanup_task: asyncio.Task | None = None
    claimed: bool = False


def _handoffs(hass: HomeAssistant) -> dict[str, _ReauthClientHandoff]:
    """Return the integration-owned handoff registry."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(_HANDOFF_KEY, {})


def store_reauth_client(hass: HomeAssistant, entry_id: str, client: Any) -> None:
    """Store one verified client until the scheduled reload consumes it."""
    handoffs = _handoffs(hass)
    handoff = _ReauthClientHandoff(client)

    async def _expire() -> None:
        try:
            await asyncio.sleep(_HANDOFF_TIMEOUT)
        except asyncio.CancelledError:
            if handoff.claimed:
                return
        current = handoffs.get(entry_id)
        if current is not handoff:
            return
        try:
            await asyncio.shield(client.close())
        except Exception as error:  # noqa: BLE001 - cleanup must remain isolated
            _LOGGER.warning(
                "Expired NAS reauthentication session cleanup failed (%s)",
                type(error).__name__,
            )
        else:
            handoffs.pop(entry_id, None)

    handoffs[entry_id] = handoff
    handoff.cleanup_task = hass.async_create_task(
        _expire(), f"{DOMAIN} reauthentication session expiry"
    )


def take_reauth_client(hass: HomeAssistant, entry_id: str) -> Any | None:
    """Transfer ownership of a verified client to the new coordinator."""
    handoff = _handoffs(hass).pop(entry_id, None)
    if handoff is None:
        return None
    handoff.claimed = True
    if handoff.cleanup_task is not None:
        handoff.cleanup_task.cancel()
    return handoff.client


async def async_discard_reauth_client(hass: HomeAssistant, entry_id: str) -> None:
    """Release a stale handoff before another reauthentication attempt."""
    handoffs = _handoffs(hass)
    handoff = handoffs.pop(entry_id, None)
    if handoff is None:
        return
    handoff.claimed = True
    if handoff.cleanup_task is not None:
        handoff.cleanup_task.cancel()
    try:
        await handoff.client.close()
    except Exception as error:
        _LOGGER.warning(
            "Stale NAS reauthentication session cleanup failed (%s)",
            type(error).__name__,
        )
        handoff.claimed = False
        handoffs[entry_id] = handoff
        raise ReauthHandoffCleanupError from error
