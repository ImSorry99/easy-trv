import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import (
    ATTR_ENTITY_ID,
    EVENT_HOMEASSISTANT_STARTED,
    STATE_UNKNOWN,
    EntityCategory,
)
from homeassistant.core import CoreState
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easy_trv.const import CONF_AREA, DOMAIN
from tests.helpers_entry import (
    FAST_OPTIONS,
    IEEE_A,
    IEEE_B,
    advance,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)

COMPONENT = Path(__file__).parent.parent / "custom_components" / "easy_trv"
VALUE = 0x600D
SETPOINT = 0x0012
MODE = 0x001C
STATE_SENSOR = "sensor.living_heating_state"


@pytest.fixture(autouse=True)
def fake_gateway(hass):
    with patched_gateway(hass) as gateway:
        yield gateway


@pytest.fixture
def entry(hass):
    return make_entry(hass, [room_data()], FAST_OPTIONS)


async def test_room_device_is_named_heating_and_sits_in_its_area(hass) -> None:
    area = ar.async_get(hass).async_get_or_create("Wohnzimmer")
    entry = make_entry(
        hass, [room_data(name="Wohnzimmer", **{CONF_AREA: area.id})], FAST_OPTIONS
    )
    await start(hass, entry)

    subentry_id = next(iter(entry.subentries))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry_id), entry.entry_id
    )
    assert device is not None
    assert device.name == "Wohnzimmer heating"
    assert device.area_id == area.id
    assert entry.runtime_data.areas == {subentry_id: "Wohnzimmer"}
    # The sensor suggests its entity id, so HA does not prefix the area to the
    # device name (which would double it: sensor.wohnzimmer_wohnzimmer_...).
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{subentry_id}_state"
    )
    assert entity_id == "sensor.wohnzimmer_heating_state"
    state = hass.states.get("sensor.wohnzimmer_heating_state")
    assert state is not None
    assert state.attributes["friendly_name"] == "Wohnzimmer heating Heating state"
    assert hass.states.get("sensor.wohnzimmer_wohnzimmer_heating_state") is None


async def test_custom_room_without_area_has_no_area(hass, entry) -> None:
    await start(hass, entry)

    subentry_id = next(iter(entry.subentries))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry_id), entry.entry_id
    )
    assert device is not None
    assert device.name == "Living heating"
    assert device.area_id is None
    assert entry.runtime_data.areas == {subentry_id: None}


async def test_device_name_follows_room_name_unless_renamed_by_user(
    hass, entry
) -> None:
    await start(hass, entry)
    subentry_id = next(iter(entry.subentries))
    devices = dr.async_get(hass)

    def device() -> dr.DeviceEntry:
        found = devices.async_get_device_by_identifier(
            (DOMAIN, subentry_id), entry.entry_id
        )
        assert found is not None
        return found

    hass.config_entries.async_update_subentry(
        entry, entry.subentries[subentry_id], title="Salon"
    )
    await settle(hass)
    assert device().name == "Salon heating"
    assert device().name_by_user is None

    devices.async_update_device(device().id, name_by_user="My radiator")
    hass.config_entries.async_update_subentry(
        entry, entry.subentries[subentry_id], title="Lounge"
    )
    await settle(hass)
    assert device().name == "Lounge heating"
    assert device().name_by_user == "My radiator"
    state = hass.states.get(STATE_SENSOR)
    assert state is not None
    assert state.attributes["friendly_name"] == "My radiator Heating state"


async def test_state_sensor_reports_a_fault_after_failed_retries(
    hass, freezer, entry, fake_gateway
) -> None:
    await start(hass, entry)
    # The thermostat keeps reading back another value than the one written.
    fake_gateway.read_override[(IEEE_A, VALUE)] = 2000

    hass.states.async_set("sensor.t1", "22.0", {"unit_of_measurement": "°C"})
    await advance(hass, freezer, 70)

    state = hass.states.get(STATE_SENSOR)
    assert state.state == "fault"
    assert state.attributes["reasons"] == ["fault", "heating"]
    assert state.attributes["thermostats"] == {"climate.a": "error"}
    assert state.attributes["last_error"]
    assert state.attributes["hint"] == "climate.a: error"


async def test_each_room_gets_its_sensor_and_one_device(hass) -> None:
    entry = make_entry(
        hass,
        [
            room_data("Living", trvs=["climate.a"]),
            room_data("Bedroom", trvs=["climate.b"], temp_sensors=["sensor.t2"]),
        ],
        FAST_OPTIONS,
    )
    await start(hass, entry)

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert sorted(e.entity_id for e in entities if e.domain == "sensor") == [
        "sensor.bedroom_heating_state",
        "sensor.living_heating_state",
    ]
    by_name = {sub.title: sub_id for sub_id, sub in entry.subentries.items()}
    for name, entity_id in (
        ("Living", "sensor.living_heating_state"),
        ("Bedroom", "sensor.bedroom_heating_state"),
    ):
        registry_entry = er.async_get(hass).async_get(entity_id)
        assert registry_entry.config_subentry_id == by_name[name]
        assert registry_entry.unique_id == f"{by_name[name]}_state"
        device = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, by_name[name]), entry.entry_id
        )
        assert device is not None
        assert device.name == f"{name} heating"
        assert registry_entry.device_id == device.id


HEATING_STATES = [
    "fault",
    "off_manual",
    "off_window",
    "off_outdoor",
    "off_away",
    "window_pending",
    "emergency",
    "restricted_unavailable",
    "restricted_contact",
    "restricted_unsupported",
    "restricted_schedule",
    "restricted_window_phase",
    "boost",
    "frost",
    "heating",
]
ATTRIBUTES = [
    "hint",
    "reasons",
    "since",
    "window_off_at",
    "thermostats",
    "silent_contacts",
    "active_sensor",
    "source",
    "setpoint_source",
    "in_sync",
    "last_error",
]


async def test_state_sensor_created_per_room(hass, entry) -> None:
    await start(hass, entry)

    subentry_id = next(iter(entry.subentries))
    state = hass.states.get(STATE_SENSOR)
    assert state is not None
    assert state.state == "heating"
    assert state.attributes["friendly_name"] == "Living heating Heating state"
    assert state.attributes["device_class"] == SensorDeviceClass.ENUM
    assert state.attributes["options"] == HEATING_STATES

    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{subentry_id}_state")
        == STATE_SENSOR
    )
    registry_entry = registry.async_get(STATE_SENSOR)
    assert registry_entry is not None
    assert registry_entry.unique_id == f"{subentry_id}_state"
    assert registry_entry.translation_key == "room_state"
    assert registry_entry.config_subentry_id == subentry_id
    # Diagnostics: the status sensor it replaced was a diagnostic entity too.
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
    device = dr.async_get(hass).async_get(registry_entry.device_id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, subentry_id)}
    assert device.manufacturer == "Easy TRV"
    assert device.name == "Living heating"
    # The only sensor of the room: no status sensor next to it.
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert [e.entity_id for e in entities if e.domain == "sensor"] == [STATE_SENSOR]
    assert hass.states.get("sensor.living_heating_status") is None

    attributes = state.attributes
    assert [key for key in attributes if key in ATTRIBUTES] == ATTRIBUTES
    assert attributes["hint"] is None
    assert attributes["reasons"] == ["heating"]
    assert attributes["since"] is not None
    assert attributes["window_off_at"] is None
    assert attributes["thermostats"] == {}
    assert attributes["silent_contacts"] == []
    assert attributes["active_sensor"] == "sensor.t1"
    assert attributes["source"] == "external"
    assert attributes["setpoint_source"] is None
    assert attributes["in_sync"] is True
    assert attributes["last_error"] is None


async def test_an_existing_state_sensor_becomes_a_diagnostic_entity(
    hass, entry
) -> None:
    # 0.5.0 registered the sensor without a category; the update changes it.
    registry = er.async_get(hass)
    subentry_id = next(iter(entry.subentries))
    old = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{subentry_id}_state",
        config_entry=entry,
        config_subentry_id=subentry_id,
        suggested_object_id="living_heating_state",
    )
    assert old.entity_category is None

    await start(hass, entry)

    updated = registry.async_get(STATE_SENSOR)
    assert updated is not None
    assert updated.id == old.id
    assert updated.entity_category is EntityCategory.DIAGNOSTIC


async def test_state_sensor_shows_where_the_setpoint_came_from(hass) -> None:
    hass.states.async_set("climate.a", "heat", {"temperature": 21.0})
    hass.states.async_set("climate.b", "heat", {"temperature": 21.0})
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)

    await start(hass, entry)

    attributes = hass.states.get(STATE_SENSOR).attributes
    assert attributes["setpoint_source"] == "initial"
    assert attributes["in_sync"] is True


async def test_state_sensor_follows_the_schedule_of_a_thermostat(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)
    assert hass.states.get(STATE_SENSOR).attributes["in_sync"] is True

    fake_gateway.inject(IEEE_A, MODE, 1)
    await settle(hass)

    state = hass.states.get(STATE_SENSOR)
    assert state.state == "restricted_schedule"
    assert state.attributes["thermostats"] == {"climate.a": "schedule"}
    assert state.attributes["in_sync"] is False


async def test_state_sensor_follows_room(hass, freezer, entry) -> None:
    await start(hass, entry)
    first = hass.states.get(STATE_SENSOR).attributes["since"]
    assert first == dt_util.utcnow().isoformat()

    freezer.tick(timedelta(minutes=5))
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        "set_hvac_mode",
        {ATTR_ENTITY_ID: "climate.living_heating", "hvac_mode": "off"},
        blocking=True,
    )
    await settle(hass)

    state = hass.states.get(STATE_SENSOR)
    assert state.state == "off_manual"
    assert state.attributes["reasons"] == ["off_manual"]
    assert state.attributes["since"] == dt_util.utcnow().isoformat()
    assert state.attributes["since"] != first


async def test_state_sensor_window_pending_attribute(hass, freezer) -> None:
    hass.states.async_set("binary_sensor.window", "off")
    entry = make_entry(
        hass,
        [
            room_data(
                window_contacts=["binary_sensor.window"], window_open_delay_seconds=30
            )
        ],
        FAST_OPTIONS,
    )
    await start(hass, entry)
    assert hass.states.get(STATE_SENSOR).attributes["window_off_at"] is None

    hass.states.async_set("binary_sensor.window", "on")
    await settle(hass)

    state = hass.states.get(STATE_SENSOR)
    assert state.state == "window_pending"
    assert (
        state.attributes["window_off_at"]
        == (dt_util.utcnow() + timedelta(seconds=30)).isoformat()
    )
    assert state.attributes["reasons"] == ["window_pending", "heating"]

    # The delay runs out: the window holds the room off and the deadline is gone.
    await advance(hass, freezer, 30)
    state = hass.states.get(STATE_SENSOR)
    assert state.state == "off_window"
    assert state.attributes["window_off_at"] is None


async def test_state_sensor_silent_contact_attributes(hass) -> None:
    hass.states.async_set("binary_sensor.window", "unavailable")
    entry = make_entry(
        hass, [room_data(window_contacts=["binary_sensor.window"])], FAST_OPTIONS
    )
    await start(hass, entry)

    state = hass.states.get(STATE_SENSOR)
    assert state.state == "restricted_contact"
    assert state.attributes["silent_contacts"] == ["binary_sensor.window"]
    assert state.attributes["reasons"] == ["restricted_contact", "heating"]

    hass.states.async_set("binary_sensor.window", "off")
    await settle(hass)
    state = hass.states.get(STATE_SENSOR)
    assert state.state == "heating"
    assert state.attributes["silent_contacts"] == []


async def test_state_sensor_thermostat_deviation(hass, entry) -> None:
    await start(hass, entry)
    assert hass.states.get(STATE_SENSOR).attributes["thermostats"] == {}

    hass.states.async_set("climate.a", "unavailable")
    await settle(hass)

    state = hass.states.get(STATE_SENSOR)
    assert state.state == "restricted_unavailable"
    assert state.attributes["thermostats"] == {"climate.a": "unavailable"}

    hass.states.async_set("climate.a", "heat")
    await settle(hass)
    state = hass.states.get(STATE_SENSOR)
    assert state.state == "heating"
    assert state.attributes["thermostats"] == {}


async def test_state_sensor_unknown_before_start(hass, entry) -> None:
    # The room has not evaluated itself yet, so it has no state to show.
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    state = hass.states.get(STATE_SENSOR)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes["since"] is None
    # No reasons either: emergency mode next to an unknown state would mislead.
    assert state.attributes["reasons"] == []
    assert state.attributes["hint"] is None
    # The room has not published its sensor yet.
    assert "source" not in state.attributes
    assert "active_sensor" not in state.attributes

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)
    state = hass.states.get(STATE_SENSOR)
    assert state.state == "heating"
    assert state.attributes["since"] is not None
    assert state.attributes["reasons"] == ["heating"]
    assert state.attributes["source"] == "external"


async def test_removed_state_sensor_stops_listening(hass, entry) -> None:
    await start(hass, entry)
    controller = next(iter(entry.runtime_data.rooms.values()))
    # The room thermostat and the state sensor. The sensor hears every event the
    # room handles, so it needs no listener for the status on top of that.
    assert len(controller._update_listeners) == 2  # noqa: SLF001
    assert len(controller._listeners) == 0  # noqa: SLF001

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert len(controller._update_listeners) == 0  # noqa: SLF001


# --- The hint ---------------------------------------------------------------


def name_thermostat(hass, entity_id: str, name: str) -> None:
    """Give a thermostat's climate entity a device with a name."""
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("test", entity_id)}, name=name
    )
    # The registry will not hand out an entity id whose state exists already.
    shown = hass.states.get(entity_id)
    hass.states.async_remove(entity_id)
    entity = er.async_get(hass).async_get_or_create(
        "climate",
        "test",
        entity_id,
        suggested_object_id=entity_id.removeprefix("climate."),
        device_id=device.id,
    )
    assert entity.entity_id == entity_id
    hass.states.async_set(entity_id, shown.state, shown.attributes)


@pytest.fixture
def named_thermostats(hass) -> None:
    name_thermostat(hass, "climate.a", "Sofa valve")
    name_thermostat(hass, "climate.b", "Window valve")


def hint(hass) -> str | None:
    state = hass.states.get(STATE_SENSOR)
    assert state is not None
    return state.attributes["hint"]


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_is_empty_while_the_room_heats(hass, entry) -> None:
    await start(hass, entry)

    assert hass.states.get(STATE_SENSOR).state == "heating"
    assert hint(hass) is None


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_names_a_thermostat_that_is_not_reachable(hass, entry) -> None:
    await start(hass, entry)

    hass.states.async_set("climate.a", "unavailable")
    await settle(hass)
    assert hass.states.get(STATE_SENSOR).state == "restricted_unavailable"
    assert hint(hass) == "Sofa valve not reachable"

    hass.states.async_set("climate.b", "unavailable")
    await settle(hass)
    assert hint(hass) == "Sofa valve not reachable; Window valve not reachable"

    hass.states.async_set("climate.a", "heat")
    hass.states.async_set("climate.b", "heat")
    await settle(hass)
    assert hass.states.get(STATE_SENSOR).state == "heating"
    assert hint(hass) is None


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_names_a_thermostat_in_error(
    hass, freezer, entry, fake_gateway
) -> None:
    await start(hass, entry)
    fake_gateway.read_override[(IEEE_A, VALUE)] = 2000

    hass.states.async_set("sensor.t1", "22.0", {"unit_of_measurement": "°C"})
    await advance(hass, freezer, 70)

    assert hass.states.get(STATE_SENSOR).state == "fault"
    assert hint(hass) == "Sofa valve: error"


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_names_a_thermostat_on_its_schedule(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)

    fake_gateway.inject(IEEE_B, MODE, 1)
    await settle(hass)

    assert hass.states.get(STATE_SENSOR).state == "restricted_schedule"
    assert hint(hass) == "Window valve runs its own schedule"


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_names_a_thermostat_in_its_window_phase(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)

    fake_gateway.inject(IEEE_A, SETPOINT, 1200)
    await settle(hass)

    assert hass.states.get(STATE_SENSOR).state == "restricted_window_phase"
    assert hint(hass) == "Sofa valve: own window detection active"


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_names_what_a_thermostat_lacks(hass, fake_gateway) -> None:
    # Firmware 1.3.0 has no adaptive mode, which the room asks for by default.
    fake_gateway.add_device("climate.a", IEEE_A, firmware=(1, 3, 0))
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)

    await start(hass, entry)

    assert hass.states.get(STATE_SENSOR).state == "restricted_unsupported"
    assert hint(hass) == "Sofa valve: lacks a function (Adaptive mode)"


async def test_hint_names_the_contacts_and_why_they_are_silent(hass) -> None:
    hass.states.async_set(
        "binary_sensor.balcony", "unavailable", {"friendly_name": "Balcony door"}
    )
    hass.states.async_set(
        "binary_sensor.bath", "unknown", {"friendly_name": "Bath window"}
    )
    hass.states.async_set("binary_sensor.door", "unavailable")
    hass.states.async_set("binary_sensor.fine", "off", {"friendly_name": "Fine"})
    contacts = [
        "binary_sensor.balcony",
        "binary_sensor.bath",
        "binary_sensor.door",
        "binary_sensor.fine",
        "binary_sensor.ghost",
    ]
    entry = make_entry(hass, [room_data(window_contacts=contacts)], FAST_OPTIONS)

    await start(hass, entry)

    state = hass.states.get(STATE_SENSOR)
    assert state.state == "restricted_contact"
    # A contact without a name shows the name Home Assistant derives from its id;
    # one that does not exist at all shows its entity id. A contact that reports
    # is not named.
    assert state.attributes["hint"] == (
        "Balcony door reports nothing (unavailable); "
        "Bath window reports nothing (unknown); "
        "door reports nothing (unavailable); "
        "binary_sensor.ghost reports nothing (does not exist)"
    )

    hass.states.async_set("binary_sensor.ghost", "off")
    hass.states.async_set("binary_sensor.door", "off")
    await settle(hass)
    assert hint(hass) == (
        "Balcony door reports nothing (unavailable); "
        "Bath window reports nothing (unknown)"
    )


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_joins_the_reasons_in_priority_order(
    hass, freezer, fake_gateway
) -> None:
    hass.states.async_set(
        "binary_sensor.balcony", "off", {"friendly_name": "Balcony door"}
    )
    entry = make_entry(
        hass, [room_data(window_contacts=["binary_sensor.balcony"])], FAST_OPTIONS
    )
    await start(hass, entry)
    fake_gateway.read_override[(IEEE_A, VALUE)] = 2000
    hass.states.async_set("climate.b", "unavailable")
    hass.states.async_set(
        "binary_sensor.balcony", "unavailable", {"friendly_name": "Balcony door"}
    )
    hass.states.async_set("sensor.t1", "22.0", {"unit_of_measurement": "°C"})
    await advance(hass, freezer, 70)

    state = hass.states.get(STATE_SENSOR)
    assert state.state == "fault"
    assert state.attributes["reasons"] == [
        "fault",
        "restricted_unavailable",
        "restricted_contact",
        "heating",
    ]
    assert state.attributes["hint"] == (
        "Sofa valve: error; "
        "Window valve not reachable; "
        "Balcony door reports nothing (unavailable)"
    )


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_lists_every_reason_of_a_thermostat(hass, fake_gateway) -> None:
    fake_gateway.add_device("climate.a", IEEE_A, firmware=(1, 3, 0))
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)
    await start(hass, entry)

    # The thermostat that lacks a function also runs its schedule, the other one
    # its window phase.
    fake_gateway.inject(IEEE_A, MODE, 1)
    fake_gateway.inject(IEEE_B, SETPOINT, 1200)
    await settle(hass)

    state = hass.states.get(STATE_SENSOR)
    assert state.state == "restricted_unsupported"
    assert state.attributes["reasons"] == [
        "restricted_unsupported",
        "restricted_schedule",
        "restricted_window_phase",
        "heating",
    ]
    assert state.attributes["hint"] == (
        "Sofa valve: lacks a function (Adaptive mode); "
        "Sofa valve runs its own schedule; "
        "Window valve: own window detection active"
    )


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_is_written_in_german(hass, fake_gateway) -> None:
    hass.config.language = "de"
    fake_gateway.add_device("climate.a", IEEE_A, firmware=(1, 3, 0))
    hass.states.async_set("climate.b", "unavailable")
    hass.states.async_set(
        "binary_sensor.balkon", "unknown", {"friendly_name": "Balkontür"}
    )
    hass.states.async_set("binary_sensor.bad", "unavailable", {"friendly_name": "Bad"})
    entry = make_entry(
        hass,
        [
            room_data(
                window_contacts=[
                    "binary_sensor.bad",
                    "binary_sensor.balkon",
                    "binary_sensor.ghost",
                ]
            )
        ],
        FAST_OPTIONS,
    )

    await start(hass, entry)

    assert hint(hass) == (
        "Window valve nicht erreichbar; "
        "Bad meldet nichts (nicht verfügbar); "
        "Balkontür meldet nichts (unbekannt); "
        "binary_sensor.ghost meldet nichts (gibt es nicht); "
        "Sofa valve: Funktion fehlt (Adaptiver Modus)"
    )

    fake_gateway.inject(IEEE_A, MODE, 1)
    await settle(hass)
    assert "Sofa valve läuft im eigenen Wochenplan" in hint(hass)
    fake_gateway.inject(IEEE_A, MODE, 4)
    fake_gateway.inject(IEEE_A, SETPOINT, 1200)
    await settle(hass)
    assert hint(hass).endswith("Sofa valve: eigene Fenstererkennung aktiv")


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_falls_back_to_english_for_a_missing_phrase(hass) -> None:
    hass.states.async_set("climate.a", "unavailable")
    hass.states.async_set("binary_sensor.balcony", "unavailable")
    entry = make_entry(
        hass, [room_data(window_contacts=["binary_sensor.balcony"])], FAST_OPTIONS
    )

    partial = {"unavailable": "{name} ist weg", "why_unavailable": "tot"}
    with patch(
        "custom_components.easy_trv.sensor.async_option_labels",
        return_value=partial,
    ) as loaded:
        await start(hass, entry)

    loaded.assert_called_once_with(hass, "hint")
    # Two phrases are translated, the contact phrase is not.
    assert hint(hass) == ("Sofa valve ist weg; balcony reports nothing (tot)")


@pytest.mark.usefixtures("named_thermostats")
async def test_hint_without_any_translation_is_english(hass) -> None:
    hass.states.async_set("climate.a", "unavailable")
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)

    with patch(
        "custom_components.easy_trv.sensor.async_option_labels", return_value={}
    ):
        await start(hass, entry)

    assert hint(hass) == "Sofa valve not reachable"


def test_translations_cover_the_sensor_states_and_issues() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    icons = json.loads((COMPONENT / "icons.json").read_text())

    assert strings == english
    assert set(strings["entity"]["sensor"]) == {"room_state"}
    assert strings["device"]["room"]["name"] == "{room} heating"
    assert set(strings["issues"]) == {
        "trv_unreachable",
        "trv_firmware",
        "zha_api_incompatible",
        "profile_invalid",
        "room_invalid",
        "settings_migrated",
        "window_open_long",
        "window_contact_suspect",
        "window_contact_unavailable",
        "schedule_thermostat_off",
    }
    assert set(icons["entity"]["sensor"]) == {"room_state"}
    state = strings["entity"]["sensor"]["room_state"]
    assert state["name"] == "Heating state"
    assert list(state["state"]) == HEATING_STATES
    assert list(state["state_attributes"]) == ATTRIBUTES
    state_icons = icons["entity"]["sensor"]["room_state"]
    assert state_icons["default"]
    assert state_icons["state"] == {
        "fault": "mdi:alert-circle",
        "off_manual": "mdi:radiator-off",
        "off_window": "mdi:window-open-variant",
        "off_outdoor": "mdi:sun-thermometer",
        "off_away": "mdi:home-export-outline",
        "window_pending": "mdi:timer-sand",
        "emergency": "mdi:thermometer-alert",
        "restricted_unavailable": "mdi:lan-disconnect",
        "restricted_contact": "mdi:signal-off",
        "restricted_unsupported": "mdi:puzzle-remove-outline",
        "restricted_schedule": "mdi:calendar-clock",
        "restricted_window_phase": "mdi:window-open",
        "boost": "mdi:fire",
        "frost": "mdi:snowflake-thermometer",
        "heating": "mdi:radiator",
    }
