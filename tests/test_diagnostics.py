import json

import pytest
from homeassistant.helpers.json import ExtendedJSONEncoder
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from custom_components.easy_trv.diagnostics import async_get_config_entry_diagnostics
from tests.helpers_entry import (
    FAST_OPTIONS,
    IEEE_A,
    make_entry,
    patched_gateway,
    room_data,
    start,
    use_config_dir,
)

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
    assert set(room) == {"config", "params", "state"}
    assert room["config"]["name"] == "Living"
    assert room["config"]["trvs"][0]["ieee"] == IEEE_A
    assert room["params"]["refresh"] == 1800.0
    assert room["state"]["source"] == "external"
    # Plain data that Home Assistant can serialise for the download.
    json.dumps(diag, cls=ExtendedJSONEncoder)


async def test_diagnostics_lists_profile_errors(hass, tmp_path, entry) -> None:
    (use_config_dir(hass, tmp_path) / "broken.yaml").write_text("id: [unterminated")
    await start(hass, entry)

    diag = await async_get_config_entry_diagnostics(hass, entry)

    assert [error["file"] for error in diag["profile_errors"]] == ["broken.yaml"]
    assert diag["profile_errors"][0]["error"]
    json.dumps(diag, cls=ExtendedJSONEncoder)


async def test_diagnostics_of_an_entry_that_failed_to_set_up(
    hass, fake_gateway
) -> None:
    fake_gateway.add_device("climate.x", "00:33", model="TRV-ZBT")
    hass.states.async_set("climate.x", "heat")
    failed = make_entry(hass, [room_data(trvs=["climate.x"])], OPTIONS)
    assert not await hass.config_entries.async_setup(failed.entry_id)

    diag = await async_get_config_entry_diagnostics(hass, failed)

    assert diag == {
        "options": OPTIONS,
        "profiles": [],
        "profile_errors": [],
        "rooms": {},
    }
