"""Tests for existing sensor behavior and registry compatibility."""

from copy import deepcopy
from unittest.mock import patch

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mycloud.const import DOMAIN
from custom_components.mycloud.sensor import STORAGE_USAGE_DESCRIPTION


def _entry(unique_id="NAS-SERIAL-1", version=5):
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "Host": "nas.local",
            "Username": "admin",
            "Password": "secret",
            "Version": version,
        },
        unique_id=unique_id,
    )


def test_storage_usage_suggests_one_decimal_without_rounding_source_value():
    assert STORAGE_USAGE_DESCRIPTION.suggested_display_precision == 1
    assert STORAGE_USAGE_DESCRIPTION.native_unit_of_measurement == "%"


async def _setup(hass, entry, client):
    entry.add_to_hass(hass)
    with patch("custom_components.mycloud.coordinator.nas_client", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_existing_sensors_and_binary_sensors(hass, make_nas_client):
    """All established values remain available on their correct platforms."""
    entry = _entry()
    await _setup(hass, entry, make_nas_client())

    registry = er.async_get(hass)
    entries = [
        item
        for item in registry.entities.values()
        if item.config_entry_id == entry.entry_id
    ]
    by_unique_id = {item.unique_id: item for item in entries}
    existing_unique_ids = {
        "NAS-SERIAL-1_cpu_usage",
        "NAS-SERIAL-1_memory_usage",
        "NAS-SERIAL-1_total_storage",
        "NAS-SERIAL-1_used_storage",
        "NAS-SERIAL-1_unused_storage",
        "DISK-SERIAL-1_disk_temp",
        "DISK-SERIAL-1_disk_size",
        "DISK-SERIAL-1_disk_healthy",
        "DISK-SERIAL-1_disk_sleep",
        "DISK-SERIAL-1_disk_failed",
        "DISK-SERIAL-1_disk_over_temp",
        "NAS-SERIAL-1_1_volume_size",
        "NAS-SERIAL-1_1_volume_mounted",
        "NAS-SERIAL-1_1_volume_unlocked",
        "NAS-SERIAL-1_1_volume_encrypted",
    }
    assert existing_unique_ids <= set(by_unique_id)
    assert by_unique_id["NAS-SERIAL-1_cpu_usage"].entity_id == (
        "sensor.test_nas_cpu_usage"
    )
    assert by_unique_id["DISK-SERIAL-1_disk_healthy"].entity_id == (
        "binary_sensor.test_nas_disk_disk_1_healthy"
    )
    assert by_unique_id["NAS-SERIAL-1_1_volume_size"].entity_id == (
        "sensor.test_nas_volume_1_size"
    )
    assert by_unique_id["NAS-SERIAL-1_1_volume_mounted"].entity_id == (
        "binary_sensor.test_nas_volume_1_mounted"
    )

    expected_states = {
        "NAS-SERIAL-1_cpu_usage": "12",
        "NAS-SERIAL-1_memory_usage": "75.0",
        "NAS-SERIAL-1_total_storage": "4.0",
        "NAS-SERIAL-1_used_storage": "1.0",
        "NAS-SERIAL-1_unused_storage": "3.0",
        "DISK-SERIAL-1_disk_temp": "34.0",
        "DISK-SERIAL-1_disk_size": "4.0",
        "DISK-SERIAL-1_disk_healthy": "on",
        "DISK-SERIAL-1_disk_sleep": "off",
        "DISK-SERIAL-1_disk_failed": "off",
        "DISK-SERIAL-1_disk_over_temp": "off",
        "NAS-SERIAL-1_1_volume_size": "4.0",
        "NAS-SERIAL-1_1_volume_mounted": "on",
        "NAS-SERIAL-1_1_volume_unlocked": "on",
        "NAS-SERIAL-1_1_volume_encrypted": "off",
    }
    assert {
        unique_id: hass.states.get(item.entity_id).state
        for unique_id, item in by_unique_id.items()
        if unique_id in existing_unique_ids
    } == expected_states


async def test_v4_entities_only_expose_verified_capabilities(
    hass, make_nas_client, v4_data
):
    entry = _entry("4:nas.local", version=4)
    await _setup(hass, entry, make_nas_client(v4_data))

    registry = er.async_get(hass)
    entries = {
        item.unique_id: item
        for item in registry.entities.values()
        if item.config_entry_id == entry.entry_id
    }
    expected = {
        "V4-NAS-SERIAL_total_storage": "1.964257656832",
        "V4-NAS-SERIAL_used_storage": "1.508405891072",
        "V4-NAS-SERIAL_unused_storage": "0.45585176576",
        "V4-NAS-SERIAL_storage_usage": "76.7926695271124",
        "V4-NAS-SERIAL_v4_status": "ready",
        "V4-NAS-SERIAL_v4_temperature": "good",
        "V4-NAS-SERIAL_v4_smart_problem": "off",
        "V4-NAS-SERIAL_v4_volume_problem": "off",
        "V4-NAS-SERIAL_v4_free_space_problem": "off",
        "V4-NAS-SERIAL_internet_access": "on",
        "V4-NAS-SERIAL_restart": "unknown",
        "V4-NAS-SERIAL_shutdown": "unknown",
    }
    assert set(entries) == set(expected)
    assert {
        unique_id: hass.states.get(entry_data.entity_id).state
        for unique_id, entry_data in entries.items()
    } == expected
    assert "V4-NAS-SERIAL_cpu_usage" not in entries

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "V4-NAS-SERIAL"), entry.entry_id
    )
    assert device is not None
    assert device.model == "WD My Cloud"
    assert device.sw_version == "04.06.00-111"


async def test_v4_storage_failure_preserves_existing_entities(
    hass, make_nas_client, v4_data
):
    """Optional storage failure retains fallback total and established health."""
    data = deepcopy(v4_data)
    data["system_info"]["size"] = {
        "total": 2_000_000_000_000,
        "used": None,
        "unused": None,
        "used_percentage": None,
    }
    data["system_info"]["storage_usage"] = None
    data["system_info"]["storage_usage_available"] = False
    entry = _entry("4:nas.local", version=4)
    await _setup(hass, entry, make_nas_client(data))

    registry = er.async_get(hass)
    entity_ids = {
        item.unique_id: item.entity_id
        for item in registry.entities.values()
        if item.config_entry_id == entry.entry_id
    }
    assert hass.states.get(entity_ids["V4-NAS-SERIAL_total_storage"]).state == "2.0"
    assert hass.states.get(entity_ids["V4-NAS-SERIAL_v4_status"]).state == "ready"
    assert hass.states.get(entity_ids["V4-NAS-SERIAL_used_storage"]).state == "unknown"
    assert (
        hass.states.get(entity_ids["V4-NAS-SERIAL_unused_storage"]).state == "unknown"
    )
    assert hass.states.get(entity_ids["V4-NAS-SERIAL_storage_usage"]).state == (
        "unavailable"
    )


async def test_owned_legacy_volume_ids_are_preserved(hass, make_nas_client):
    """An upgraded entry binds to every legacy volume ID without renaming it."""
    entry = _entry()
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "1")}
    )

    legacy_ids = {
        ("sensor", "1_volume_size"),
        ("binary_sensor", "Test NAS Volume 1_volume_mounted"),
        ("binary_sensor", "Test NAS Volume 1_volume_unlocked"),
        ("binary_sensor", "Test NAS Volume 1_volume_encrypted"),
    }
    legacy_entity_ids = {}
    for platform, unique_id in legacy_ids:
        registered = entity_registry.async_get_or_create(
            platform,
            DOMAIN,
            unique_id,
            config_entry=entry,
            suggested_object_id=f"legacy_{platform}_{len(unique_id)}",
        )
        legacy_entity_ids[(platform, unique_id)] = registered.entity_id

    with patch(
        "custom_components.mycloud.coordinator.nas_client",
        return_value=make_nas_client(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    entry_unique_ids = {
        item.unique_id
        for item in entity_registry.entities.values()
        if item.config_entry_id == entry.entry_id
    }
    for platform, unique_id in legacy_ids:
        assert unique_id in entry_unique_ids
        assert (
            entity_registry.async_get_entity_id(platform, DOMAIN, unique_id)
            == legacy_entity_ids[(platform, unique_id)]
        )
    assert "NAS-SERIAL-1_1_volume_size" not in entry_unique_ids
    assert "NAS-SERIAL-1_1_volume_mounted" not in entry_unique_ids
    assert "NAS-SERIAL-1_1_volume_unlocked" not in entry_unique_ids
    assert "NAS-SERIAL-1_1_volume_encrypted" not in entry_unique_ids

    volume_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, "1"), entry.entry_id
    )
    assert volume_device is not None
    assert volume_device.config_entry_id == entry.entry_id


async def test_legacy_binary_entities_remain_non_destructive(hass, make_nas_client):
    """Legacy entities retain IDs, customizations, devices, and restart behavior."""
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    area = ar.async_get(hass).async_create("Server room")
    disk_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "DISK-SERIAL-1")},
    )
    volume_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "1")},
    )

    disk = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "DISK-SERIAL-1_disk_healthy",
        config_entry=entry,
        device_id=disk_device.id,
        suggested_object_id="test_nas_disk_disk_1_healthy",
    )
    disk = registry.async_update_entity(
        disk.entity_id,
        area_id=area.id,
        icon="mdi:custom-test",
        name="Custom health name",
    )
    volume = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "Test NAS Volume 1_volume_mounted",
        config_entry=entry,
        device_id=volume_device.id,
        disabled_by=er.RegistryEntryDisabler.USER,
        suggested_object_id="test_nas_volume_1_mounted",
    )
    original_ids = {disk.unique_id: disk.id, volume.unique_id: volume.id}

    first_client = make_nas_client()
    with patch(
        "custom_components.mycloud.coordinator.nas_client", return_value=first_client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    disk_after = registry.async_get("sensor.test_nas_disk_disk_1_healthy")
    volume_after = registry.async_get("sensor.test_nas_volume_1_mounted")
    assert disk_after.id == original_ids[disk_after.unique_id]
    assert disk_after.name == "Custom health name"
    assert disk_after.icon == "mdi:custom-test"
    assert disk_after.area_id == area.id
    assert disk_after.device_id == disk_device.id
    assert volume_after.id == original_ids[volume_after.unique_id]
    assert volume_after.disabled_by is er.RegistryEntryDisabler.USER
    assert volume_after.device_id == volume_device.id
    assert hass.states.get(disk_after.entity_id).state == "on"
    assert hass.states.get(volume_after.entity_id) is None
    assert (
        registry.async_get_entity_id("binary_sensor", DOMAIN, disk_after.unique_id)
        is None
    )
    assert (
        registry.async_get_entity_id("binary_sensor", DOMAIN, volume_after.unique_id)
        is None
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    second_client = make_nas_client()
    with patch(
        "custom_components.mycloud.coordinator.nas_client", return_value=second_client
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert (
        registry.async_get(disk_after.entity_id).id
        == original_ids[disk_after.unique_id]
    )
    assert (
        registry.async_get(volume_after.entity_id).id
        == original_ids[volume_after.unique_id]
    )


async def test_conflicting_legacy_and_binary_entries_are_not_mutated(
    hass, make_nas_client, caplog
):
    """A cross-domain conflict is left untouched and neither copy is loaded."""
    entry = _entry()
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    unique_id = "DISK-SERIAL-1_disk_healthy"
    legacy = registry.async_get_or_create(
        "sensor", DOMAIN, unique_id, config_entry=entry
    )
    current = registry.async_get_or_create(
        "binary_sensor", DOMAIN, unique_id, config_entry=entry
    )

    with patch(
        "custom_components.mycloud.coordinator.nas_client",
        return_value=make_nas_client(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert registry.async_get(legacy.entity_id).id == legacy.id
    assert registry.async_get(current.entity_id).id == current.id
    assert hass.states.get(legacy.entity_id) is None
    assert hass.states.get(current.entity_id) is None
    assert "Conflicting legacy and binary registry entries" in caplog.text


async def test_two_nas_devices_get_distinct_new_volume_ids(
    hass, make_nas_client, nas_data
):
    """Identical volume IDs on two devices never collide."""
    data_two = deepcopy(nas_data)
    data_two["device_info"] = {
        **data_two["device_info"],
        "serial_number": "NAS-SERIAL-2",
        "name": "Second NAS",
    }
    data_two["system_info"]["disks"][0]["sn"] = "DISK-SERIAL-2"

    first = _entry("NAS-SERIAL-1")
    second = _entry("NAS-SERIAL-2")
    await _setup(hass, first, make_nas_client())
    await _setup(hass, second, make_nas_client(data_two))

    registry = er.async_get(hass)
    first_volume = registry.async_get_entity_id(
        "sensor", DOMAIN, "NAS-SERIAL-1_1_volume_size"
    )
    second_volume = registry.async_get_entity_id(
        "sensor", DOMAIN, "NAS-SERIAL-2_1_volume_size"
    )
    assert first_volume is not None
    assert second_volume is not None
    assert first_volume != second_volume


async def test_malformed_optional_disk_values_become_unknown(
    hass, make_nas_client, nas_data
):
    """Malformed temperature/size values do not break all entities."""
    data = deepcopy(nas_data)
    data["system_info"]["disks"][0]["temp"] = None
    data["system_info"]["disks"][0]["size"] = "invalid"
    entry = _entry()
    await _setup(hass, entry, make_nas_client(data))

    registry = er.async_get(hass)
    temp_id = registry.async_get_entity_id("sensor", DOMAIN, "DISK-SERIAL-1_disk_temp")
    size_id = registry.async_get_entity_id("sensor", DOMAIN, "DISK-SERIAL-1_disk_size")
    assert hass.states.get(temp_id).state == "unknown"
    assert hass.states.get(size_id).state == "unknown"
