import json
from datetime import timedelta
from pathlib import Path

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

from custom_components.easy_trv.const import CONF_AREA, DOMAIN
from tests.helpers_entry import (
    FAST_OPTIONS,
    IEEE_A,
    advance,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)

COMPONENT = Path(__file__).parent.parent / "custom_components" / "easy_trv"
VALUE = 0x600D


@pytest.fixture(autouse=True)
def fake_gateway(hass):
    with patched_gateway(hass) as gateway:
        yield gateway


@pytest.fixture
def entry(hass):
    return make_entry(hass, [room_data()], FAST_OPTIONS)


async def test_status_sensor_describes_itself(hass, entry) -> None:
    await start(hass, entry)

    state = hass.states.get("sensor.living_heating_status")
    assert state is not None
    assert state.state == "ok"
    assert state.attributes["friendly_name"] == "Living heating Heating status"
    assert state.attributes["device_class"] == SensorDeviceClass.ENUM
    assert state.attributes["options"] == ["ok", "degraded", "error"]
    assert state.attributes["source"] == "external"
    assert state.attributes["active_sensor"] == "sensor.t1"
    assert state.attributes["trv_health"] == {"climate.a": "ok", "climate.b": "ok"}

    registry_entry = er.async_get(hass).async_get("sensor.living_heating_status")
    assert registry_entry is not None
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert registry_entry.translation_key == "room_status"
    device = dr.async_get(hass).async_get(registry_entry.device_id)
    assert device is not None
    assert device.manufacturer == "Easy TRV"
    assert device.name == "Living heating"


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
        "sensor", DOMAIN, f"{subentry_id}_status"
    )
    assert entity_id == "sensor.wohnzimmer_heating_status"
    state = hass.states.get("sensor.wohnzimmer_heating_status")
    assert state is not None
    assert state.attributes["friendly_name"] == "Wohnzimmer heating Heating status"
    assert hass.states.get("sensor.wohnzimmer_wohnzimmer_heating_status") is None


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
    state = hass.states.get("sensor.living_heating_status")
    assert state is not None
    assert state.attributes["friendly_name"] == "My radiator Heating status"


async def test_status_sensor_follows_the_room_status(hass, entry) -> None:
    await start(hass, entry)
    assert hass.states.get("sensor.living_heating_status").state == "ok"

    hass.states.async_set("climate.a", "unavailable")
    await settle(hass)
    state = hass.states.get("sensor.living_heating_status")
    assert state.state == "degraded"
    assert state.attributes["trv_health"]["climate.a"] == "unavailable"

    hass.states.async_set("climate.a", "heat")
    await settle(hass)
    assert hass.states.get("sensor.living_heating_status").state == "ok"


async def test_status_sensor_reports_error_after_failed_retries(
    hass, freezer, entry, fake_gateway
) -> None:
    await start(hass, entry)
    # The thermostat keeps reading back another value than the one written.
    fake_gateway.read_override[(IEEE_A, VALUE)] = 2000

    hass.states.async_set("sensor.t1", "22.0", {"unit_of_measurement": "°C"})
    await advance(hass, freezer, 70)

    state = hass.states.get("sensor.living_heating_status")
    assert state.state == "error"
    assert state.attributes["trv_health"]["climate.a"] == "error"
    assert state.attributes["last_error"]


async def test_each_room_gets_its_two_sensors_and_one_device(hass) -> None:
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
        "sensor.bedroom_heating_status",
        "sensor.living_heating_state",
        "sensor.living_heating_status",
    ]
    by_name = {sub.title: sub_id for sub_id, sub in entry.subentries.items()}
    for name, entity_id in (
        ("Living", "sensor.living_heating_status"),
        ("Bedroom", "sensor.bedroom_heating_status"),
    ):
        registry_entry = er.async_get(hass).async_get(entity_id)
        assert registry_entry.config_subentry_id == by_name[name]
        assert registry_entry.unique_id == f"{by_name[name]}_status"
        device = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, by_name[name]), entry.entry_id
        )
        assert device is not None
        assert device.name == f"{name} heating"
        state_entry = er.async_get(hass).async_get(
            entity_id.replace("_status", "_state")
        )
        assert state_entry.config_subentry_id == by_name[name]
        assert state_entry.unique_id == f"{by_name[name]}_state"
        assert state_entry.device_id == device.id


async def test_removed_sensor_stops_listening(hass, entry) -> None:
    await start(hass, entry)
    subentry_id = next(iter(entry.subentries))
    controller = entry.runtime_data.rooms[subentry_id]
    listeners = len(controller._listeners)  # noqa: SLF001
    assert listeners == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert len(controller._listeners) == 0  # noqa: SLF001
    assert len(controller._update_listeners) == 0  # noqa: SLF001


async def test_status_sensor_has_the_room_attributes(hass, fake_gateway) -> None:
    hass.states.async_set("binary_sensor.window", "off")
    hass.states.async_set("climate.a", "heat", {"temperature": 21.0})
    hass.states.async_set("climate.b", "heat", {"temperature": 21.0})
    entry = make_entry(
        hass,
        [
            room_data(
                window_contacts=["binary_sensor.window"], window_open_delay_seconds=0
            )
        ],
        FAST_OPTIONS,
    )
    await start(hass, entry)

    attributes = hass.states.get("sensor.living_heating_status").attributes
    assert attributes["window_open"] is False
    assert attributes["outdoor_cutoff"] is False
    assert attributes["setpoint_source"] == "initial"
    assert attributes["in_sync"] is True
    assert attributes["schedule_thermostats"] == []

    # The sensor follows the room without a new status: a window opens ...
    hass.states.async_set("binary_sensor.window", "on")
    await settle(hass)
    attributes = hass.states.get("sensor.living_heating_status").attributes
    assert attributes["window_open"] is True

    # ... and a thermostat runs its schedule.
    hass.states.async_set("binary_sensor.window", "off")
    await settle(hass)
    fake_gateway.inject(IEEE_A, 0x001C, 1)
    await settle(hass)
    attributes = hass.states.get("sensor.living_heating_status").attributes
    assert attributes["window_open"] is False
    assert attributes["schedule_thermostats"] == ["climate.a"]
    assert attributes["in_sync"] is False


HEATING_STATES = [
    "fault",
    "off_manual",
    "off_window",
    "off_outdoor",
    "off_away",
    "window_pending",
    "emergency",
    "restricted",
    "boost",
    "frost",
    "heating",
]
STATE_SENSOR = "sensor.living_heating_state"


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
    # It belongs on dashboards: no entity category, unlike the status sensor.
    assert registry_entry.entity_category is None
    # The room's device, the same the status sensor sits on.
    status_entry = registry.async_get("sensor.living_heating_status")
    assert status_entry is not None
    assert registry_entry.device_id == status_entry.device_id
    device = dr.async_get(hass).async_get(registry_entry.device_id)
    assert device is not None
    assert device.identifiers == {(DOMAIN, subentry_id)}
    assert device.manufacturer == "Easy TRV"
    assert device.name == "Living heating"

    attributes = state.attributes
    assert attributes["reasons"] == ["heating"]
    assert attributes["since"] is not None
    assert attributes["window_off_at"] is None
    assert attributes["thermostats"] == {}
    assert attributes["silent_contacts"] == []


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
    assert state.state == "restricted"
    assert state.attributes["silent_contacts"] == ["binary_sensor.window"]
    assert state.attributes["reasons"] == ["restricted", "heating"]

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
    assert state.state == "restricted"
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

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)
    state = hass.states.get(STATE_SENSOR)
    assert state.state == "heating"
    assert state.attributes["since"] is not None
    assert state.attributes["reasons"] == ["heating"]


async def test_removed_state_sensor_stops_listening(hass, entry) -> None:
    await start(hass, entry)
    controller = next(iter(entry.runtime_data.rooms.values()))
    # The room thermostat, the status sensor and the state sensor.
    assert len(controller._update_listeners) == 3  # noqa: SLF001

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert len(controller._update_listeners) == 0  # noqa: SLF001


def test_translations_cover_the_sensor_states_and_issues() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    icons = json.loads((COMPONENT / "icons.json").read_text())

    assert strings == english
    status = strings["entity"]["sensor"]["room_status"]
    assert status["name"] == "Heating status"
    assert set(status["state"]) == {"ok", "degraded", "error"}
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
    icon = icons["entity"]["sensor"]["room_status"]
    assert icon["default"] == "mdi:radiator"
    assert icon["state"] == {"error": "mdi:radiator-off"}
    state = strings["entity"]["sensor"]["room_state"]
    assert state["name"] == "Heating state"
    assert list(state["state"]) == HEATING_STATES
    assert set(state["state_attributes"]) == {
        "reasons",
        "since",
        "window_off_at",
        "thermostats",
        "silent_contacts",
    }
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
        "restricted": "mdi:alert",
        "boost": "mdi:fire",
        "frost": "mdi:snowflake-thermometer",
        "heating": "mdi:radiator",
    }
