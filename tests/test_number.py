from types import MappingProxyType
from unittest.mock import patch

from homeassistant.components.number import ATTR_VALUE
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EntityCategory
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.easy_trv.const import DOMAIN, SUBENTRY_ROOM
from tests.helpers_entry import (
    FAST_OPTIONS,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)

NUMBER = "number.easy_trv_away_setback"
OPTIONS = {
    **FAST_OPTIONS,
    "presence_entity": "person.a",
    "away_delay_minutes": 0,
    "away_setback_kelvin": 3.0,
}


def _states(hass, person: str = "not_home") -> None:
    hass.states.async_set("person.a", person)
    for entity_id in ("climate.a", "climate.b"):
        hass.states.async_set(entity_id, "heat", {"temperature": 21.0})


async def _setup(hass, rooms=None, options=None):
    entry = make_entry(hass, rooms or [room_data()], options or OPTIONS)
    await start(hass, entry)
    # The wish is set at home; set while away it would override the presence.
    home = hass.states.get("person.a").state
    hass.states.async_set("person.a", "home")
    await settle(hass)
    for room in entry.runtime_data.rooms.values():
        await room.async_set_setpoint(21.0)
    hass.states.async_set("person.a", home)
    await settle(hass)
    return entry


def _room(entry, name="Living"):
    for subentry_id, controller in entry.runtime_data.rooms.items():
        if entry.subentries[subentry_id].title == name:
            return controller
    raise AssertionError(name)


async def test_the_number_sits_on_the_house_device(hass) -> None:
    _states(hass)
    with patched_gateway(hass):
        entry = await _setup(hass)
        state = hass.states.get(NUMBER)
        assert float(state.state) == 3.0
        assert state.attributes["min"] == 0.5
        assert state.attributes["max"] == 10.0
        assert state.attributes["step"] == 0.5
        assert state.attributes["unit_of_measurement"] == "K"
        assert state.attributes["mode"] == "box"
        assert state.attributes["rooms_with_own_setback"] == []

        registered = er.async_get(hass).async_get(NUMBER)
        assert registered.unique_id == f"{entry.entry_id}_away_setback"
        assert registered.entity_category is EntityCategory.CONFIG
        device = dr.async_get(hass).async_get(registered.device_id)
        assert device.identifiers == {(DOMAIN, entry.entry_id)}


async def test_the_number_shows_the_default_without_the_option(hass) -> None:
    _states(hass)
    with patched_gateway(hass):
        await _setup(hass, options=FAST_OPTIONS)
        assert float(hass.states.get(NUMBER).state) == 3.0


async def test_setting_the_number_writes_the_option_without_a_reload(hass) -> None:
    _states(hass, "home")
    with patched_gateway(hass):
        entry = await _setup(hass)
        before = entry.runtime_data
        await hass.services.async_call(
            NUMBER_DOMAIN,
            "set_value",
            {"entity_id": NUMBER, ATTR_VALUE: 2.0},
            blocking=True,
        )
        await settle(hass)

        assert entry.options["away_setback_kelvin"] == 2.0
        assert entry.runtime_data is before
        assert float(hass.states.get(NUMBER).state) == 2.0


async def test_live_keys_apply_without_reload(hass) -> None:
    _states(hass)
    with patched_gateway(hass) as gateway:
        entry = await _setup(hass)
        room = _room(entry)
        assert room.presence == "away"
        assert room.away_target is not None
        lowered = room.away_target
        writes = len(gateway.writes)

        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            await hass.services.async_call(
                NUMBER_DOMAIN,
                "set_value",
                {"entity_id": NUMBER, ATTR_VALUE: 1.0},
                blocking=True,
            )
            await settle(hass)

        reload.assert_not_called()
        assert room.away_target == lowered + 2.0
        assert len(gateway.writes) > writes


async def test_delay_change_applies_to_running_inputs(hass) -> None:
    _states(hass, "home")
    with patched_gateway(hass):
        entry = await _setup(hass)
        room = _room(entry)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            hass.config_entries.async_update_entry(
                entry, options={**OPTIONS, "away_delay_minutes": 30}
            )
            await settle(hass)
        reload.assert_not_called()

        hass.states.async_set("person.a", "not_home")
        await settle(hass)
        assert room.presence == "home"
        assert room.presence_away_from is not None


async def test_other_change_reloads(hass) -> None:
    _states(hass, "home")
    with patched_gateway(hass):
        entry = await _setup(hass)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            hass.config_entries.async_update_entry(
                entry, options={**OPTIONS, "refresh_minutes": 45}
            )
            await settle(hass)
        reload.assert_called_once_with(entry.entry_id)


async def test_live_and_other_change_together_reloads(hass) -> None:
    _states(hass, "home")
    with patched_gateway(hass):
        entry = await _setup(hass)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            hass.config_entries.async_update_entry(
                entry,
                options={**OPTIONS, "refresh_minutes": 45, "away_setback_kelvin": 2.0},
            )
            await settle(hass)
        reload.assert_called_once_with(entry.entry_id)


async def test_subentry_change_reloads(hass) -> None:
    _states(hass, "home")
    with patched_gateway(hass):
        entry = await _setup(hass)
        subentry = next(iter(entry.subentries.values()))
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            hass.config_entries.async_update_subentry(
                entry, subentry, data={**subentry.data, "away_setback_kelvin": 1.5}
            )
            await settle(hass)
        reload.assert_called_once_with(entry.entry_id)


async def test_added_subentry_reloads(hass) -> None:
    _states(hass, "home")
    with patched_gateway(hass):
        entry = await _setup(hass)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            hass.config_entries.async_add_subentry(
                entry,
                ConfigSubentry(
                    data=MappingProxyType(room_data("Other")),
                    subentry_type=SUBENTRY_ROOM,
                    title="Other",
                    unique_id="other",
                ),
            )
            await settle(hass)
        reload.assert_called_once_with(entry.entry_id)


async def test_room_with_own_setback_keeps_it(hass) -> None:
    _states(hass)
    rooms = [
        room_data("Living"),
        room_data(
            "Study",
            trvs=["climate.a"],
            temp_sensors=["sensor.t1"],
            away_setback_kelvin=2.0,
        ),
    ]
    with patched_gateway(hass):
        entry = await _setup(hass, rooms)
        study = _room(entry, "Study")
        living = _room(entry, "Living")
        study_target = study.away_target
        living_target = living.away_target
        assert hass.states.get(NUMBER).attributes["rooms_with_own_setback"] == ["Study"]

        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            await hass.services.async_call(
                NUMBER_DOMAIN,
                "set_value",
                {"entity_id": NUMBER, ATTR_VALUE: 1.0},
                blocking=True,
            )
            await settle(hass)

        reload.assert_not_called()
        assert study.away_target == study_target
        assert living.away_target == living_target + 2.0


async def test_options_form_change_updates_the_number(hass) -> None:
    _states(hass, "home")
    with patched_gateway(hass):
        entry = await _setup(hass)
        before = entry.runtime_data
        hass.config_entries.async_update_entry(
            entry, options={**OPTIONS, "away_setback_kelvin": 2.0}
        )
        await settle(hass)
        assert float(hass.states.get(NUMBER).state) == 2.0
        assert entry.runtime_data is before
