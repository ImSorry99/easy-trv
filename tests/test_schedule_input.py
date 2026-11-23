"""The schedule helper of a room: reading it, feeding it, and the room using it."""

import logging

import pytest
from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    SERVICE_SET_TEMPERATURE,
)
from homeassistant.core import State
from homeassistant.helpers.restore_state import async_get as async_restore_data
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.easy_trv.observation import ScheduleInput, read_schedule
from tests.helpers_entry import (
    FAST_OPTIONS,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)
from tests.test_climate import CLIMATE, call

SCHEDULE = "schedule.bedroom"
STATE_SENSOR = "sensor.living_heating_state"


@pytest.mark.parametrize(
    ("state", "attributes", "expected"),
    [
        ("on", {}, (True, None)),
        ("on", {"temperature": 21.5}, (True, 21.5)),
        ("on", {"temperature": "20"}, (True, 20.0)),
        ("off", {"temperature": 21.5}, (False, None)),
        ("unavailable", {}, (True, None)),
        ("unknown", {}, (True, None)),
        (None, {}, (True, None)),
    ],
)
def test_read_schedule(state, attributes, expected) -> None:
    read = None if state is None else State(SCHEDULE, state, attributes)
    assert read_schedule(SCHEDULE, read) == expected


@pytest.mark.parametrize("bad", ["warm", True, float("nan"), [21]])
def test_a_block_temperature_that_is_no_number_is_ignored(bad, caplog) -> None:
    with caplog.at_level(logging.WARNING):
        assert read_schedule(SCHEDULE, State(SCHEDULE, "on", {"temperature": bad})) == (
            True,
            None,
        )
    assert "is not a number" in caplog.text


async def test_schedule_input_feeds_changes_only(hass) -> None:
    fed: list = []
    hass.states.async_set(SCHEDULE, "off")
    tracker = ScheduleInput(hass, SCHEDULE, fed.append)

    assert tracker.start() == (False, None)
    for state, attributes in (
        ("on", {}),
        ("on", {"next_event": "x"}),  # nothing that matters changed
        ("on", {"temperature": 22.0}),
        ("off", {}),
    ):
        hass.states.async_set(SCHEDULE, state, attributes)
        await hass.async_block_till_done()
    tracker.stop()
    hass.states.async_set(SCHEDULE, "on")
    await hass.async_block_till_done()

    assert [(e.on, e.temperature) for e in fed] == [
        (True, None),
        (True, 22.0),
        (False, None),
    ]
    assert tracker.on is False


@pytest.fixture(autouse=True)
def fake_gateway(hass):
    with patched_gateway(hass) as gateway:
        for entity_id in ("climate.a", "climate.b"):
            hass.states.async_set(entity_id, "heat", {"temperature": 21.0})
        yield gateway


async def test_a_room_follows_its_schedule(hass) -> None:
    hass.states.async_set(SCHEDULE, "on", {"friendly_name": "Bedroom plan"})
    entry = make_entry(hass, [room_data(schedule_entity=SCHEDULE)], FAST_OPTIONS)
    await start(hass, entry)

    attributes = hass.states.get(CLIMATE).attributes
    assert attributes["schedule_entity"] == SCHEDULE
    assert (attributes["schedule"], attributes["away_target"]) == ("on", None)

    hass.states.async_set(SCHEDULE, "off", {"friendly_name": "Bedroom plan"})
    await settle(hass)
    attributes = hass.states.get(CLIMATE).attributes
    assert (attributes["schedule"], attributes["away_target"]) == ("off", 18.0)
    sensor = hass.states.get(STATE_SENSOR)
    assert sensor.state == "setback_schedule"
    assert sensor.attributes["hint"] == "Bedroom plan off"

    hass.states.async_set(SCHEDULE, "on", {"temperature": 22.5})
    await settle(hass)
    attributes = hass.states.get(CLIMATE).attributes
    assert attributes[ATTR_TEMPERATURE] == 22.5
    assert attributes["setpoint_source"] == "schedule"
    assert hass.states.get(STATE_SENSOR).state == "heating"


async def test_a_target_set_while_off_is_kept_across_a_restart(hass) -> None:
    hass.states.async_set(SCHEDULE, "off")
    entry = make_entry(hass, [room_data(schedule_entity=SCHEDULE)], FAST_OPTIONS)
    await start(hass, entry)

    await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 22.0})
    attributes = hass.states.get(CLIMATE).attributes
    assert (attributes["schedule_override"], attributes["away_target"]) == (True, None)
    stored = {
        stored.state.entity_id: stored.extra_data
        for stored in async_restore_data(hass).async_get_stored_states()
    }
    assert stored[CLIMATE].as_dict()["schedule_override"] is True


async def test_a_restored_override_holds_while_off(hass) -> None:
    hass.states.async_set(SCHEDULE, "off")
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(CLIMATE, "heat", {ATTR_TEMPERATURE: 22.0}),
                {"frost_return": None, "preset": "none", "schedule_override": True},
            )
        ],
    )
    entry = make_entry(hass, [room_data(schedule_entity=SCHEDULE)], FAST_OPTIONS)
    await start(hass, entry)

    attributes = hass.states.get(CLIMATE).attributes
    assert (attributes["schedule_override"], attributes["away_target"]) == (True, None)


@pytest.mark.parametrize(
    ("room", "expected"),
    [({}, 16.0), ({"schedule_setback_kelvin": 10.0}, 11.0)],
)
async def test_the_schedule_setback_comes_from_the_settings(
    hass, room, expected
) -> None:
    hass.states.async_set(SCHEDULE, "off")
    options = {**FAST_OPTIONS, "schedule_setback_kelvin": 5.0}
    entry = make_entry(hass, [room_data(schedule_entity=SCHEDULE, **room)], options)
    await start(hass, entry)

    assert hass.states.get(CLIMATE).attributes["away_target"] == expected


async def test_a_room_without_schedule_shows_none(hass) -> None:
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)
    await start(hass, entry)

    attributes = hass.states.get(CLIMATE).attributes
    assert attributes["schedule_entity"] is None
    assert attributes["schedule"] is None
    assert attributes["schedule_override"] is False
