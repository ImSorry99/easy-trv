from homeassistant.core import State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    mock_restore_cache_with_extra_data,
)

from custom_components.easy_trv.const import DOMAIN
from tests.helpers_entry import (
    FAST_OPTIONS,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)

SELECT = "select.easy_trv_presence"
OPTIONS = {**FAST_OPTIONS, "presence_entity": "person.a"}


async def _setup(hass, options=OPTIONS):
    hass.states.async_set("person.a", "home")
    entry = make_entry(hass, [room_data()], options)
    await start(hass, entry)
    return entry


async def test_the_select_sits_on_the_house_device(hass) -> None:
    with patched_gateway(hass):
        entry = await _setup(hass)
        state = hass.states.get(SELECT)
        assert state.state == "auto"
        assert state.attributes["options"] == ["auto", "home", "away"]
        assert state.attributes["presence_entity"] == "person.a"
        assert "forced_at" not in state.attributes

        registered = er.async_get(hass).async_get(SELECT)
        assert registered.unique_id == f"{entry.entry_id}_presence"
        device = dr.async_get(hass).async_get(registered.device_id)
        assert device.identifiers == {(DOMAIN, entry.entry_id)}
        assert (device.manufacturer, device.name) == ("Easy TRV", "Easy TRV")


async def test_select_option_forces_and_fires_the_event(hass) -> None:
    with patched_gateway(hass):
        await _setup(hass)
        events = async_capture_events(hass, "easy_trv_presence_forced")
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": SELECT, "option": "away"},
            blocking=True,
        )
        await settle(hass)

        state = hass.states.get(SELECT)
        assert state.state == "away"
        assert "forced_at" in state.attributes
        assert [e.data for e in events] == [{"option": "away", "reason": "user"}]


async def test_no_global_entity_no_attribute(hass) -> None:
    with patched_gateway(hass):
        await _setup(hass, FAST_OPTIONS)
        assert "presence_entity" not in hass.states.get(SELECT).attributes


def _restore_away(hass) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(SELECT, "away"),
                {"forced_at": "2026-11-07T08:00:00+00:00", "raw_away": True},
            )
        ],
    )


async def test_restore_keeps_away_when_presence_is_the_same(hass) -> None:
    _restore_away(hass)
    hass.states.async_set("person.a", "not_home")
    for entity_id in ("climate.a", "climate.b"):
        hass.states.async_set(entity_id, "heat", {"temperature": 21.0})
    events = async_capture_events(hass, "easy_trv_presence_forced")
    with patched_gateway(hass):
        entry = make_entry(hass, [room_data()], OPTIONS)
        await start(hass, entry)

        state = hass.states.get(SELECT)
        assert state.state == "away"
        assert state.attributes["forced_at"] == "2026-11-07T08:00:00+00:00"
        room = next(iter(entry.runtime_data.rooms.values()))
        assert room.presence == "away"
        assert room.presence_source == "forced"
        assert events == []


async def test_restore_returns_to_auto_when_presence_changed(hass) -> None:
    _restore_away(hass)
    hass.states.async_set("person.a", "home")
    for entity_id in ("climate.a", "climate.b"):
        hass.states.async_set(entity_id, "heat", {"temperature": 21.0})
    events = async_capture_events(hass, "easy_trv_presence_forced")
    with patched_gateway(hass):
        entry = make_entry(hass, [room_data()], OPTIONS)
        await start(hass, entry)

        assert hass.states.get(SELECT).state == "auto"
        assert "forced_at" not in hass.states.get(SELECT).attributes
        room = next(iter(entry.runtime_data.rooms.values()))
        assert room.presence == "home"
        assert [e.data for e in events] == [
            {"option": "auto", "reason": "presence_changed"}
        ]


async def test_reload_while_running_keeps_the_forced_option(hass) -> None:
    with patched_gateway(hass):
        entry = await _setup(hass)
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": SELECT, "option": "away"},
            blocking=True,
        )
        await settle(hass)

        assert await hass.config_entries.async_reload(entry.entry_id)
        await settle(hass)

        assert hass.states.get(SELECT).state == "away"
        room = next(iter(entry.runtime_data.rooms.values()))
        assert room.presence == "away"
