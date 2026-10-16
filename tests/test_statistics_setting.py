"""The setting "Collect statistics": off, hidden or visible, globally or per room."""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from custom_components.easy_trv.const import DOMAIN
from custom_components.easy_trv.visibility import MARKER
from tests.helpers_entry import (
    FAST_OPTIONS,
    FIRMWARE,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)

INTEGRATION = er.RegistryEntryHider.INTEGRATION
USER = er.RegistryEntryHider.USER

STATE_ENTITY = "sensor.living_heating_state"
BEDROOM_STATE_ENTITY = "sensor.bedroom_heating_state"
A_WRITES = "sensor.a_writes"
LIVING = {
    "sensor.living_deviation",
    "sensor.living_heating_time",
    *(
        f"sensor.{trv}_{key}"
        for trv in ("a", "b")
        for key in ("writes", "write_failures", "dropouts")
    ),
}
BEDROOM = {
    "sensor.bedroom_deviation",
    "sensor.bedroom_heating_time",
    *(f"sensor.c_{key}" for key in ("writes", "write_failures", "dropouts")),
}


@pytest.fixture(autouse=True)
def fake_gateway(hass):
    with patched_gateway(hass) as gateway:
        yield gateway


@pytest.fixture
def two_rooms(hass, fake_gateway):
    """Return the rooms Living (a, b) and Bedroom (c) to build an entry from."""
    fake_gateway.add_device("climate.c", "00:33", firmware=FIRMWARE)
    hass.states.async_set("climate.c", "heat")
    return [
        room_data("Living"),
        room_data("Bedroom", trvs=["climate.c"], temp_sensors=["sensor.t2"]),
    ]


def options(**values: str):
    return {**FAST_OPTIONS, **values}


def statistics(hass, entry) -> dict[str, er.RegistryEntry]:
    """Return the statistics sensors of the entry in the registry, by entity id."""
    return {
        found.entity_id: found
        for found in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if found.domain == "sensor" and found.translation_key != "room_state"
    }


def hidden_by_integration(hass, entry) -> set[str]:
    return {
        entity_id
        for entity_id, found in statistics(hass, entry).items()
        if found.hidden_by is INTEGRATION
    }


def marked(hass, entry) -> set[str]:
    return {
        entity_id
        for entity_id, found in statistics(hass, entry).items()
        if found.options.get(DOMAIN) == MARKER
    }


def registered(hass, entity_id: str) -> er.RegistryEntry:
    found = er.async_get(hass).async_get(entity_id)
    assert found is not None, entity_id
    return found


async def set_options(hass, entry, **values: str) -> None:
    """Change the options of the entry and let the reload run."""
    hass.config_entries.async_update_entry(entry, options=options(**values))
    await settle(hass)
    assert entry.state is ConfigEntryState.LOADED


def subentry_of(entry, title: str):
    return next(s for s in entry.subentries.values() if s.title == title)


# --- off ---------------------------------------------------------------------


async def test_statistics_are_off_by_default(hass) -> None:
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert statistics(hass, entry) == {}
    for entity_id in LIVING:
        assert hass.states.get(entity_id) is None, entity_id
    # The heating state is no statistic: it is there and it is shown.
    state = registered(hass, STATE_ENTITY)
    assert state.hidden_by is None
    assert hass.states.get(STATE_ENTITY).state == "heating"


async def test_off_removes_the_statistics_that_were_there(hass) -> None:
    entry = make_entry(hass, [room_data()], options(statistics="visible"))
    await start(hass, entry)
    assert set(statistics(hass, entry)) == LIVING

    await set_options(hass, entry, statistics="off")

    assert statistics(hass, entry) == {}
    for entity_id in LIVING:
        assert hass.states.get(entity_id) is None, entity_id
    assert hass.states.get(STATE_ENTITY).state == "heating"


async def test_back_from_off_the_statistics_are_created_again(hass) -> None:
    entry = make_entry(hass, [room_data()], options(statistics="hidden"))
    await start(hass, entry)
    await set_options(hass, entry, statistics="off")
    assert statistics(hass, entry) == {}

    await set_options(hass, entry, statistics="hidden")

    assert set(statistics(hass, entry)) == LIVING
    assert hidden_by_integration(hass, entry) == LIVING
    assert marked(hass, entry) == LIVING


# --- hidden ------------------------------------------------------------------


async def test_hidden_statistics_exist_collect_and_are_hidden(hass) -> None:
    entry = make_entry(hass, [room_data()], options(statistics="hidden"))

    await start(hass, entry)

    assert set(statistics(hass, entry)) == LIVING
    assert hidden_by_integration(hass, entry) == LIVING
    # Hidden by Easy TRV, with the marker that tells it later.
    assert marked(hass, entry) == LIVING
    # They still collect: the sensors have states, the counters count.
    for entity_id in LIVING:
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.state != STATE_UNAVAILABLE or entity_id.endswith("heating_time")
    assert int(hass.states.get(A_WRITES).state) > 0
    # The heating state stays in the lists.
    assert registered(hass, STATE_ENTITY).hidden_by is None
    assert registered(hass, "climate.living_heating").hidden_by is None


async def test_hidden_statistics_are_registered_hidden_from_the_start(hass) -> None:
    entry = make_entry(hass, [room_data()], options(statistics="hidden"))
    seen: list[er.RegistryEntry] = []

    @callback
    def watch(event) -> None:
        if event.data["action"] == "create":
            seen.append(er.async_get(hass).async_get(event.data["entity_id"]))

    hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, watch)
    await start(hass, entry)

    created = {found.entity_id: found for found in seen if found is not None}
    assert set(created) >= LIVING
    # Not shown for a moment and hidden afterwards: hidden when created.
    assert {found.hidden_by for found in seen if found.entity_id in LIVING} == {
        INTEGRATION
    }


async def test_visible_statistics_are_shown_and_not_marked(hass) -> None:
    entry = make_entry(hass, [room_data()], options(statistics="visible"))

    await start(hass, entry)

    assert set(statistics(hass, entry)) == LIVING
    assert hidden_by_integration(hass, entry) == set()
    assert marked(hass, entry) == set()
    assert all(found.hidden_by is None for found in statistics(hass, entry).values())


# --- switching ---------------------------------------------------------------


async def test_hidden_to_visible_shows_the_statistics(hass) -> None:
    entry = make_entry(hass, [room_data()], options(statistics="hidden"))
    await start(hass, entry)
    assert hidden_by_integration(hass, entry) == LIVING

    await set_options(hass, entry, statistics="visible")

    assert set(statistics(hass, entry)) == LIVING
    assert hidden_by_integration(hass, entry) == set()
    assert marked(hass, entry) == set()
    assert all(found.hidden_by is None for found in statistics(hass, entry).values())


async def test_visible_to_hidden_hides_what_was_shown(hass) -> None:
    entry = make_entry(hass, [room_data()], options(statistics="visible"))
    await start(hass, entry)
    ids = {entity_id: found.id for entity_id, found in statistics(hass, entry).items()}

    await set_options(hass, entry, statistics="hidden")

    # The same registry entries (no new entities), now hidden and marked.
    assert {e: found.id for e, found in statistics(hass, entry).items()} == ids
    assert hidden_by_integration(hass, entry) == LIVING
    assert marked(hass, entry) == LIVING
    assert int(hass.states.get(A_WRITES).state) > 0


async def test_a_statistic_the_person_shows_again_stays_shown(hass) -> None:
    entry = make_entry(hass, [room_data()], options(statistics="hidden"))
    await start(hass, entry)
    er.async_get(hass).async_update_entity("sensor.living_deviation", hidden_by=None)

    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    shown = registered(hass, "sensor.living_deviation")
    assert shown.hidden_by is None
    assert shown.options.get(DOMAIN) == MARKER
    assert hidden_by_integration(hass, entry) == LIVING - {"sensor.living_deviation"}


async def test_a_statistic_the_user_hid_is_never_touched(hass) -> None:
    entry = make_entry(hass, [room_data()], options(statistics="visible"))
    await start(hass, entry)
    er.async_get(hass).async_update_entity(A_WRITES, hidden_by=USER)

    await set_options(hass, entry, statistics="hidden")

    mine = registered(hass, A_WRITES)
    assert mine.hidden_by is USER
    assert DOMAIN not in mine.options
    assert hidden_by_integration(hass, entry) == LIVING - {A_WRITES}

    await set_options(hass, entry, statistics="visible")

    mine = registered(hass, A_WRITES)
    assert mine.hidden_by is USER
    assert DOMAIN not in mine.options
    assert hidden_by_integration(hass, entry) == set()
    assert marked(hass, entry) == set()


async def test_a_statistic_the_user_hid_by_hand_stays_hidden_when_shown(hass) -> None:
    entry = make_entry(hass, [room_data()], options(statistics="hidden"))
    await start(hass, entry)
    # Hidden by Easy TRV first, by the person afterwards.
    er.async_get(hass).async_update_entity(A_WRITES, hidden_by=USER)

    await set_options(hass, entry, statistics="visible")

    assert registered(hass, A_WRITES).hidden_by is USER
    assert DOMAIN not in registered(hass, A_WRITES).options
    assert hidden_by_integration(hass, entry) == set()


# --- the room's own choice ---------------------------------------------------


async def test_a_room_can_switch_the_statistics_off_for_itself(hass, two_rooms) -> None:
    two_rooms[1]["statistics"] = "off"
    entry = make_entry(hass, two_rooms, options(statistics="hidden"))

    await start(hass, entry)

    assert set(statistics(hass, entry)) == LIVING
    assert hidden_by_integration(hass, entry) == LIVING
    for entity_id in BEDROOM:
        assert hass.states.get(entity_id) is None, entity_id
    assert hass.states.get(BEDROOM_STATE_ENTITY) is not None


async def test_a_room_can_ask_for_statistics_the_global_setting_does_not(
    hass, two_rooms
) -> None:
    two_rooms[1]["statistics"] = "visible"
    entry = make_entry(hass, two_rooms, FAST_OPTIONS)

    await start(hass, entry)

    assert set(statistics(hass, entry)) == BEDROOM
    assert hidden_by_integration(hass, entry) == set()
    for entity_id in LIVING:
        assert hass.states.get(entity_id) is None, entity_id


async def test_rooms_hide_or_show_each_on_their_own(hass, two_rooms) -> None:
    two_rooms[1]["statistics"] = "visible"
    entry = make_entry(hass, two_rooms, options(statistics="hidden"))

    await start(hass, entry)

    assert set(statistics(hass, entry)) == LIVING | BEDROOM
    assert hidden_by_integration(hass, entry) == LIVING
    assert marked(hass, entry) == LIVING


async def test_changing_a_room_changes_only_that_room(hass, two_rooms) -> None:
    entry = make_entry(hass, two_rooms, options(statistics="hidden"))
    await start(hass, entry)
    assert hidden_by_integration(hass, entry) == LIVING | BEDROOM

    hass.config_entries.async_update_subentry(
        entry, subentry_of(entry, "Bedroom"), data={**two_rooms[1], "statistics": "off"}
    )
    await settle(hass)

    assert set(statistics(hass, entry)) == LIVING
    assert hidden_by_integration(hass, entry) == LIVING

    hass.config_entries.async_update_subentry(
        entry,
        subentry_of(entry, "Bedroom"),
        data={**two_rooms[1], "statistics": "visible"},
    )
    await settle(hass)

    assert set(statistics(hass, entry)) == LIVING | BEDROOM
    assert hidden_by_integration(hass, entry) == LIVING


# --- rooms that cannot be set up ---------------------------------------------


async def test_a_room_that_cannot_be_set_up_keeps_its_hidden_statistics(
    hass, two_rooms
) -> None:
    entry = make_entry(hass, two_rooms, options(statistics="hidden"))
    await start(hass, entry)
    assert hidden_by_integration(hass, entry) == LIVING | BEDROOM

    # Corrupt data: the room is invalid now, its statistics are not wanted by
    # anyone and must stay as they are anyway.
    hass.config_entries.async_update_subentry(
        entry, subentry_of(entry, "Bedroom"), data={**two_rooms[1], "hysteresis": "x"}
    )
    await settle(hass)

    assert entry.runtime_data.rooms.keys() == {subentry_of(entry, "Living").subentry_id}
    assert set(statistics(hass, entry)) == LIVING | BEDROOM
    assert hidden_by_integration(hass, entry) == LIVING | BEDROOM
    assert marked(hass, entry) == LIVING | BEDROOM


async def test_a_failed_rooms_statistics_stay_hidden_when_the_others_are_shown(
    hass, two_rooms
) -> None:
    entry = make_entry(hass, two_rooms, options(statistics="hidden"))
    await start(hass, entry)
    hass.config_entries.async_update_subentry(
        entry, subentry_of(entry, "Bedroom"), data={**two_rooms[1], "hysteresis": "x"}
    )
    await settle(hass)

    await set_options(hass, entry, statistics="visible")

    # Living is shown now; Bedroom cannot run, so it keeps what it had.
    assert hidden_by_integration(hass, entry) == BEDROOM
    assert marked(hass, entry) == BEDROOM


async def test_a_failed_room_that_becomes_valid_gets_the_current_setting(
    hass, two_rooms
) -> None:
    broken = {**two_rooms[1], "hysteresis": "x"}
    entry = make_entry(hass, [two_rooms[0], broken], options(statistics="hidden"))
    await start(hass, entry)
    assert set(statistics(hass, entry)) == LIVING

    hass.config_entries.async_update_subentry(
        entry, subentry_of(entry, "Bedroom"), data=two_rooms[1]
    )
    await settle(hass)

    assert set(statistics(hass, entry)) == LIVING | BEDROOM
    assert hidden_by_integration(hass, entry) == LIVING | BEDROOM
