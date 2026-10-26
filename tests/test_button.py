"""Offline tests for explicit NAS power actions."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mycloud._vendor.wdnas_client.exceptions import (
    PowerActionBlockedError,
)
from custom_components.mycloud.button import (
    RESTART_DESCRIPTION,
    SHUTDOWN_DESCRIPTION,
    MyCloudPowerButton,
)
from custom_components.mycloud.const import DOMAIN
from custom_components.mycloud.coordinator import (
    MyCloudDataUpdateCoordinator,
    PowerActionInProgressError,
)
from custom_components.mycloud.entity import main_device_info


def _entry(version=5, host="nas.local", unique_id="NAS-SERIAL-1"):
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "Host": host,
            "Username": "admin",
            "Password": "secret",
            "Version": version,
        },
        unique_id=unique_id,
    )


async def _setup(hass, entry, client):
    entry.add_to_hass(hass)
    with patch("custom_components.mycloud.coordinator.nas_client", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_os5_mirror_buttons_are_unique_and_only_run_when_pressed(
    hass, make_nas_client
):
    client = make_nas_client()
    entry = _entry()
    await _setup(hass, entry, client)

    client.reboot.assert_not_awaited()
    client.shutdown.assert_not_awaited()
    registry = er.async_get(hass)
    restart = registry.async_get_entity_id("button", DOMAIN, "NAS-SERIAL-1_restart")
    shutdown = registry.async_get_entity_id("button", DOMAIN, "NAS-SERIAL-1_shutdown")
    assert restart is not None
    assert shutdown is not None
    assert restart != shutdown

    await hass.services.async_call(
        "button", "press", {"entity_id": restart}, blocking=True
    )
    client.reboot.assert_awaited_once()
    client.shutdown.assert_not_awaited()

    await hass.services.async_call(
        "button", "press", {"entity_id": shutdown}, blocking=True
    )
    client.shutdown.assert_awaited_once()


async def test_power_platform_logs_safe_client_guard_revision(
    hass, make_nas_client, caplog
):
    caplog.set_level(logging.INFO, logger="custom_components.mycloud.button")
    client = make_nas_client()

    await _setup(hass, _entry(), client)

    assert "api_version=5" in caplog.text
    assert "client_guard=os5-power-open-session-v3" in caplog.text
    assert "nas.local" not in caplog.text
    assert "session-id" not in caplog.text


async def test_live_os5_compact_model_identifier_enables_buttons(
    hass, make_nas_client, nas_data
):
    data = deepcopy(nas_data)
    data["device_info"]["description"] = "WDMyCloudMirror"
    data["system_version"]["firmware"] = "5.33.102"
    client = make_nas_client(data)
    entry = _entry()

    await _setup(hass, entry, client)

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("button", DOMAIN, "NAS-SERIAL-1_restart")
        is not None
    )
    assert (
        registry.async_get_entity_id("button", DOMAIN, "NAS-SERIAL-1_shutdown")
        is not None
    )
    client.reboot.assert_not_awaited()
    client.shutdown.assert_not_awaited()


async def test_v4_sq_buttons_reuse_coordinator_session(hass, make_nas_client, v4_data):
    client = make_nas_client(v4_data)
    entry = _entry(4)
    await _setup(hass, entry, client)

    registry = er.async_get(hass)
    restart = registry.async_get_entity_id("button", DOMAIN, "V4-NAS-SERIAL_restart")
    shutdown = registry.async_get_entity_id("button", DOMAIN, "V4-NAS-SERIAL_shutdown")
    assert restart is not None
    assert shutdown is not None
    client.reboot.assert_not_awaited()
    client.shutdown.assert_not_awaited()

    await hass.services.async_call(
        "button", "press", {"entity_id": restart}, blocking=True
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": shutdown}, blocking=True
    )

    client.reboot.assert_awaited_once()
    client.shutdown.assert_awaited_once()
    client.__aenter__.assert_awaited_once()


def test_power_buttons_are_normal_controls_not_configuration_entities():
    assert RESTART_DESCRIPTION.entity_category is None
    assert SHUTDOWN_DESCRIPTION.entity_category is None


async def test_v4_and_os5_buttons_coexist_with_stable_unique_ids(
    hass, make_nas_client, nas_data, v4_data
):
    os5_data = deepcopy(nas_data)
    os5_data["device_info"]["description"] = "WDMyCloudMirror"
    os5_client = make_nas_client(os5_data)
    v4_client = make_nas_client(v4_data)
    os5_entry = _entry(5, "mirror.local", "OS5-CONFIG-ENTRY")
    v4_entry = _entry(4, "v4.local", "V4-CONFIG-ENTRY")

    await _setup(hass, os5_entry, os5_client)
    await _setup(hass, v4_entry, v4_client)

    registry = er.async_get(hass)
    for unique_id in (
        "NAS-SERIAL-1_restart",
        "NAS-SERIAL-1_shutdown",
        "V4-NAS-SERIAL_restart",
        "V4-NAS-SERIAL_shutdown",
    ):
        assert registry.async_get_entity_id("button", DOMAIN, unique_id) is not None
    os5_client.reboot.assert_not_awaited()
    os5_client.shutdown.assert_not_awaited()
    v4_client.reboot.assert_not_awaited()
    v4_client.shutdown.assert_not_awaited()


async def test_existing_disabled_button_registry_entry_is_preserved(
    hass, make_nas_client
):
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    existing = registry.async_get_or_create(
        "button",
        DOMAIN,
        "NAS-SERIAL-1_restart",
        config_entry=entry,
        suggested_object_id="custom_restart_name",
        disabled_by=er.RegistryEntryDisabler.USER,
        entity_category=EntityCategory.CONFIG,
    )
    client = make_nas_client()

    with patch("custom_components.mycloud.coordinator.nas_client", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    preserved = registry.async_get(existing.entity_id)
    assert preserved is not None
    assert preserved.unique_id == "NAS-SERIAL-1_restart"
    assert preserved.disabled_by is er.RegistryEntryDisabler.USER
    assert preserved.entity_id == existing.entity_id
    assert preserved.entity_category is None
    assert (
        registry.async_get_entity_id("button", DOMAIN, "NAS-SERIAL-1_shutdown")
        is not None
    )


@pytest.mark.parametrize(
    ("model_number", "firmware"),
    [
        ("other", "04.06.00-111"),
        ("sq", "04.05.00-000"),
        (None, "04.06.00-111"),
    ],
)
async def test_unconfirmed_v4_targets_do_not_get_power_buttons(
    hass, make_nas_client, v4_data, model_number, firmware
):
    data = deepcopy(v4_data)
    data["device_info"]["model_number"] = model_number
    data["system_version"]["firmware"] = firmware
    client = make_nas_client(data)
    entry = _entry(4)
    await _setup(hass, entry, client)

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("button", DOMAIN, "V4-NAS-SERIAL_restart") is None
    )
    assert (
        registry.async_get_entity_id("button", DOMAIN, "V4-NAS-SERIAL_shutdown") is None
    )
    client.reboot.assert_not_awaited()
    client.shutdown.assert_not_awaited()


@pytest.mark.parametrize("firmware", ["5.32.0", "5.34.0", None])
async def test_os5_mirror_requires_confirmed_firmware(
    hass, make_nas_client, nas_data, firmware
):
    data = deepcopy(nas_data)
    data["device_info"]["description"] = "WDMyCloudMirror"
    data["system_version"]["firmware"] = firmware
    client = make_nas_client(data)
    entry = _entry()

    await _setup(hass, entry, client)

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("button", DOMAIN, "NAS-SERIAL-1_restart") is None
    )
    assert (
        registry.async_get_entity_id("button", DOMAIN, "NAS-SERIAL-1_shutdown") is None
    )


@pytest.mark.parametrize(
    ("version", "description"),
    [
        (2, "WD My Cloud Mirror"),
        (4, "WD My Cloud"),
        (5, "WD My Cloud EX2 Ultra"),
    ],
)
async def test_unsupported_devices_do_not_get_power_buttons(
    hass, make_nas_client, nas_data, version, description
):
    data = deepcopy(nas_data)
    data["device_info"]["description"] = description
    client = make_nas_client(data)
    entry = _entry(version)
    await _setup(hass, entry, client)

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("button", DOMAIN, "NAS-SERIAL-1_restart") is None
    )
    assert (
        registry.async_get_entity_id("button", DOMAIN, "NAS-SERIAL-1_shutdown") is None
    )
    client.reboot.assert_not_awaited()
    client.shutdown.assert_not_awaited()


async def test_button_action_failure_is_reported_without_credentials(nas_data, caplog):
    entry = _entry()
    entry.runtime_data = type("Coordinator", (), {"data": nas_data})()
    entry.runtime_data.async_execute_power_action = AsyncMock(
        side_effect=RuntimeError("secret should not be logged")
    )
    button = MyCloudPowerButton(
        entry.runtime_data,
        main_device_info(nas_data),
        "NAS-SERIAL-1",
        RESTART_DESCRIPTION,
        "reboot",
    )

    with pytest.raises(HomeAssistantError, match="restart action failed"):
        await button.async_press()
    assert "secret should not be logged" not in caplog.text
    assert "RuntimeError" in caplog.text


async def test_restart_button_delegates_to_coordinator(nas_data):
    coordinator = type("Coordinator", (), {"data": nas_data})()
    coordinator.async_execute_power_action = AsyncMock()
    button = MyCloudPowerButton(
        coordinator,
        main_device_info(nas_data),
        "NAS-SERIAL-1",
        RESTART_DESCRIPTION,
        "reboot",
    )

    await button.async_press()

    coordinator.async_execute_power_action.assert_awaited_once_with("reboot")


async def test_shutdown_button_delegates_to_coordinator(nas_data):
    coordinator = type("Coordinator", (), {"data": nas_data})()
    coordinator.async_execute_power_action = AsyncMock()
    button = MyCloudPowerButton(
        coordinator,
        main_device_info(nas_data),
        "NAS-SERIAL-1",
        SHUTDOWN_DESCRIPTION,
        "shutdown",
    )

    await button.async_press()

    coordinator.async_execute_power_action.assert_awaited_once_with("shutdown")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (PowerActionBlockedError(2), "blocked by its safety check"),
        (TimeoutError(), "outcome is unknown"),
        (aiohttp.ServerConnectionError(), "outcome is unknown"),
    ],
)
async def test_power_action_errors_are_not_retried(nas_data, error, message):
    coordinator = type("Coordinator", (), {"data": nas_data})()
    coordinator.async_execute_power_action = AsyncMock(side_effect=error)
    button = MyCloudPowerButton(
        coordinator,
        main_device_info(nas_data),
        "NAS-SERIAL-1",
        RESTART_DESCRIPTION,
        "reboot",
    )

    with pytest.raises(HomeAssistantError, match=message):
        await button.async_press()

    coordinator.async_execute_power_action.assert_awaited_once_with("reboot")


async def test_unavailable_session_cannot_execute_power_action(
    hass, make_nas_client, nas_data
):
    entry = _entry()
    entry.add_to_hass(hass)
    client = make_nas_client()
    coordinator = MyCloudDataUpdateCoordinator(hass, entry, client)
    button = MyCloudPowerButton(
        coordinator,
        main_device_info(nas_data),
        "NAS-SERIAL-1",
        RESTART_DESCRIPTION,
        "reboot",
    )

    with pytest.raises(HomeAssistantError, match="session is unavailable"):
        await button.async_press()

    client.reboot.assert_not_awaited()
    client.__aenter__.assert_not_awaited()


async def test_concurrent_power_action_is_rejected_without_duplicate_request(
    hass, make_nas_client, nas_data
):
    entry = _entry()
    entry.add_to_hass(hass)
    client = make_nas_client()
    coordinator = MyCloudDataUpdateCoordinator(hass, entry, client)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_reboot():
        started.set()
        await release.wait()

    client.reboot.side_effect = slow_reboot
    button = MyCloudPowerButton(
        coordinator,
        main_device_info(nas_data),
        "NAS-SERIAL-1",
        RESTART_DESCRIPTION,
        "reboot",
    )
    first_press = asyncio.create_task(button.async_press())
    await started.wait()

    with pytest.raises(HomeAssistantError, match="already in progress"):
        await button.async_press()

    release.set()
    await first_press
    client.reboot.assert_awaited_once()
    client.__aenter__.assert_awaited_once()
    await coordinator.async_shutdown()


async def test_v4_concurrent_power_action_is_rejected_without_relogin(
    hass, make_nas_client, v4_data
):
    entry = _entry(4)
    entry.add_to_hass(hass)
    client = make_nas_client(v4_data)
    coordinator = MyCloudDataUpdateCoordinator(hass, entry, client)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_reboot():
        started.set()
        await release.wait()

    client.reboot.side_effect = slow_reboot
    first_action = asyncio.create_task(coordinator.async_execute_power_action("reboot"))
    await started.wait()

    with pytest.raises(PowerActionInProgressError):
        await coordinator.async_execute_power_action("reboot")

    release.set()
    await first_action
    client.reboot.assert_awaited_once()
    client.__aenter__.assert_awaited_once()
    await coordinator.async_shutdown()


async def test_monitoring_waits_while_power_action_is_running(
    hass, make_nas_client, nas_data
):
    entry = _entry()
    entry.add_to_hass(hass)
    client = make_nas_client()
    coordinator = MyCloudDataUpdateCoordinator(hass, entry, client)
    await coordinator._async_setup()
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_shutdown():
        started.set()
        await release.wait()

    client.shutdown.side_effect = slow_shutdown
    action = asyncio.create_task(coordinator.async_execute_power_action("shutdown"))
    await started.wait()
    update = asyncio.create_task(coordinator._async_update_data())
    await asyncio.sleep(0)
    assert not update.done()

    release.set()
    await action
    await update
    client.shutdown.assert_awaited_once()
    client.__aenter__.assert_awaited_once()
    await coordinator.async_shutdown()
