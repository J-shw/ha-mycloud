"""Offline tests for optional WD OS 5 monitoring."""

import asyncio
from copy import deepcopy
from unittest.mock import patch

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mycloud._vendor.wdnas_client.exceptions import RequestFailedError
from custom_components.mycloud.const import DOMAIN
from custom_components.mycloud.coordinator import MyCloudDataUpdateCoordinator


def _entry(version=5, serial="NAS-SERIAL-1"):
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "Host": "nas.local",
            "Username": "admin",
            "Password": "secret",
            "Version": version,
        },
        unique_id=serial,
    )


async def _setup(hass, entry, client):
    entry.add_to_hass(hass)
    with patch("custom_components.mycloud.coordinator.nas_client", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _state_by_unique_id(hass, entry, unique_id):
    registry = er.async_get(hass)
    entity = next(
        item
        for item in registry.entities.values()
        if item.config_entry_id == entry.entry_id and item.unique_id == unique_id
    )
    return hass.states.get(entity.entity_id)


async def test_os5_monitoring_entities_and_units(hass, make_nas_client):
    """OS 5 entities expose normalized safe fields with stable IDs."""
    entry = _entry()
    await _setup(hass, entry, make_nas_client())

    expected = {
        "NAS-SERIAL-1_process_count": "42",
        "NAS-SERIAL-1_memory_cached": "128",
        "NAS-SERIAL-1_memory_buffers": "64",
        "NAS-SERIAL-1_system_warning": "normal",
        "NAS-SERIAL-1_raid_1_state": "clean",
        "NAS-SERIAL-1_raid_1_mode": "raid1",
        "NAS-SERIAL-1_volume_1_filesystem": "ext4",
        "NAS-SERIAL-1_over_temperature": "off",
        "NAS-SERIAL-1_internet_access": "on",
        "NAS-SERIAL-1_raid_1_problem": "off",
    }
    for unique_id, value in expected.items():
        assert _state_by_unique_id(hass, entry, unique_id).state == value

    entries = [
        item
        for item in er.async_get(hass).entities.values()
        if item.config_entry_id == entry.entry_id
    ]
    assert len(entries) == 27
    assert len({item.unique_id for item in entries}) == 27

    assert (
        _state_by_unique_id(hass, entry, "NAS-SERIAL-1_memory_cached").attributes[
            "unit_of_measurement"
        ]
        == "KiB"
    )


async def test_os2_never_calls_or_registers_os5_monitoring(hass, make_nas_client):
    """OS 2 keeps the established entity set and never probes OS 5 APIs."""
    client = make_nas_client()
    entry = _entry(version=2)
    await _setup(hass, entry, client)

    for method in (
        client.diagnostics,
        client.volume_status,
        client.system_warning,
        client.internet_access,
    ):
        method.assert_not_awaited()
    registry = er.async_get(hass)
    entries = [
        item
        for item in registry.entities.values()
        if item.config_entry_id == entry.entry_id
    ]
    assert len(entries) == 15


async def test_optional_calls_are_rate_limited(hass, make_nas_client):
    """Rapid base updates do not repeat low-priority NAS requests."""
    client = make_nas_client()
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = MyCloudDataUpdateCoordinator(hass, entry, client)
    await coordinator._async_setup()
    await coordinator._async_update_data()
    await coordinator._async_update_data()

    assert client.system_info.await_count == 2
    for method in (
        client.diagnostics,
        client.volume_status,
        client.system_warning,
        client.internet_access,
    ):
        method.assert_awaited_once()
    await coordinator.async_shutdown()


async def test_optional_failure_retains_data_but_marks_endpoint_unavailable(
    hass, make_nas_client, nas_data
):
    """One unsupported endpoint does not discard other or last-known data."""
    client = make_nas_client()
    client.diagnostics.side_effect = [
        nas_data["diagnostics"],
        RequestFailedError(404),
    ]
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = MyCloudDataUpdateCoordinator(hass, entry, client)
    await coordinator._async_setup()

    with patch("custom_components.mycloud.coordinator.monotonic", side_effect=[0, 700]):
        first = await coordinator._async_update_data()
        second = await coordinator._async_update_data()

    assert first["diagnostics"] == second["diagnostics"]
    assert second["optional_available"]["diagnostics"] is False
    assert second["optional_available"]["system_warning"] is True
    assert second["system_info"] == nas_data["system_info"]
    await coordinator.async_shutdown()


async def test_optional_auth_failure_uses_central_reauthentication(
    hass, make_nas_client, nas_data
):
    """An auth failure from an optional endpoint follows the one-login retry path."""
    client = make_nas_client()
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = MyCloudDataUpdateCoordinator(hass, entry, client)
    await coordinator._async_setup()
    await coordinator._async_update_data()
    coordinator._optional_last_attempt.clear()
    client.diagnostics.side_effect = [
        RequestFailedError(403),
        nas_data["diagnostics"],
    ]
    data = await coordinator._async_update_data()

    assert client.__aenter__.await_count == 2
    assert client.diagnostics.await_count == 3
    assert data["optional_available"]["diagnostics"] is True
    await coordinator.async_shutdown()


@pytest.mark.parametrize(
    ("code", "status", "warning_state", "over_temperature"),
    [
        (0, "normal", "normal", "off"),
        (1, "over_temperature", "over_temperature", "on"),
        (2, "system_busy_hotplug", "system_busy_hotplug", "off"),
        (99, "unexpected", "unknown", "unknown"),
    ],
)
async def test_warning_code_semantics(
    hass,
    make_nas_client,
    nas_data,
    code,
    status,
    warning_state,
    over_temperature,
):
    """Unknown warnings never become normal and only code 1 means overheat."""
    data = deepcopy(nas_data)
    data["system_warning"] = {"code": code, "status": status}
    entry = _entry(serial=f"warning-{code}")
    await _setup(hass, entry, make_nas_client(data))
    assert (
        _state_by_unique_id(hass, entry, "NAS-SERIAL-1_system_warning").state
        == warning_state
    )
    assert (
        _state_by_unique_id(hass, entry, "NAS-SERIAL-1_over_temperature").state
        == over_temperature
    )


@pytest.mark.parametrize(
    ("raid_state", "problem_state"),
    [
        ("clean", "off"),
        ("degraded", "on"),
        ("damaged", "on"),
        ("resync", "off"),
        ("resync5", "off"),
        ("recovery", "off"),
        ("migrate", "off"),
        ("reshape", "off"),
        ("expansion", "off"),
        ("vendor-new-state", "unknown"),
    ],
)
async def test_raid_state_semantics(
    hass, make_nas_client, nas_data, raid_state, problem_state
):
    """RAID transitions stay visible without false failure alarms."""
    data = deepcopy(nas_data)
    data["diagnostics"]["raid"]["volumes"][0]["state"] = raid_state
    entry = _entry(serial=f"raid-{raid_state}")
    await _setup(hass, entry, make_nas_client(data))
    normalized = raid_state if raid_state != "vendor-new-state" else "unknown"
    assert (
        _state_by_unique_id(hass, entry, "NAS-SERIAL-1_raid_1_state").state
        == normalized
    )
    assert (
        _state_by_unique_id(hass, entry, "NAS-SERIAL-1_raid_1_problem").state
        == problem_state
    )


async def test_multiple_raid_volumes_have_distinct_ids(hass, make_nas_client, nas_data):
    """Multiple RAID volumes on one NAS are represented independently."""
    data = deepcopy(nas_data)
    second = deepcopy(data["diagnostics"]["raid"]["volumes"][0])
    second.update({"id": "2", "number": "2", "volume": "Volume_2"})
    data["diagnostics"]["raid"]["volumes"].append(second)
    entry = _entry()
    await _setup(hass, entry, make_nas_client(data))

    assert _state_by_unique_id(hass, entry, "NAS-SERIAL-1_raid_1_state")
    assert _state_by_unique_id(hass, entry, "NAS-SERIAL-1_raid_2_state")


async def test_sensitive_volume_fields_are_not_entity_attributes(hass, make_nas_client):
    """Device paths and RAID identifiers never enter the state machine."""
    entry = _entry()
    await _setup(hass, entry, make_nas_client())
    state = _state_by_unique_id(hass, entry, "NAS-SERIAL-1_volume_1_filesystem")
    serialized = repr(state.as_dict())
    assert "/dev/md1" not in serialized
    assert "sensitive-uuid" not in serialized


async def test_process_info_is_never_called(hass, make_nas_client):
    """Process count comes from system_status without process enumeration."""
    client = make_nas_client()
    entry = _entry()
    await _setup(hass, entry, client)
    client.process_info.assert_not_called()


async def test_optional_timeout_does_not_fail_base_update(hass, make_nas_client):
    """A slow optional endpoint becomes unavailable without blocking core data."""

    async def never_finishes():
        await asyncio.Event().wait()

    client = make_nas_client()
    client.internet_access.side_effect = never_finishes
    entry = _entry()
    entry.add_to_hass(hass)
    coordinator = MyCloudDataUpdateCoordinator(hass, entry, client)
    await coordinator._async_setup()

    with patch("custom_components.mycloud.coordinator.UPDATE_TIMEOUT", 0.01):
        data = await coordinator._async_update_data()

    assert data["system_status"]["cpu"] == 12
    assert data["optional_available"]["internet_access"] is False
    await coordinator.async_shutdown()


async def test_missing_optional_fields_are_unknown(hass, make_nas_client, nas_data):
    """Missing safe fields remain unknown instead of receiving invented defaults."""
    data = deepcopy(nas_data)
    data["system_status"] = {"cpu": 12, "memory": {"total": 1024, "unused": 256}}
    data["system_warning"] = {}
    data["internet_access"] = None
    data["diagnostics"] = {"raid": {"volumes": []}}
    data["volume_status"] = []
    entry = _entry()
    await _setup(hass, entry, make_nas_client(data))

    for unique_id in (
        "NAS-SERIAL-1_process_count",
        "NAS-SERIAL-1_memory_cached",
        "NAS-SERIAL-1_memory_buffers",
        "NAS-SERIAL-1_over_temperature",
        "NAS-SERIAL-1_internet_access",
    ):
        assert _state_by_unique_id(hass, entry, unique_id).state == "unknown"
    assert (
        _state_by_unique_id(hass, entry, "NAS-SERIAL-1_system_warning").state
        == "unknown"
    )


@pytest.mark.parametrize(("reported", "expected"), [(True, "on"), (False, "off")])
async def test_nas_reported_internet_connectivity(
    hass, make_nas_client, nas_data, reported, expected
):
    """Connectivity reflects only the NAS response and performs no external probe."""
    data = deepcopy(nas_data)
    data["internet_access"] = reported
    entry = _entry(serial=f"internet-{reported}")
    await _setup(hass, entry, make_nas_client(data))
    assert (
        _state_by_unique_id(hass, entry, "NAS-SERIAL-1_internet_access").state
        == expected
    )


async def test_existing_disk_health_handles_failure(hass, make_nas_client, nas_data):
    """OS 5 reuses the existing disk health entities without duplicates."""
    data = deepcopy(nas_data)
    data["system_info"]["disks"][0].update({"healthy": False, "failed": True})
    entry = _entry()
    await _setup(hass, entry, make_nas_client(data))

    assert _state_by_unique_id(hass, entry, "DISK-SERIAL-1_disk_healthy").state == "off"
    assert _state_by_unique_id(hass, entry, "DISK-SERIAL-1_disk_failed").state == "on"
    registry = er.async_get(hass)
    health_entries = [
        item
        for item in registry.entities.values()
        if item.config_entry_id == entry.entry_id
        and item.unique_id == "DISK-SERIAL-1_disk_healthy"
    ]
    assert len(health_entries) == 1


async def test_two_nas_get_distinct_os5_unique_ids(hass, make_nas_client, nas_data):
    """The same RAID and volume numbers do not collide across NAS devices."""
    second_data = deepcopy(nas_data)
    second_data["device_info"]["serial_number"] = "NAS-SERIAL-2"
    second_data["device_info"]["name"] = "Second NAS"
    second_data["system_info"]["disks"][0]["sn"] = "DISK-SERIAL-2"
    first = _entry(serial="NAS-SERIAL-1")
    second = _entry(serial="NAS-SERIAL-2")
    await _setup(hass, first, make_nas_client())
    await _setup(hass, second, make_nas_client(second_data))

    first_state = _state_by_unique_id(hass, first, "NAS-SERIAL-1_raid_1_state")
    second_state = _state_by_unique_id(hass, second, "NAS-SERIAL-2_raid_1_state")
    assert first_state.entity_id != second_state.entity_id
