from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.easy_trv.house import HousePresence
from tests.helpers_entry import (
    FAST_OPTIONS,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)

EVENT = "easy_trv_presence_forced"
OPTIONS = {**FAST_OPTIONS, "presence_entity": "person.a"}


def _states(hass, away: str = "home") -> None:
    hass.states.async_set("person.a", away)
    for entity_id in ("climate.a", "climate.b"):
        hass.states.async_set(entity_id, "heat", {"temperature": 21.0})


async def _entry(hass, rooms=None, options=None):
    entry = make_entry(hass, rooms or [room_data()], options or OPTIONS)
    await start(hass, entry)
    return entry


def _room(entry, name="Living"):
    for subentry_id, controller in entry.runtime_data.rooms.items():
        if entry.subentries[subentry_id].title == name:
            return controller
    raise AssertionError(name)


async def test_forced_away_overrides_at_once_without_delay(hass) -> None:
    _states(hass)
    with patched_gateway(hass):
        entry = await _entry(hass)
        house = entry.runtime_data.house
        room = _room(entry)
        assert room.presence == "home"
        assert room.presence_source == "entity"

        await house.async_set_option("away")
        await settle(hass)

        assert room.presence == "away"
        assert room.presence_source == "forced"
        assert house.forced_away() is True
        assert house.forced_at is not None


async def test_forced_home_overrides_a_running_delay(hass) -> None:
    _states(hass, "not_home")
    with patched_gateway(hass):
        entry = await _entry(hass)
        room = _room(entry)
        assert room.presence == "home"
        assert room.presence_away_from is not None

        await entry.runtime_data.house.async_set_option("home")
        await settle(hass)

        assert room.presence == "home"
        assert room.presence_away_from is None
        assert entry.runtime_data.house.forced_away() is False


async def test_rooms_without_presence_are_untouched(hass) -> None:
    _states(hass)
    with patched_gateway(hass):
        entry = await _entry(
            hass,
            [room_data(), room_data("Study", presence="none", trvs=["climate.a"])],
        )
        study = _room(entry, "Study")
        await entry.runtime_data.house.async_set_option("away")
        await settle(hass)

        assert study.presence == "none"
        assert study.presence_source is None


async def test_global_entity_change_returns_to_auto(hass) -> None:
    _states(hass)
    with patched_gateway(hass):
        entry = await _entry(hass)
        house = entry.runtime_data.house
        events = async_capture_events(hass, EVENT)
        await house.async_set_option("away")
        await settle(hass)

        hass.states.async_set("person.a", "not_home")
        await settle(hass)

        assert house.option == "auto"
        assert house.forced_at is None
        assert [e.data for e in events] == [
            {"option": "away", "reason": "user"},
            {"option": "auto", "reason": "presence_changed"},
        ]


async def test_unknown_policy_applies_before_the_change_counts(hass) -> None:
    _states(hass)
    with patched_gateway(hass):
        entry = await _entry(hass, options={**OPTIONS, "presence_unknown": "home"})
        house = entry.runtime_data.house
        await house.async_set_option("away")
        hass.states.async_set("person.a", "unavailable")
        await settle(hass)
        assert house.option == "away"


async def test_own_room_entity_change_keeps_forcing(hass) -> None:
    _states(hass)
    hass.states.async_set("person.b", "home")
    with patched_gateway(hass):
        entry = await _entry(
            hass, [room_data(presence="own", presence_entity="person.b")]
        )
        house = entry.runtime_data.house
        room = _room(entry)
        await house.async_set_option("away")
        await settle(hass)
        assert room.presence == "away"

        hass.states.async_set("person.b", "not_home")
        await settle(hass)

        assert house.option == "away"
        assert room.presence == "away"


async def test_without_global_entity_forcing_stays(hass) -> None:
    hass.states.async_set("person.b", "home")
    for entity_id in ("climate.a", "climate.b"):
        hass.states.async_set(entity_id, "heat", {"temperature": 21.0})
    with patched_gateway(hass):
        entry = await _entry(
            hass,
            [room_data(presence="own", presence_entity="person.b")],
            FAST_OPTIONS,
        )
        house = entry.runtime_data.house
        room = _room(entry)
        await house.async_set_option("away")
        hass.states.async_set("person.a", "not_home")
        hass.states.async_set("person.b", "not_home")
        hass.states.async_set("person.b", "home")
        await settle(hass)
        assert house.option == "away"
        assert room.presence == "away"
        assert house.raw_away is None


async def test_restore_with_same_presence_keeps_option(hass) -> None:
    _states(hass)
    with patched_gateway(hass):
        entry = await _entry(hass)
        house = entry.runtime_data.house
        house.stop()
        fresh = HousePresence(hass, entry.entry_id, "person.a", unknown_is_away=True)
        forced_at = dt_util.utcnow() - timedelta(hours=1)
        fresh.restore("away", forced_at, raw_away=False)
        fresh.start()
        assert (fresh.option, fresh.forced_at) == ("away", forced_at)
        fresh.stop()


async def test_restore_with_changed_presence_returns_to_auto(hass) -> None:
    _states(hass, "not_home")
    house = HousePresence(hass, "entry", "person.a", unknown_is_away=True)
    events = async_capture_events(hass, EVENT)
    house.restore("home", dt_util.utcnow(), raw_away=False)
    house.start()
    await settle(hass)
    assert (house.option, house.forced_at) == ("auto", None)
    assert [e.data for e in events] == [
        {"option": "auto", "reason": "presence_changed"}
    ]
    house.stop()


async def _restored_unavailable(hass):
    hass.states.async_set("person.a", "unavailable")
    house = HousePresence(hass, "entry", "person.a", unknown_is_away=False)
    events = async_capture_events(hass, EVENT)
    house.restore("away", dt_util.utcnow(), raw_away=True)
    house.start()
    await settle(hass)
    assert house.option == "away"
    return house, events


async def test_restore_waits_for_the_first_real_state(hass) -> None:
    house, events = await _restored_unavailable(hass)
    hass.states.async_set("person.a", "not_home")
    await settle(hass)
    assert house.option == "away"
    assert events == []
    house.stop()


async def test_restore_ends_when_the_first_real_state_differs(hass) -> None:
    house, events = await _restored_unavailable(hass)
    hass.states.async_set("person.a", "home")
    await settle(hass)
    assert house.option == "auto"
    assert [e.data for e in events] == [
        {"option": "auto", "reason": "presence_changed"}
    ]
    house.stop()
