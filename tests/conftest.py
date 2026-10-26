"""Shared offline fixtures for My Cloud tests."""

from __future__ import annotations

import sys
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_socket

pytest_plugins = ["pytest_homeassistant_custom_component"]


if sys.platform == "win32":

    def _disable_remote_sockets(*, allow_unix_socket=False):
        """Keep Windows event-loop sockets available while blocking remote hosts."""
        pytest_socket.enable_socket()
        pytest_socket.socket_allow_hosts(["127.0.0.1", "::1", "localhost"])

    pytest_socket.disable_socket = _disable_remote_sockets


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading the integration from custom_components."""
    yield


@pytest.fixture
def nas_data():
    """Return representative data shared by OS 2 and OS 5 methods."""
    return {
        "system_info": {
            "disks": [
                {
                    "id": "disk1",
                    "name": "Disk_1",
                    "sn": "DISK-SERIAL-1",
                    "model": "WD40EFRX",
                    "rev": "1.0",
                    "size": 4_000_000_000_000,
                    "temp": 34,
                    "healthy": True,
                    "sleep": False,
                    "failed": False,
                    "over_temp": False,
                }
            ],
            "volumes": [
                {
                    "id": "1",
                    "name": "Volume_1",
                    "label": "Volume 1",
                    "size": 4_000_000_000_000,
                    "mounted": True,
                    "unlocked": True,
                    "encrypted": False,
                }
            ],
            "size": {
                "total": 4_000_000_000_000,
                "used": 1_000_000_000_000,
                "unused": 3_000_000_000_000,
            },
        },
        "system_status": {
            "cpu": 12,
            "memory": {
                "total": 1024,
                "unused": 256,
                "simple": "1 GB",
                "buffers": 64,
                "cached": 128,
            },
            "process_count": 42,
        },
        "device_info": {
            "serial_number": "NAS-SERIAL-1",
            "name": "Test NAS",
            "description": "WD My Cloud Mirror",
        },
        "system_version": {"firmware": "5.33.102"},
        "diagnostics": {
            "board_temperature": 0,
            "fan_rpm": 0,
            "disk_statuses": ["good"],
            "raid": {
                "volume_count": 1,
                "result": "success",
                "disk_count": 2,
                "volumes": [
                    {
                        "id": "1",
                        "number": "1",
                        "volume": "Volume_1",
                        "mode": "raid1",
                        "state": "clean",
                        "extra": {},
                    }
                ],
                "extra": {},
            },
            "extra": {},
        },
        "volume_status": [
            {
                "volume": "1",
                "raid_mode": "raid1",
                "raid_status": "clean",
                "file_type": "ext4",
                "free_size": 100,
                "percentage": 0,
                "device": "/dev/md1",
                "raid_uuid": "sensitive-uuid",
            }
        ],
        "system_warning": {"code": 0, "status": "normal"},
        "internet_access": True,
    }


@pytest.fixture
def v4_data():
    """Return only measurements established for the legacy REST 2.1 API."""
    return {
        "system_info": {
            "disks": [],
            "volumes": [],
            "size": {
                "total": 1_964_257_656_832,
                "used": 1_508_405_891_072,
                "unused": 455_851_765_760,
                "used_percentage": 1_508_405_891_072 * 100 / 1_964_257_656_832,
            },
            "capacity_label": "2TB",
            "storage_usage": {
                "size": 1_964_257_656_832,
                "usage": 1_508_405_891_072,
                "free": 455_851_765_760,
                "used_percentage": 1_508_405_891_072 * 100 / 1_964_257_656_832,
                "video": 429_798_163,
                "photos": 5_219_116_702,
                "music": 1_371_834_792,
                "other": 1_501_385_141_415,
            },
            "storage_usage_available": True,
        },
        "system_status": {
            "status": "ready",
            "temperature": "good",
            "smart": "good",
            "volume": "good",
            "free_space": "good",
            "reported_status": "good",
            "extra": {},
        },
        "device_info": {
            "serial_number": "V4-NAS-SERIAL",
            "name": "NAS02",
            "description": "WD My Cloud",
            "model_name": "WDMyCloud",
            "model_number": "sq",
            "manufacturer": "Western Digital Corporation",
        },
        "system_version": {
            "firmware": "04.06.00-111",
            "oled": None,
            "name": "WDMyCloud",
            "description": "Core F/W",
        },
        "internet_access": True,
    }


@pytest.fixture
def make_nas_client(nas_data):
    """Build a fully asynchronous NAS client mock."""

    def _make(data=None):
        values = deepcopy(data or nas_data)
        session = SimpleNamespace(closed=False)
        client = MagicMock()
        client.session = session
        client.phpsessid = "session-id"

        async def _enter():
            session.closed = False
            return client

        async def _exit(*_args):
            session.closed = True

        client.__aenter__ = AsyncMock(side_effect=_enter)
        client.__aexit__ = AsyncMock(side_effect=_exit)
        client.close = AsyncMock(side_effect=_exit)
        client.system_info = AsyncMock(return_value=values["system_info"])
        client.system_status = AsyncMock(return_value=values["system_status"])
        client.device_info = AsyncMock(return_value=values["device_info"])
        client.system_version = AsyncMock(return_value=values["system_version"])
        client.diagnostics = AsyncMock(return_value=values.get("diagnostics"))
        client.volume_status = AsyncMock(return_value=values.get("volume_status"))
        client.system_warning = AsyncMock(return_value=values.get("system_warning"))
        client.internet_access = AsyncMock(return_value=values.get("internet_access"))
        client.reboot = AsyncMock()
        client.shutdown = AsyncMock()
        return client

    return _make
