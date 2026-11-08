from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.easy_trv.const import DOMAIN
from tests.helpers_entry import (
    FAST_OPTIONS,
    advance,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)

WINDOW = "binary_sensor.living_window"
PRESENCE = "binary_sensor.living_presence"
CONTACT = "binary_sensor.contact"
OPTIONS = {**FAST_OPTIONS, "presence_entity": "person.a"}


async def _setup(hass, options=FAST_OPTIONS, **room):
    for entity_id in ("climate.a", "climate.b"):
        hass.states.async_set(entity_id, "heat", {"temperature": 21.0})
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("person.a", "home")
    entry = make_entry(hass, [room_data(**room)], options)
    await start(hass, entry)
    return entry


async def test_only_what_the_room_has(hass) -> None:
    with patched_gateway(hass):
        await _setup(hass)
        assert hass.states.get(WINDOW) is None
        assert hass.states.get(PRESENCE) is None


async def test_window_follows_the_contact_without_the_delay(hass) -> None:
    with patched_gateway(hass):
        entry = await _setup(
            hass, window_contacts=[CONTACT], window_open_delay_seconds=60
        )
        state = hass.states.get(WINDOW)
        assert state.state == "off"
        assert state.attributes["device_class"] == "window"
        assert state.attributes["contacts_open"] == []

        hass.states.async_set(CONTACT, "on")
        await settle(hass)
        state = hass.states.get(WINDOW)
        assert state.state == "on"
        assert state.attributes["contacts_open"] == [CONTACT]

        hass.states.async_set(CONTACT, "off")
        await settle(hass)
        assert hass.states.get(WINDOW).state == "off"

        registered = er.async_get(hass).async_get(WINDOW)
        subentry_id = next(iter(entry.subentries))
        assert registered.unique_id == f"{subentry_id}_window"
        assert registered.entity_category is None
        device = dr.async_get(hass).async_get(registered.device_id)
        assert device.identifiers == {(DOMAIN, subentry_id)}


async def test_presence_follows_the_entity_with_the_delay(hass, freezer) -> None:
    options = {**OPTIONS, "away_delay_minutes": 1}
    with patched_gateway(hass):
        entry = await _setup(hass, options)
        state = hass.states.get(PRESENCE)
        assert state.state == "on"
        assert state.attributes["device_class"] == "presence"
        assert state.attributes["source"] == "entity"
        assert state.attributes["presence_entity"] == "person.a"
        assert "away_from" not in state.attributes

        hass.states.async_set("person.a", "not_home")
        await settle(hass)
        state = hass.states.get(PRESENCE)
        assert state.state == "on"
        assert state.attributes["away_from"] is not None

        await advance(hass, freezer, 65)
        state = hass.states.get(PRESENCE)
        assert state.state == "off"
        assert "away_from" not in state.attributes

        registered = er.async_get(hass).async_get(PRESENCE)
        subentry_id = next(iter(entry.subentries))
        assert registered.unique_id == f"{subentry_id}_presence"
        assert registered.entity_category is None


async def test_presence_shows_the_forcing(hass) -> None:
    with patched_gateway(hass):
        entry = await _setup(hass, OPTIONS)
        await entry.runtime_data.house.async_set_option("away")
        await settle(hass)
        state = hass.states.get(PRESENCE)
        assert state.state == "off"
        assert state.attributes["source"] == "forced"

        await entry.runtime_data.house.async_set_option("home")
        await settle(hass)
        state = hass.states.get(PRESENCE)
        assert state.state == "on"
        assert state.attributes["source"] == "forced"

        await entry.runtime_data.house.async_set_option("auto")
        await settle(hass)
        assert hass.states.get(PRESENCE).attributes["source"] == "entity"


async def test_unavailable_until_the_room_has_started(hass) -> None:
    hass.states.async_set("climate.a", "heat", {"temperature": 21.0})
    hass.states.async_set("climate.b", "heat", {"temperature": 21.0})
    hass.states.async_set(CONTACT, "on")
    hass.states.async_set("person.a", "home")
    entry = make_entry(hass, [room_data(window_contacts=[CONTACT])], {**OPTIONS})
    hass.set_state(CoreState.starting)
    with patched_gateway(hass):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(WINDOW).state == "unavailable"
        assert hass.states.get(PRESENCE).state == "unavailable"

        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await settle(hass)
        assert hass.states.get(WINDOW).state == "on"
        assert hass.states.get(PRESENCE).state == "on"


async def test_second_contact_updates_the_list(hass) -> None:
    other = "binary_sensor.contact_b"
    hass.states.async_set(other, "off")
    with patched_gateway(hass):
        await _setup(
            hass, window_contacts=[CONTACT, other], window_open_delay_seconds=60
        )
        hass.states.async_set(CONTACT, "on")
        await settle(hass)
        hass.states.async_set(other, "on")
        await settle(hass)
        assert hass.states.get(WINDOW).attributes["contacts_open"] == [CONTACT, other]
        hass.states.async_set(other, "off")
        await settle(hass)
        assert hass.states.get(WINDOW).attributes["contacts_open"] == [CONTACT]
