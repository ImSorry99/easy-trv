import json
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.json import ExtendedJSONEncoder
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.easy_trv.adapters.zha_gateway import ZhaUnavailable
from custom_components.easy_trv.diagnostics import async_get_config_entry_diagnostics
from tests.helpers_entry import (
    FAST_OPTIONS,
    GATEWAY_FACTORY,
    IEEE_A,
    IEEE_B,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
    use_config_dir,
)

SETPOINT = 0x0012
MODE = 0x001C
OPTIONS = {**FAST_OPTIONS, "refresh_minutes": 30}


@pytest.fixture(autouse=True)
def fake_gateway(hass):
    with patched_gateway(hass) as gateway:
        yield gateway


@pytest.fixture
def entry(hass):
    return make_entry(hass, [room_data()], OPTIONS)


@pytest.mark.usefixtures("local_binding")
async def test_diagnostics_contains_rooms(hass, hass_client, entry) -> None:
    await start(hass, entry)

    # Through Home Assistant's diagnostics download, i.e. serialised to JSON.
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert set(diag["rooms"]) == set(entry.subentries)
    assert diag["profiles"] == ["sonoff_trvzb_native"]
    assert diag["profile_errors"] == []
    assert diag["options"] == OPTIONS
    settings = diag["rooms"][next(iter(entry.subentries))]["settings"]
    assert settings["hysteresis"] == 0.4
    assert settings["adaptive_mode"] == "on"


async def test_diagnostics_room_snapshot_has_config_params_and_state(
    hass, entry
) -> None:
    await start(hass, entry)
    subentry_id = next(iter(entry.subentries))

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert set(diag) == {"options", "profiles", "profile_errors", "rooms"}
    assert diag["options"] == OPTIONS
    assert diag["profiles"] == ["sonoff_trvzb_native"]
    assert diag["profile_errors"] == []
    assert set(diag["rooms"]) == {subentry_id}
    room = diag["rooms"][subentry_id]
    assert set(room) == {
        "config",
        "params",
        "state",
        "not_in_profile",
        "hold",
        "notes",
        "observations",
        "settings",
    }
    assert room["config"]["name"] == "Living"
    assert room["config"]["trvs"][0]["ieee"] == IEEE_A
    assert room["params"]["refresh"] == 1800.0
    assert room["state"]["source"] == "external"
    assert room["not_in_profile"] == {}
    # Plain data that Home Assistant can serialise for the download.
    json.dumps(diag, cls=ExtendedJSONEncoder)


async def test_diagnostics_include_resolved_room_settings(hass, entry) -> None:
    await start(hass, entry)
    subentry_id = next(iter(entry.subentries))

    diag = await async_get_config_entry_diagnostics(hass, entry)

    # The room sets its own hysteresis and window detection; adaptive mode and
    # frost temperature are built-in defaults; the refresh interval is the
    # global option.
    assert diag["rooms"][subentry_id]["settings"] == {
        "refresh_minutes": 30,
        "verify_delay_seconds": 0,
        "max_attempts": 3,
        "min_delta_kelvin": 0.1,
        "sensor_timeout_minutes": 120,
        "window_detection": "on",
        "hysteresis": 0.4,
        "adaptive_mode": "on",
        "frost_temperature": 5.0,
        "profile": "auto",
        "setpoint_min": 5.0,
        "setpoint_max": 30.0,
        "window_warn_minutes": 120,
        "window_check_minutes": 30,
        "window_check_drop_kelvin": 0.3,
        "outdoor_cutoff_celsius": None,
    }


async def test_diagnostics_lists_profile_errors(hass, tmp_path, entry) -> None:
    (use_config_dir(hass, tmp_path) / "broken.yaml").write_text("id: [unterminated")
    await start(hass, entry)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert [error["file"] for error in diag["profile_errors"]] == ["broken.yaml"]
    assert diag["profile_errors"][0]["error"]
    json.dumps(diag, cls=ExtendedJSONEncoder)


async def test_diagnostics_of_an_entry_that_failed_to_set_up(hass) -> None:
    failed = make_entry(hass, [room_data()], OPTIONS)
    with patch(GATEWAY_FACTORY, side_effect=ZhaUnavailable("no zha")):
        assert not await hass.config_entries.async_setup(failed.entry_id)
    assert failed.state is ConfigEntryState.SETUP_RETRY

    diag = await async_get_config_entry_diagnostics(hass, failed)

    assert diag == {
        "options": OPTIONS,
        "profiles": [],
        "profile_errors": [],
        "rooms": {},
    }


async def test_diagnostics_include_holds_and_observations(
    hass, fake_gateway, freezer
) -> None:
    freezer.move_to("2026-10-15T08:00:00+00:00")
    hass.states.async_set("binary_sensor.window", "off")
    entry = make_entry(
        hass, [room_data(window_contacts=["binary_sensor.window"])], OPTIONS
    )
    await start(hass, entry)
    subentry_id = next(iter(entry.subentries))
    # Twelve knob turns on one thermostat, then its schedule on the other.
    for step in range(12):
        fake_gateway.inject(IEEE_A, SETPOINT, 2000 + 50 * step)
    fake_gateway.inject(IEEE_B, MODE, 1)
    fake_gateway.inject(IEEE_B, MODE, 4, kind="written")
    hass.states.async_set("binary_sensor.window", "on")
    await settle(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    room = diag["rooms"][subentry_id]
    assert room["hold"] == {
        "window_open": True,
        "window_opened_at": "2026-10-15T08:00:00+00:00",
        "outdoor_hold": False,
    }
    assert room["notes"] == {}
    observations = room["observations"]
    assert set(observations) == {"climate.a", "climate.b"}
    # Only the last ten of each thermostat are kept, oldest first.
    assert [o["raw"] for o in observations["climate.a"]] == [
        2000 + 50 * step for step in range(2, 12)
    ]
    assert observations["climate.a"][0] == {
        "at": "2026-10-15T08:00:00+00:00",
        "capability": "setpoint",
        "raw": 2100,
        "kind": "report",
    }
    seen = [(o["capability"], o["raw"], o["kind"]) for o in observations["climate.b"]]
    assert seen == [("hvac_mode", 1, "report"), ("hvac_mode", 4, "written")]
    json.dumps(diag, cls=ExtendedJSONEncoder)


async def test_diagnostics_show_the_notes_of_a_thermostat(
    hass, fake_gateway, entry
) -> None:
    await start(hass, entry)
    subentry_id = next(iter(entry.subentries))
    fake_gateway.inject(IEEE_A, MODE, 1)
    await settle(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert diag["rooms"][subentry_id]["notes"] == {"climate.a": "schedule"}
