"""Offline tests for manual setup and reauthentication."""

import asyncio
from unittest.mock import patch

import aiohttp
import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from probatio.codecs.fields import to_field_list
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mycloud import config_flow
from custom_components.mycloud._vendor.wdnas_client.exceptions import (
    InvalidLoginError,
    RequestFailedError,
)
from custom_components.mycloud.config_flow import _validate_host
from custom_components.mycloud.const import DOMAIN, RELEASE_ADMIN_SESSION
from custom_components.mycloud.session import (
    _handoffs,
    async_discard_reauth_client,
    store_reauth_client,
    take_reauth_client,
)

USER_INPUT = {
    "Host": "nas.local",
    "Username": "admin",
    "Password": "secret",
    "Version": 5,
}


@pytest.mark.parametrize(
    ("value", "expected"),
    [("192.168.1.10", "192.168.1.10"), ("NAS.Local", "nas.local")],
)
def test_host_validation(value, expected):
    assert _validate_host(value) == expected


@pytest.mark.parametrize("value", ["", "http://nas.local", "nas/path", "bad host"])
def test_invalid_host_rejected(value):
    with pytest.raises(vol.Invalid):
        _validate_host(value)


async def test_initial_form_opens_no_nas_session(hass):
    with patch("custom_components.mycloud.config_flow.nas_client") as constructor:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    constructor.assert_not_called()


@pytest.mark.parametrize("version", [2, 5])
async def test_os2_os5_setup_retains_historical_probe_login(
    hass, make_nas_client, version
):
    submitted = {**USER_INPUT, "Version": str(version)}
    verifier = make_nas_client()
    with (
        patch(
            "custom_components.mycloud.config_flow.nas_client",
            return_value=verifier,
        ) as constructor,
        patch("custom_components.mycloud.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=submitted,
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test NAS"
    assert result["data"] == {**USER_INPUT, "Version": version}
    assert result["result"].unique_id == "NAS-SERIAL-1"
    constructor.assert_called_once_with("admin", "secret", "nas.local", version)
    verifier.__aenter__.assert_awaited_once()
    verifier.device_info.assert_awaited_once()
    verifier.__aexit__.assert_awaited_once()
    verifier.close.assert_not_awaited()


async def test_manual_v4_setup_also_defers_login_to_coordinator(hass):
    submitted = {**USER_INPUT, "Version": "4"}
    with (
        patch("custom_components.mycloud.config_flow.nas_client") as constructor,
        patch("custom_components.mycloud.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=submitted,
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["Version"] == 4
    constructor.assert_not_called()


@pytest.mark.parametrize("version", [2, 5])
async def test_manual_ip_host_setup_uses_historical_probe(
    hass, make_nas_client, version
):
    submitted = {
        **USER_INPUT,
        "Host": "192.0.2.10",
        "Version": str(version),
    }
    with (
        patch(
            "custom_components.mycloud.config_flow.nas_client",
            return_value=make_nas_client(),
        ) as constructor,
        patch("custom_components.mycloud.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=submitted,
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "NAS-SERIAL-1"
    constructor.assert_called_once()


async def test_manual_v4_ip_host_setup_never_opens_probe_session(hass):
    submitted = {**USER_INPUT, "Host": "192.0.2.10", "Version": "4"}
    with (
        patch("custom_components.mycloud.config_flow.nas_client") as constructor,
        patch("custom_components.mycloud.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=submitted,
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "4:192.0.2.10"
    constructor.assert_not_called()


def test_automatic_detection_flow_is_removed():
    assert not hasattr(config_flow, "async_detect_api_version")
    assert not hasattr(config_flow.MyCloudConfigFlow, "async_step_api_version")


async def test_user_flow_validates_and_normalizes_host_after_submission(
    hass, make_nas_client
):
    invalid = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={**USER_INPUT, "Host": "https://nas.local/path"},
    )
    assert invalid["type"] is FlowResultType.FORM
    assert invalid["errors"] == {"Host": "invalid_host"}

    with (
        patch(
            "custom_components.mycloud.config_flow.nas_client",
            return_value=make_nas_client(),
        ),
        patch("custom_components.mycloud.async_setup_entry", return_value=True),
    ):
        valid = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={**USER_INPUT, "Host": "NAS.Local."},
        )

    assert valid["type"] is FlowResultType.CREATE_ENTRY
    assert valid["data"]["Host"] == "nas.local"


async def test_user_flow_rejects_invalid_api_version_without_login(hass):
    with patch("custom_components.mycloud.config_flow.nas_client") as constructor:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data={**USER_INPUT, "Version": "3"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"Version": "unsupported_firmware"}
    constructor.assert_not_called()


@pytest.mark.parametrize("version", [None, "", "3", "6"])
async def test_user_flow_requires_supported_manual_version(hass, version):
    submitted = {**USER_INPUT, "Version": version}
    if version is None:
        submitted.pop("Version")
    with patch("custom_components.mycloud.config_flow.nas_client") as constructor:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=submitted,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"Version": "unsupported_firmware"}
    constructor.assert_not_called()


@pytest.mark.parametrize("existing_version", [2, 4, 5])
async def test_user_flow_rejects_existing_entry_with_same_host(hass, existing_version):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, "Version": existing_version},
        unique_id="NAS-SERIAL-1",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
        data={**USER_INPUT, "Version": "5"},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _submit_reauthentication(hass, entry, credentials):
    """Open and submit one reauthentication flow."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], credentials
    )


async def test_v4_reauthentication_hands_verified_session_to_reload(
    hass, make_nas_client
):
    version = 4
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, "Version": version},
        unique_id="NAS-SERIAL-1",
    )
    entry.add_to_hass(hass)
    verifier = make_nas_client()
    with (
        patch(
            "custom_components.mycloud.config_flow.nas_client",
            return_value=verifier,
        ) as constructor,
        patch.object(hass.config_entries, "async_schedule_reload") as reload_mock,
    ):
        result = await _submit_reauthentication(
            hass,
            entry,
            {"Username": "new-admin", "Password": "new-secret"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["Version"] == version
    assert entry.data["Username"] == "new-admin"
    assert entry.data["Password"] == "new-secret"
    assert entry.unique_id == "NAS-SERIAL-1"
    constructor.assert_called_once_with("new-admin", "new-secret", "nas.local", version)
    verifier.__aenter__.assert_awaited_once()
    verifier.device_info.assert_awaited_once()
    verifier.close.assert_not_awaited()
    reload_mock.assert_called_once_with(entry.entry_id)
    assert take_reauth_client(hass, entry.entry_id) is verifier
    await verifier.close()


@pytest.mark.parametrize("version", [2, 5])
async def test_os2_os5_reauthentication_uses_historical_local_close(
    hass, make_nas_client, version
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, "Version": version},
        unique_id="NAS-SERIAL-1",
    )
    entry.add_to_hass(hass)
    verifier = make_nas_client()
    with (
        patch(
            "custom_components.mycloud.config_flow.nas_client",
            return_value=verifier,
        ),
        patch.object(hass.config_entries, "async_schedule_reload") as reload_mock,
    ):
        result = await _submit_reauthentication(
            hass,
            entry,
            {"Username": "new-admin", "Password": "new-secret"},
        )

    assert result["reason"] == "reauth_successful"
    assert entry.data["Version"] == version
    assert entry.data["Username"] == "new-admin"
    verifier.__aenter__.assert_awaited_once()
    verifier.device_info.assert_awaited_once()
    verifier.__aexit__.assert_awaited_once()
    verifier.close.assert_not_awaited()
    assert take_reauth_client(hass, entry.entry_id) is None
    reload_mock.assert_called_once_with(entry.entry_id)


async def test_successful_reauthentication_allows_following_setup(
    hass, make_nas_client
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        unique_id="NAS-SERIAL-1",
    )
    entry.add_to_hass(hass)
    events = []
    verifier = make_nas_client()

    async def verifier_login():
        events.append("verification_login")
        return verifier

    verifier.__aenter__.side_effect = verifier_login

    with (
        patch(
            "custom_components.mycloud.config_flow.nas_client",
            return_value=verifier,
        ),
        patch.object(hass.config_entries, "async_schedule_reload"),
    ):
        result = await _submit_reauthentication(
            hass,
            entry,
            {"Username": "new-admin", "Password": "new-secret"},
        )

    assert result["reason"] == "reauth_successful"
    coordinator_client = make_nas_client()
    with patch(
        "custom_components.mycloud.coordinator.nas_client",
        return_value=coordinator_client,
    ) as coordinator_constructor:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert events == ["verification_login"]
    assert entry.state is config_entries.ConfigEntryState.LOADED
    coordinator_constructor.assert_called_once_with(
        "new-admin", "new-secret", "nas.local", 5
    )
    verifier.__aenter__.assert_awaited_once()
    verifier.__aexit__.assert_awaited_once()
    verifier.close.assert_not_awaited()
    coordinator_client.__aenter__.assert_awaited_once()
    await hass.config_entries.async_unload(entry.entry_id)
    coordinator_client.close.assert_awaited_once()
    coordinator_client.__aexit__.assert_not_awaited()


@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (InvalidLoginError("rejected"), "auth"),
        (RequestFailedError(403), "auth"),
        (aiohttp.ClientConnectionError("offline"), "connection"),
        (TimeoutError(), "connection"),
    ],
)
async def test_failed_reauthentication_never_reports_success(
    hass, make_nas_client, error, expected_error
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        unique_id="NAS-SERIAL-1",
    )
    entry.add_to_hass(hass)
    verifier = make_nas_client()
    verifier.__aenter__.side_effect = error

    with (
        patch(
            "custom_components.mycloud.config_flow.nas_client",
            return_value=verifier,
        ),
        patch.object(hass.config_entries, "async_schedule_reload") as reload_mock,
    ):
        result = await _submit_reauthentication(
            hass,
            entry,
            {"Username": "wrong", "Password": "wrong-secret"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected_error}
    assert entry.data == USER_INPUT
    verifier.__aexit__.assert_awaited_once()
    verifier.close.assert_not_awaited()
    reload_mock.assert_not_called()


async def test_reauthentication_rejects_session_that_fails_first_api_call(
    hass, make_nas_client
):
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="NAS-SERIAL-1")
    entry.add_to_hass(hass)
    verifier = make_nas_client()
    verifier.device_info.side_effect = RequestFailedError(403)

    with (
        patch(
            "custom_components.mycloud.config_flow.nas_client",
            return_value=verifier,
        ),
        patch.object(hass.config_entries, "async_schedule_reload") as reload_mock,
    ):
        result = await _submit_reauthentication(
            hass,
            entry,
            {"Username": "admin", "Password": "new-secret"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "auth"}
    assert entry.data == USER_INPUT
    verifier.__aenter__.assert_awaited_once()
    verifier.device_info.assert_awaited_once()
    verifier.__aexit__.assert_awaited_once()
    verifier.close.assert_not_awaited()
    reload_mock.assert_not_called()


async def test_stale_handoff_logout_failure_blocks_new_reauthentication(
    hass, make_nas_client
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, "Version": 4},
        unique_id="NAS-SERIAL-1",
    )
    entry.add_to_hass(hass)
    stale_client = make_nas_client()
    stale_client.close.side_effect = aiohttp.ClientConnectionError("logout ambiguous")
    store_reauth_client(hass, entry.entry_id, stale_client)

    with (
        patch(
            "custom_components.mycloud.config_flow.nas_client",
        ) as constructor,
        patch.object(hass.config_entries, "async_schedule_reload") as reload_mock,
    ):
        result = await _submit_reauthentication(
            hass,
            entry,
            {"Username": "new-admin", "Password": "new-secret"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "logout_failed"}
    assert entry.data == {**USER_INPUT, "Version": 4}
    stale_client.close.assert_awaited_once()
    constructor.assert_not_called()
    reload_mock.assert_not_called()

    stale_client.close.side_effect = None
    await async_discard_reauth_client(hass, entry.entry_id)


async def test_parallel_reauthentication_submission_performs_one_login(
    hass, make_nas_client
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, "Version": 4},
        unique_id="NAS-SERIAL-1",
    )
    entry.add_to_hass(hass)
    verifier = make_nas_client()
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_login():
        started.set()
        await release.wait()
        return verifier

    verifier.__aenter__.side_effect = slow_login
    with (
        patch(
            "custom_components.mycloud.config_flow.nas_client",
            return_value=verifier,
        ),
        patch.object(hass.config_entries, "async_schedule_reload"),
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={
                "source": config_entries.SOURCE_REAUTH,
                "entry_id": entry.entry_id,
            },
            data=entry.data,
        )
        submission = asyncio.create_task(
            hass.config_entries.flow.async_configure(
                flow["flow_id"],
                {"Username": "admin", "Password": "new-secret"},
            )
        )
        await started.wait()
        release.set()
        result = await submission

    assert result["reason"] == "reauth_successful"
    verifier.__aenter__.assert_awaited_once()
    assert take_reauth_client(hass, entry.entry_id) is verifier
    await verifier.close()


async def test_unclaimed_reauthentication_session_expires(hass, make_nas_client):
    verifier = make_nas_client()
    with patch("custom_components.mycloud.session._HANDOFF_TIMEOUT", 0):
        store_reauth_client(hass, "entry-id", verifier)
        cleanup_task = _handoffs(hass)["entry-id"].cleanup_task
        assert cleanup_task is not None
        await cleanup_task

    verifier.close.assert_awaited_once()
    assert take_reauth_client(hass, "entry-id") is None


async def test_cancelled_unclaimed_handoff_still_closes_session(hass, make_nas_client):
    verifier = make_nas_client()
    store_reauth_client(hass, "entry-id", verifier)
    cleanup_task = _handoffs(hass)["entry-id"].cleanup_task
    assert cleanup_task is not None

    cleanup_task.cancel()
    await cleanup_task

    verifier.close.assert_awaited_once()
    assert take_reauth_client(hass, "entry-id") is None


async def test_all_config_flow_form_schemas_are_probatio_serializable(hass):
    user_form = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert user_form["type"] is FlowResultType.FORM
    user_fields = to_field_list(
        user_form["data_schema"], custom_serializer=cv.custom_serializer
    )
    password = next(field for field in user_fields if field["name"] == "Password")
    assert password["selector"]["text"]["type"] == "password"
    assert password["selector"]["text"]["autocomplete"] == "current-password"
    version = next(field for field in user_fields if field["name"] == "Version")
    assert version["selector"]["select"]["options"] == [
        {"value": "5", "label": "WD OS 5"},
        {"value": "4", "label": "WD My Cloud V4"},
        {"value": "2", "label": "WD OS 2"},
    ]

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="NAS-SERIAL-1")
    entry.add_to_hass(hass)
    reauth_form = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    reauth_fields = to_field_list(
        reauth_form["data_schema"], custom_serializer=cv.custom_serializer
    )
    reauth_password = next(
        field for field in reauth_fields if field["name"] == "Password"
    )
    assert reauth_password["selector"]["text"]["type"] == "password"
    assert reauth_password["selector"]["text"]["autocomplete"] == "current-password"

    options_form = await hass.config_entries.options.async_init(entry.entry_id)
    assert options_form["type"] is FlowResultType.FORM
    options_fields = to_field_list(
        options_form["data_schema"], custom_serializer=cv.custom_serializer
    )
    release_session = next(
        field for field in options_fields if field["name"] == RELEASE_ADMIN_SESSION
    )
    assert release_session["default"] is False
    assert release_session["selector"] == {"boolean": {}}


async def test_os5_release_session_option_can_be_enabled_and_preserved(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={"update_interval": 120},
        unique_id="NAS-SERIAL-1",
    )
    entry.add_to_hass(hass)

    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {"update_interval": 120, RELEASE_ADMIN_SESSION: True},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "update_interval": 120,
        RELEASE_ADMIN_SESSION: True,
    }


@pytest.mark.parametrize("version", [2, 4])
async def test_release_session_option_is_not_offered_outside_os5(hass, version):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, "Version": version},
        unique_id=f"NAS-{version}",
    )
    entry.add_to_hass(hass)

    form = await hass.config_entries.options.async_init(entry.entry_id)
    fields = to_field_list(form["data_schema"], custom_serializer=cv.custom_serializer)

    assert RELEASE_ADMIN_SESSION not in {field["name"] for field in fields}
