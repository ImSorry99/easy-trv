import logging
from types import MappingProxyType
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.easy_trv.adapters.zha_gateway import ZhaUnavailable
from custom_components.easy_trv.const import DOMAIN, SUBENTRY_ROOM
from custom_components.easy_trv.controller import RoomController
from custom_components.easy_trv.profiles.loader import (
    BUILTIN_DIR,
)
from tests.helpers_entry import (
    CELSIUS,
    FAST_OPTIONS,
    GATEWAY_FACTORY,
    advance,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
    use_config_dir,
)

VALUE = 0x600D
BUILTIN_TEXT = (BUILTIN_DIR / "sonoff_trvzb_native.yaml").read_text()


def zha_issue(hass):
    return ir.async_get(hass).async_get_issue(DOMAIN, "zha_api_incompatible")


def value_writes(gateway) -> list[int]:
    """Return the room temperatures written so far (raw centi-degrees)."""
    return [w[4] for w in gateway.writes if w[3] == VALUE]


@pytest.fixture(autouse=True)
def fake_gateway(hass):
    with patched_gateway(hass) as gateway:
        yield gateway


@pytest.fixture
def entry(hass):
    return make_entry(hass, [room_data()], FAST_OPTIONS)


# --- Setup ----------------------------------------------------------------


async def test_setup_creates_room_device_and_status_sensor(hass, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get("sensor.living_status")
    assert state is not None
    assert state.state == "ok"
    assert state.attributes["source"] == "external"

    subentry_id = next(iter(entry.subentries))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry_id), entry.entry_id
    )
    assert device is not None
    assert device.name == "Living"
    # Exactly one entity per room, attached to the room's subentry.
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert [e.entity_id for e in entities] == ["sensor.living_status"]
    assert entities[0].unique_id == f"{subentry_id}_status"
    assert entities[0].config_subentry_id == subentry_id
    assert entry.runtime_data.rooms.keys() == {subentry_id}
    assert zha_issue(hass) is None


async def test_rooms_start_only_when_home_assistant_has_started(
    hass, entry, fake_gateway
) -> None:
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    state = hass.states.get("sensor.living_status")
    assert state is not None
    assert "source" not in state.attributes
    assert fake_gateway.writes == []

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)
    state = hass.states.get("sensor.living_status")
    assert state.attributes["source"] == "external"
    assert fake_gateway.writes


async def test_unload_before_start_cancels_the_pending_start(
    hass, entry, fake_gateway
) -> None:
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    waiting = hass.bus.async_listeners().get(EVENT_HOMEASSISTANT_STARTED, 0)
    assert await hass.config_entries.async_unload(entry.entry_id)
    # Our wait for the start of Home Assistant is gone.
    assert hass.bus.async_listeners().get(EVENT_HOMEASSISTANT_STARTED, 0) == (
        waiting - 1
    )

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)
    assert fake_gateway.writes == []


async def test_setup_zha_unavailable_retries_and_clears_issue(hass, entry) -> None:
    with patch(GATEWAY_FACTORY, side_effect=ZhaUnavailable("no zha")):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = zha_issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {"error": "no zha"}

    # ZHA comes up: the retry succeeds and the issue disappears.
    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert zha_issue(hass) is None
    assert hass.states.get("sensor.living_status").state == "ok"


async def test_profile_auto_selects_builtin_and_rejects_unmatched(
    hass, fake_gateway
) -> None:
    fake_gateway.add_device("climate.x", "00:33", model="TRV-ZBT")
    hass.states.async_set("climate.x", "heat")
    foreign = make_entry(hass, [room_data(trvs=["climate.x"])], FAST_OPTIONS)

    assert not await hass.config_entries.async_setup(foreign.entry_id)
    assert foreign.state is ConfigEntryState.SETUP_ERROR
    assert "climate.x (SONOFF TRV-ZBT)" in foreign.reason
    assert hass.states.get("sensor.living_status") is None


async def test_non_zha_thermostat_is_a_setup_error(hass) -> None:
    hass.states.async_set("climate.mqtt", "heat")
    entry = make_entry(hass, [room_data(trvs=["climate.a", "climate.mqtt"])])

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "climate.mqtt is not a ZHA thermostat" in entry.reason


async def test_explicit_profile_is_used(hass) -> None:
    entry = make_entry(hass, [room_data(profile="sonoff_trvzb_native")], FAST_OPTIONS)
    await start(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.living_status").state == "ok"


async def test_unknown_explicit_profile_is_a_setup_error(hass) -> None:
    entry = make_entry(hass, [room_data(profile="no_such_profile")])

    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "no_such_profile" in entry.reason


async def test_user_profile_covers_a_foreign_thermostat(
    hass, tmp_path, fake_gateway
) -> None:
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_zbt", 1)
    text = text.replace("model: TRVZB", "model: TRV-ZBT", 1)
    use_config_dir(hass, tmp_path).joinpath("zbt.yaml").write_text(text)
    fake_gateway.add_device("climate.x", "00:33", model="TRV-ZBT")
    hass.states.async_set("climate.x", "heat")
    entry = make_entry(hass, [room_data(trvs=["climate.x"])], FAST_OPTIONS)

    await start(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert set(entry.runtime_data.profiles.profiles) == {
        "sonoff_trvzb_native",
        "user_zbt",
    }


async def test_invalid_user_profile_raises_issue_and_setup_still_succeeds(
    hass, tmp_path, entry
) -> None:
    profiles_dir = use_config_dir(hass, tmp_path)
    (profiles_dir / "broken.yaml").write_text("schema: 1\nid: [unterminated")

    await start(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "profile_invalid_broken")
    assert issue is not None
    assert issue.translation_placeholders["file"] == "broken.yaml"
    assert issue.translation_placeholders["error"]
    assert [e.file for e in entry.runtime_data.profiles.errors] == ["broken.yaml"]

    # Fixing the file and reloading removes the issue.
    (profiles_dir / "broken.yaml").unlink()
    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert ir.async_get(hass).async_get_issue(DOMAIN, "profile_invalid_broken") is None


async def test_start_is_logged_and_one_failing_room_does_not_block_the_others(
    hass, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="custom_components.easy_trv")
    entry = make_entry(
        hass,
        [
            room_data("Living", trvs=["climate.a"]),
            room_data("Bedroom", trvs=["climate.b"], temp_sensors=["sensor.t2"]),
        ],
        FAST_OPTIONS,
    )
    original = RoomController.async_start

    async def flaky_start(self) -> None:
        if self.snapshot()["config"]["name"] == "Living":
            msg = "boom"
            raise RuntimeError(msg)
        await original(self)

    with patch.object(RoomController, "async_start", flaky_start):
        await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert "Room Living (1 thermostat(s), profile sonoff_trvzb_native) could not" in (
        caplog.text
    )
    assert "Started room Bedroom (1 thermostat(s), profile sonoff_trvzb_native)" in (
        caplog.text
    )
    assert hass.states.get("sensor.bedroom_status").attributes["source"] == "external"
    assert "source" not in hass.states.get("sensor.living_status").attributes


async def test_subentries_of_other_types_are_ignored(hass) -> None:
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType({"anything": 1}),
            subentry_type="not_a_room",
            title="Other",
            unique_id="other",
        ),
    )

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert len(entry.runtime_data.rooms) == 1
    assert hass.states.get("sensor.living_status") is not None
    assert hass.states.get("sensor.other_status") is None


# --- Reload on change -----------------------------------------------------


async def test_options_change_reloads(hass, entry) -> None:
    await start(hass, entry)
    before = entry.runtime_data
    subentry_id = next(iter(entry.subentries))
    assert before.rooms[subentry_id].snapshot()["params"]["refresh"] == 1200.0

    hass.config_entries.async_update_entry(
        entry, options={**FAST_OPTIONS, "refresh_minutes": 30}
    )
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not before
    new_room = entry.runtime_data.rooms[subentry_id]
    assert new_room.snapshot()["params"]["refresh"] == 1800.0
    assert hass.states.get("sensor.living_status").attributes["source"] == "external"


async def test_adding_a_room_reloads_and_adds_its_status_sensor(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)
    fake_gateway.add_device("climate.c", "00:33")
    hass.states.async_set("climate.c", "heat")

    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType(
                room_data("Bedroom", trvs=["climate.c"], temp_sensors=["sensor.t2"])
            ),
            subentry_type=SUBENTRY_ROOM,
            title="Bedroom",
            unique_id="bedroom",
        ),
    )
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert len(entry.runtime_data.rooms) == 2
    assert hass.states.get("sensor.living_status").state == "ok"
    assert hass.states.get("sensor.bedroom_status").state == "ok"


# --- Unload ---------------------------------------------------------------


async def test_unload_stops_the_controllers(hass, freezer, entry, fake_gateway) -> None:
    await start(hass, entry)
    fake_gateway.writes.clear()
    # Control: while loaded, a changed sensor value is written to the TRVs.
    hass.states.async_set("sensor.t1", "22.0", CELSIUS)
    await advance(hass, freezer, 10)
    assert value_writes(fake_gateway) == [2200, 2200]

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get("sensor.living_status").state == "unavailable"

    fake_gateway.writes.clear()
    fake_gateway.reads.clear()
    hass.states.async_set("sensor.t1", "23.5", CELSIUS)
    hass.states.async_set("climate.a", "unavailable")
    await advance(hass, freezer, 130)
    assert fake_gateway.writes == []
    assert fake_gateway.reads == []
