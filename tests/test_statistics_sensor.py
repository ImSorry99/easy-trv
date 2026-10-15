"""The diagnostic statistics sensors of a room and its thermostats."""

import json
import logging
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import CoreState, State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import async_get as async_restore_data
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache,
    mock_restore_cache_with_extra_data,
)

from custom_components.easy_trv.const import DOMAIN
from custom_components.easy_trv.controller import ThermostatStatistics
from tests.helpers_entry import (
    CELSIUS,
    FAST_OPTIONS,
    FIRMWARE,
    IEEE_A,
    IEEE_B,
    advance,
    make_entry,
    name_thermostat,
    patched_gateway,
    room_data,
    settle,
    start,
)

COMPONENT = Path(__file__).parent.parent / "custom_components" / "easy_trv"
VALUE = 0x600D

DEVIATION = "sensor.living_deviation"
HEATING_TIME = "sensor.living_heating_time"
OPENINGS = "sensor.living_window_openings"
OPEN_TIME = "sensor.living_window_open_time"
A_WRITES = "sensor.a_writes"
A_FAILURES = "sensor.a_write_failures"
A_DROPOUTS = "sensor.a_dropouts"
B_WRITES = "sensor.b_writes"
WINDOW = "binary_sensor.window"

STATISTICS_KEYS = {
    "deviation",
    "heating_time",
    "window_openings",
    "window_open_time",
    "writes",
    "write_failures",
    "dropouts",
}
STATE_ENTITY = "sensor.living_heating_state"
HEATING = {"hvac_action": "heating"}
IDLE = {"hvac_action": "idle"}


@pytest.fixture(autouse=True)
def fake_gateway(hass):
    with patched_gateway(hass) as gateway:
        yield gateway


@pytest.fixture
def entry(hass):
    return make_entry(hass, [room_data()], FAST_OPTIONS)


@pytest.fixture
def entry_with_contact(hass):
    hass.states.async_set(WINDOW, "off")
    return make_entry(hass, [room_data(window_contacts=[WINDOW])], FAST_OPTIONS)


@pytest.fixture
def big_room(hass, fake_gateway):
    """Three thermostats and one window contact."""
    fake_gateway.add_device("climate.c", "00:33", firmware=FIRMWARE)
    hass.states.async_set("climate.c", "heat")
    hass.states.async_set(WINDOW, "off")
    return make_entry(
        hass,
        [
            room_data(
                trvs=["climate.a", "climate.b", "climate.c"],
                window_contacts=[WINDOW],
            )
        ],
        FAST_OPTIONS,
    )


def sensors(hass, entry) -> dict[str, er.RegistryEntry]:
    """Return the sensors of the entry in the registry, by entity id."""
    return {
        found.entity_id: found
        for found in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if found.domain == "sensor"
    }


def value(hass, entity_id: str) -> str:
    """Return the state of an entity."""
    state = hass.states.get(entity_id)
    assert state is not None, entity_id
    return state.state


def number(hass, entity_id: str) -> float:
    return float(value(hass, entity_id))


def room_of(entry):
    return next(iter(entry.runtime_data.rooms.values()))


def sent_to(gateway, ieee) -> int:
    """Return how many write requests reached a device (the failing ones too)."""
    return len([write for write in gateway.writes if write[0] == ieee])


def seed(hass, totals: dict[str, Any]) -> None:
    """Put the last native values of sensors where a restart would find them."""
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(entity_id, str(total)),
                {"native_value": total, "native_unit_of_measurement": None},
            )
            for entity_id, total in totals.items()
        ],
    )


def dumped(hass, entity_id: str) -> Any:
    """Return the native value that would be saved for a restart of the entity."""
    for stored in async_restore_data(hass).async_get_stored_states():
        if stored.state.entity_id == entity_id:
            assert stored.extra_data is not None
            return stored.extra_data.as_dict()["native_value"]
    pytest.fail(f"nothing saved for {entity_id}")


async def start_room_not_running(hass, entry) -> None:
    """Set the entry up while Home Assistant is still starting: no room runs yet."""
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)


async def run_rooms(hass) -> None:
    """Tell the rooms Home Assistant has started."""
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)


# --- Which entities exist ---------------------------------------------------


async def test_a_room_without_contacts_gets_no_window_sensors(hass, entry) -> None:
    await start(hass, entry)

    assert set(sensors(hass, entry)) == {
        STATE_ENTITY,
        DEVIATION,
        HEATING_TIME,
        A_WRITES,
        A_FAILURES,
        A_DROPOUTS,
        B_WRITES,
        "sensor.b_write_failures",
        "sensor.b_dropouts",
    }
    assert hass.states.get(OPENINGS) is None
    assert hass.states.get(OPEN_TIME) is None


async def test_a_room_of_three_thermostats_and_a_contact(hass, big_room) -> None:
    await start(hass, big_room)

    assert set(sensors(hass, big_room)) == {
        STATE_ENTITY,
        DEVIATION,
        HEATING_TIME,
        OPENINGS,
        OPEN_TIME,
        *(
            f"sensor.{thermostat}_{key}"
            for thermostat in ("a", "b", "c")
            for key in ("writes", "write_failures", "dropouts")
        ),
    }


async def test_unique_ids_device_and_category(hass, big_room) -> None:
    await start(hass, big_room)

    subentry_id = next(iter(big_room.subentries))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry_id), big_room.entry_id
    )
    assert device is not None
    expected = {
        DEVIATION: ("deviation", f"{subentry_id}_deviation"),
        HEATING_TIME: ("heating_time", f"{subentry_id}_heating_time"),
        OPENINGS: ("window_openings", f"{subentry_id}_window_openings"),
        OPEN_TIME: ("window_open_time", f"{subentry_id}_window_open_time"),
    }
    for thermostat in ("a", "b", "c"):
        for key in ("writes", "write_failures", "dropouts"):
            expected[f"sensor.{thermostat}_{key}"] = (
                key,
                f"{subentry_id}_{thermostat}_{key}",
            )

    found = sensors(hass, big_room)
    for entity_id, (key, unique_id) in expected.items():
        registered = found[entity_id]
        assert registered.unique_id == unique_id
        assert registered.translation_key == key
        assert registered.platform == DOMAIN
        assert registered.config_subentry_id == subentry_id
        assert registered.device_id == device.id
        assert registered.entity_category is EntityCategory.DIAGNOSTIC


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [
        (DEVIATION, (None, "K", "measurement", 1)),
        (HEATING_TIME, ("duration", "h", "total_increasing", 2)),
        (OPENINGS, (None, None, "total_increasing", None)),
        (OPEN_TIME, ("duration", "h", "total_increasing", 2)),
        (A_WRITES, (None, None, "total_increasing", None)),
        (A_FAILURES, (None, None, "total_increasing", None)),
        (A_DROPOUTS, (None, None, "total_increasing", None)),
    ],
)
async def test_classes_units_and_precision(hass, big_room, entity_id, expected) -> None:
    await start(hass, big_room)

    attributes = hass.states.get(entity_id).attributes
    options = er.async_get(hass).async_get(entity_id).options.get("sensor", {})
    assert (
        attributes.get("device_class"),
        attributes.get("unit_of_measurement"),
        attributes.get("state_class"),
        options.get("suggested_display_precision"),
    ) == expected


# --- Names and translations -------------------------------------------------


async def test_names_in_english(hass, big_room) -> None:
    name_thermostat(hass, "climate.a", "Sofa valve")

    await start(hass, big_room)

    def name(entity_id: str) -> str:
        return hass.states.get(entity_id).attributes["friendly_name"]

    assert name(DEVIATION) == "Living heating Control deviation"
    assert name(HEATING_TIME) == "Living heating Heating time"
    assert name(OPENINGS) == "Living heating Window openings"
    assert name(OPEN_TIME) == "Living heating Window open time"
    assert name(A_WRITES) == "Living heating Sofa valve writes"
    assert name(A_FAILURES) == "Living heating Sofa valve failed writes"
    assert name(A_DROPOUTS) == "Living heating Sofa valve dropouts"
    # A thermostat without a device is called by its entity id.
    assert name("sensor.c_writes") == "Living heating climate.c writes"


async def test_names_in_german(hass, big_room) -> None:
    hass.config.language = "de"
    name_thermostat(hass, "climate.a", "Sofa valve")

    await start(hass, big_room)

    def name(entity_id: str) -> str:
        return hass.states.get(entity_id).attributes["friendly_name"]

    assert name(DEVIATION) == "Heizung Living Regelabweichung"
    assert name(HEATING_TIME) == "Heizung Living Heizzeit"
    assert name(OPENINGS) == "Heizung Living Fensteröffnungen"
    assert name(OPEN_TIME) == "Heizung Living Fenster-offen-Zeit"
    assert name(A_WRITES) == "Heizung Living Sofa valve Schreibvorgänge"
    assert name(A_FAILURES) == "Heizung Living Sofa valve Schreibfehler"
    assert name(A_DROPOUTS) == "Heizung Living Sofa valve Ausfälle"


def test_translation_files_name_and_icon_every_sensor() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")
    )
    german = json.loads(
        (COMPONENT / "translations" / "de.json").read_text(encoding="utf-8")
    )
    icons = json.loads((COMPONENT / "icons.json").read_text(encoding="utf-8"))

    assert strings == english
    for translated in (strings, german):
        assert set(translated["entity"]["sensor"]) == {"room_state", *STATISTICS_KEYS}
    assert set(icons["entity"]["sensor"]) == {"room_state", *STATISTICS_KEYS}

    assert {
        key: strings["entity"]["sensor"][key]["name"] for key in STATISTICS_KEYS
    } == {
        "deviation": "Control deviation",
        "heating_time": "Heating time",
        "window_openings": "Window openings",
        "window_open_time": "Window open time",
        "writes": "{thermostat} writes",
        "write_failures": "{thermostat} failed writes",
        "dropouts": "{thermostat} dropouts",
    }
    assert {
        key: german["entity"]["sensor"][key]["name"] for key in STATISTICS_KEYS
    } == {
        "deviation": "Regelabweichung",
        "heating_time": "Heizzeit",
        "window_openings": "Fensteröffnungen",
        "window_open_time": "Fenster-offen-Zeit",
        "writes": "{thermostat} Schreibvorgänge",
        "write_failures": "{thermostat} Schreibfehler",
        "dropouts": "{thermostat} Ausfälle",
    }
    assert {
        key: icons["entity"]["sensor"][key]["default"] for key in STATISTICS_KEYS
    } == {
        "deviation": "mdi:thermometer-lines",
        "heating_time": "mdi:fire-circle",
        "window_openings": "mdi:window-open-variant",
        "window_open_time": "mdi:timer-outline",
        "writes": "mdi:upload-network-outline",
        "write_failures": "mdi:alert-circle-outline",
        "dropouts": "mdi:lan-disconnect",
    }


# --- Values follow the room -------------------------------------------------


async def test_writes_follow_what_was_sent_to_each_thermostat(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)

    assert sent_to(fake_gateway, IEEE_A) > 0
    assert value(hass, A_WRITES) == str(sent_to(fake_gateway, IEEE_A))
    assert value(hass, B_WRITES) == str(sent_to(fake_gateway, IEEE_B))
    assert value(hass, A_FAILURES) == "0"
    assert value(hass, A_DROPOUTS) == "0"
    before = sent_to(fake_gateway, IEEE_A)

    # A new target is written to both thermostats.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_temperature",
        {ATTR_ENTITY_ID: "climate.living_heating", "temperature": 23.0},
        blocking=True,
    )
    await settle(hass)

    assert sent_to(fake_gateway, IEEE_A) > before
    assert value(hass, A_WRITES) == str(sent_to(fake_gateway, IEEE_A))
    assert value(hass, B_WRITES) == str(sent_to(fake_gateway, IEEE_B))


async def test_failed_writes_are_counted_next_to_the_writes(
    hass, freezer, entry, fake_gateway
) -> None:
    await start(hass, entry)
    fake_gateway.read_override[(IEEE_A, VALUE)] = 2000
    before = int(value(hass, A_WRITES))

    hass.states.async_set("sensor.t1", "22.0", CELSIUS)
    await advance(hass, freezer, 70)

    failures = int(value(hass, A_FAILURES))
    assert failures > 0
    assert int(value(hass, A_WRITES)) >= before + failures
    assert value(hass, A_WRITES) == str(sent_to(fake_gateway, IEEE_A))
    # The other thermostat reads back what it was given.
    assert value(hass, "sensor.b_write_failures") == "0"


async def test_dropouts_count_each_time_a_thermostat_goes_away(hass, entry) -> None:
    await start(hass, entry)
    assert value(hass, A_DROPOUTS) == "0"

    hass.states.async_set("climate.a", "unavailable")
    await settle(hass)
    assert value(hass, A_DROPOUTS) == "1"
    assert value(hass, "sensor.b_dropouts") == "0"

    # Unavailable twice in a row is one dropout, back and gone again another.
    hass.states.async_set("climate.a", "unavailable", {"x": 1})
    hass.states.async_set("climate.a", "heat")
    await settle(hass)
    hass.states.async_set("climate.a", "unavailable")
    await settle(hass)
    assert value(hass, A_DROPOUTS) == "2"


async def test_deviation_is_the_sensor_minus_the_setpoint_while_heating(
    hass, entry
) -> None:
    hass.states.async_set("climate.a", "heat", {"temperature": 21.0})
    hass.states.async_set("climate.b", "heat", {"temperature": 21.0})

    await start(hass, entry)
    assert value(hass, DEVIATION) == "0.3"

    hass.states.async_set("sensor.t1", "20.5", CELSIUS)
    await settle(hass)
    assert value(hass, DEVIATION) == "-0.5"

    # Switched off, the room has no deviation: nothing to average.
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_hvac_mode",
        {ATTR_ENTITY_ID: "climate.living_heating", "hvac_mode": "off"},
        blocking=True,
    )
    await settle(hass)
    assert value(hass, DEVIATION) == STATE_UNKNOWN


async def test_deviation_is_unknown_before_the_room_runs(hass, entry) -> None:
    await start_room_not_running(hass, entry)

    assert value(hass, DEVIATION) == STATE_UNKNOWN


async def test_window_openings_and_open_time(hass, freezer, entry_with_contact) -> None:
    await start(hass, entry_with_contact)
    assert value(hass, OPENINGS) == "0"
    assert number(hass, OPEN_TIME) == 0.0

    hass.states.async_set(WINDOW, "on")
    await settle(hass)
    assert value(hass, OPENINGS) == "1"
    await advance(hass, freezer, 1800, step=60)
    assert number(hass, OPEN_TIME) == pytest.approx(0.5)

    # Reporting on again is not a new opening.
    hass.states.async_set(WINDOW, "on", {"battery": 80})
    hass.states.async_set(WINDOW, "off")
    await settle(hass)
    assert value(hass, OPENINGS) == "1"
    await advance(hass, freezer, 600, step=60)
    assert number(hass, OPEN_TIME) == pytest.approx(0.5)

    hass.states.async_set(WINDOW, "on")
    await settle(hass)
    assert value(hass, OPENINGS) == "2"


async def test_heating_time_runs_while_a_thermostat_heats(hass, freezer, entry) -> None:
    hass.states.async_set("climate.a", "heat", IDLE)
    await start(hass, entry)
    assert number(hass, HEATING_TIME) == 0.0

    hass.states.async_set("climate.a", "heat", HEATING)
    await settle(hass)
    await advance(hass, freezer, 1800, step=60)
    assert number(hass, HEATING_TIME) == pytest.approx(0.5)

    hass.states.async_set("climate.a", "heat", IDLE)
    await settle(hass)
    await advance(hass, freezer, 600, step=60)
    assert number(hass, HEATING_TIME) == pytest.approx(0.5)


# --- A thermostat that never reports hvac_action ----------------------------


async def test_heating_time_is_unavailable_without_an_hvac_action(
    hass, freezer, entry, caplog
) -> None:
    with caplog.at_level(logging.WARNING):
        await start(hass, entry)
        await advance(hass, freezer, 300, step=60)

        # Not stuck at 0, not an error: there is nothing to measure.
        assert value(hass, HEATING_TIME) == STATE_UNAVAILABLE
        # The other sensors of the room are fine.
        assert value(hass, A_WRITES) != STATE_UNAVAILABLE
        assert value(hass, DEVIATION) != STATE_UNAVAILABLE

        hass.states.async_set("climate.b", "heat", IDLE)
        await settle(hass)
        assert number(hass, HEATING_TIME) == 0.0

    ours = [r for r in caplog.records if r.name.startswith("custom_components")]
    assert not [r for r in ours if r.levelno >= logging.WARNING]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# --- A total never goes down ------------------------------------------------


async def test_a_duration_never_shows_less_than_before(hass, freezer, entry) -> None:
    hass.states.async_set("climate.a", "heat", HEATING)
    await start(hass, entry)
    controller = room_of(entry)
    await advance(hass, freezer, 3600, step=60)
    assert number(hass, HEATING_TIME) == pytest.approx(1.0)

    # The clock steps back to before the heating began: the room counts the
    # running period as zero, the sensor keeps what it showed.
    freezer.tick(timedelta(minutes=-90))
    hass.states.async_set("sensor.t1", "21.4", CELSIUS)
    await settle(hass)
    assert controller.statistics().heating_hours == pytest.approx(0.0)
    assert number(hass, HEATING_TIME) == pytest.approx(1.0)

    # Once the clock has passed what was shown, it grows again.
    freezer.tick(timedelta(minutes=30 + 90))
    hass.states.async_set("sensor.t1", "21.5", CELSIUS)
    await settle(hass)
    assert number(hass, HEATING_TIME) == pytest.approx(1.5)


async def test_counters_and_durations_keep_the_highest_value_the_room_reports(
    hass, freezer, big_room
) -> None:
    hass.states.async_set("climate.a", "heat", HEATING)
    await start(hass, big_room)
    controller = room_of(big_room)
    hass.states.async_set(WINDOW, "on")
    hass.states.async_set(WINDOW, "off")
    hass.states.async_set(WINDOW, "on")
    await settle(hass)
    await advance(hass, freezer, 600, step=60)
    shown = {
        entity_id: value(hass, entity_id)
        for entity_id in (OPENINGS, OPEN_TIME, HEATING_TIME, A_WRITES, A_DROPOUTS)
    }
    assert shown[OPENINGS] == "2"
    assert float(shown[OPEN_TIME]) > 0
    assert int(shown[A_WRITES]) > 0

    # The room hands out less than it did, however that came about.
    real = controller.statistics()
    lower = replace(
        real,
        deviation=-1.0,
        heating_hours=real.heating_hours - 0.01,
        window_openings=1,
        window_open_hours=real.window_open_hours - 0.01,
        per_thermostat=MappingProxyType(
            {
                trv: ThermostatStatistics(0, 0, 0)
                for trv in ("climate.a", "climate.b", "climate.c")
            }
        ),
    )
    with patch.object(controller, "statistics", return_value=lower):
        hass.states.async_set("sensor.t1", "21.6", CELSIUS)
        await settle(hass)

        for entity_id, before in shown.items():
            assert value(hass, entity_id) == before
        # A measurement is not a total: it shows what the room says.
        assert value(hass, DEVIATION) == "-1.0"


# --- Restoring across a restart ---------------------------------------------


async def test_counters_come_back_before_the_room_runs(
    hass, entry, fake_gateway
) -> None:
    seed(hass, {A_WRITES: 7, A_FAILURES: 2, A_DROPOUTS: 3})

    await start_room_not_running(hass, entry)

    # Published before the first write, and given to the room to count on.
    assert value(hass, A_WRITES) == "7"
    assert value(hass, A_FAILURES) == "2"
    assert value(hass, A_DROPOUTS) == "3"
    assert value(hass, B_WRITES) == "0"
    stats = room_of(entry).statistics().per_thermostat["climate.a"]
    assert stats == ThermostatStatistics(7, 2, 3)

    await run_rooms(hass)

    # The writes of the start come on top.
    assert sent_to(fake_gateway, IEEE_A) > 0
    assert value(hass, A_WRITES) == str(7 + sent_to(fake_gateway, IEEE_A))
    assert value(hass, A_FAILURES) == "2"


async def test_durations_and_openings_continue_from_the_restored_values(
    hass, freezer, entry_with_contact
) -> None:
    seed(hass, {HEATING_TIME: 12.5, OPEN_TIME: 2.25, OPENINGS: 4})
    hass.states.async_set("climate.a", "heat", HEATING)

    await start(hass, entry_with_contact)

    assert number(hass, HEATING_TIME) == pytest.approx(12.5)
    assert number(hass, OPEN_TIME) == pytest.approx(2.25)
    assert value(hass, OPENINGS) == "4"

    await advance(hass, freezer, 1800, step=60)
    assert number(hass, HEATING_TIME) == pytest.approx(13.0)

    hass.states.async_set(WINDOW, "on")
    await settle(hass)
    await advance(hass, freezer, 900, step=60)
    assert value(hass, OPENINGS) == "5"
    assert number(hass, OPEN_TIME) == pytest.approx(2.5)


async def test_a_restored_heating_time_waits_for_the_first_hvac_action(
    hass, entry
) -> None:
    seed(hass, {HEATING_TIME: 12.5})

    await start(hass, entry)
    assert value(hass, HEATING_TIME) == STATE_UNAVAILABLE

    hass.states.async_set("climate.a", "heat", IDLE)
    await settle(hass)
    assert number(hass, HEATING_TIME) == pytest.approx(12.5)


async def test_a_restored_duration_does_not_come_back_a_float_step_lower(
    hass, entry
) -> None:
    # Through seconds and back this value loses a float step.
    stored = 29.402
    assert stored * 3600 / 3600 < stored
    seed(hass, {HEATING_TIME: stored})
    hass.states.async_set("climate.a", "heat", IDLE)

    await start(hass, entry)

    assert float(value(hass, HEATING_TIME)) == stored
    # The room itself reports a float step less; what is kept for the next
    # restart is the value that was shown.
    assert room_of(entry).statistics().heating_hours < stored
    assert dumped(hass, HEATING_TIME) == stored


async def test_nothing_usable_to_restore_starts_at_zero(hass, entry) -> None:
    seed(
        hass,
        {
            A_WRITES: None,
            A_FAILURES: "banana",
            A_DROPOUTS: float("inf"),
            # Totals of something this room does not have.
            OPENINGS: 9,
        },
    )

    await start(hass, entry)

    assert value(hass, A_WRITES) != "None"
    assert value(hass, A_FAILURES) == "0"
    assert value(hass, A_DROPOUTS) == "0"
    assert hass.states.get(OPENINGS) is None


async def test_a_sensor_without_a_stored_value_starts_at_zero(hass, entry) -> None:
    # Only the state is known, as after an update from a version without extras.
    mock_restore_cache(hass, [State(A_FAILURES, "4")])

    await start(hass, entry)

    assert value(hass, A_FAILURES) == "0"


async def test_the_deviation_is_not_restored(hass, entry) -> None:
    seed(hass, {DEVIATION: 1.5})

    await start_room_not_running(hass, entry)

    assert value(hass, DEVIATION) == STATE_UNKNOWN


# --- A room reload ----------------------------------------------------------


async def test_a_reload_continues_the_totals_without_new_entities(
    hass, freezer, entry, fake_gateway
) -> None:
    await start(hass, entry)
    fake_gateway.read_override[(IEEE_A, VALUE)] = 2000
    hass.states.async_set("sensor.t1", "22.0", CELSIUS)
    await advance(hass, freezer, 70)
    hass.states.async_set("climate.b", "unavailable")
    await settle(hass)
    hass.states.async_set("climate.b", "heat")
    await settle(hass)
    fake_gateway.read_override.clear()

    entities = set(sensors(hass, entry))
    states = len(hass.states.async_entity_ids("sensor"))
    before = {
        entity_id: int(value(hass, entity_id))
        for entity_id in (A_WRITES, A_FAILURES, "sensor.b_dropouts")
    }
    assert before[A_FAILURES] > 0
    assert before["sensor.b_dropouts"] == 1
    # More than the five writes of a start, so a total that was not carried over
    # could not reach it again.
    assert before[A_WRITES] > 5
    old = room_of(entry)

    await hass.config_entries.async_reload(entry.entry_id)
    await run_rooms(hass)

    assert set(sensors(hass, entry)) == entities
    assert len(hass.states.async_entity_ids("sensor")) == states
    new = room_of(entry)
    assert new is not old
    assert not old._update_listeners  # noqa: SLF001
    for entity_id, total in before.items():
        assert int(value(hass, entity_id)) >= total
    assert int(value(hass, A_FAILURES)) == before[A_FAILURES]
    assert value(hass, "sensor.b_dropouts") == "1"
    shown = new.statistics().per_thermostat
    assert shown["climate.a"].writes == int(value(hass, A_WRITES))
    assert shown["climate.a"].write_failures == before[A_FAILURES]


async def test_a_reload_in_the_middle_of_a_running_duration_loses_nothing(
    hass, freezer, entry_with_contact
) -> None:
    hass.states.async_set("climate.a", "heat", HEATING)
    await start(hass, entry_with_contact)
    hass.states.async_set(WINDOW, "on")
    await settle(hass)
    await advance(hass, freezer, 1800, step=60)
    assert number(hass, HEATING_TIME) == pytest.approx(0.5)
    assert number(hass, OPEN_TIME) == pytest.approx(0.5)

    # Half a minute after the last tick the room is reloaded.
    freezer.tick(timedelta(seconds=30))
    await hass.config_entries.async_reload(entry_with_contact.entry_id)
    await run_rooms(hass)

    after_30_s = 1830 / 3600
    assert number(hass, HEATING_TIME) == pytest.approx(after_30_s)
    # The window is still open after the reload: it is not an opening again.
    assert value(hass, OPENINGS) == "1"
    assert number(hass, OPEN_TIME) == pytest.approx(after_30_s)

    # And nothing is counted twice from here on.
    await advance(hass, freezer, 600, step=60)
    assert number(hass, HEATING_TIME) == pytest.approx(after_30_s + 600 / 3600)
    assert number(hass, OPEN_TIME) == pytest.approx(after_30_s + 600 / 3600)


async def test_unloading_releases_the_listeners_of_the_statistics(
    hass, big_room
) -> None:
    await start(hass, big_room)
    controller = room_of(big_room)
    # The room thermostat, the state sensor and the thirteen statistics sensors.
    assert len(controller._update_listeners) == 2 + 13  # noqa: SLF001

    assert await hass.config_entries.async_unload(big_room.entry_id)

    assert len(controller._update_listeners) == 0  # noqa: SLF001
