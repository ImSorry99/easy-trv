import json
from pathlib import Path

import pytest
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.easy_trv.const import DOMAIN
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

    state = hass.states.get("sensor.living_status")
    assert state is not None
    assert state.state == "ok"
    assert state.attributes["friendly_name"] == "Living Status"
    assert state.attributes["device_class"] == SensorDeviceClass.ENUM
    assert state.attributes["options"] == ["ok", "degraded", "error"]
    assert state.attributes["source"] == "external"
    assert state.attributes["active_sensor"] == "sensor.t1"
    assert state.attributes["trv_health"] == {"climate.a": "ok", "climate.b": "ok"}

    registry_entry = er.async_get(hass).async_get("sensor.living_status")
    assert registry_entry is not None
    assert registry_entry.entity_category is EntityCategory.DIAGNOSTIC
    assert registry_entry.translation_key == "room_status"
    device = dr.async_get(hass).async_get(registry_entry.device_id)
    assert device is not None
    assert device.manufacturer == "Easy TRV"
    assert device.name == "Living"


async def test_status_sensor_follows_the_room_status(hass, entry) -> None:
    await start(hass, entry)
    assert hass.states.get("sensor.living_status").state == "ok"

    hass.states.async_set("climate.a", "unavailable")
    await settle(hass)
    state = hass.states.get("sensor.living_status")
    assert state.state == "degraded"
    assert state.attributes["trv_health"]["climate.a"] == "unavailable"

    hass.states.async_set("climate.a", "heat")
    await settle(hass)
    assert hass.states.get("sensor.living_status").state == "ok"


async def test_status_sensor_reports_error_after_failed_retries(
    hass, freezer, entry, fake_gateway
) -> None:
    await start(hass, entry)
    # The thermostat keeps reading back another value than the one written.
    fake_gateway.read_override[(IEEE_A, VALUE)] = 2000

    hass.states.async_set("sensor.t1", "22.0", {"unit_of_measurement": "°C"})
    await advance(hass, freezer, 70)

    state = hass.states.get("sensor.living_status")
    assert state.state == "error"
    assert state.attributes["trv_health"]["climate.a"] == "error"
    assert state.attributes["last_error"]


async def test_each_room_gets_exactly_one_sensor_and_device(hass) -> None:
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
    assert sorted(e.entity_id for e in entities) == [
        "sensor.bedroom_status",
        "sensor.living_status",
    ]
    by_name = {sub.title: sub_id for sub_id, sub in entry.subentries.items()}
    for name, entity_id in (
        ("Living", "sensor.living_status"),
        ("Bedroom", "sensor.bedroom_status"),
    ):
        registry_entry = er.async_get(hass).async_get(entity_id)
        assert registry_entry.config_subentry_id == by_name[name]
        assert registry_entry.unique_id == f"{by_name[name]}_status"
        device = dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, by_name[name]), entry.entry_id
        )
        assert device is not None
        assert device.name == name


async def test_removed_sensor_stops_listening(hass, entry) -> None:
    await start(hass, entry)
    subentry_id = next(iter(entry.subentries))
    controller = entry.runtime_data.rooms[subentry_id]
    listeners = len(controller._listeners)  # noqa: SLF001
    assert listeners == 1

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert len(controller._listeners) == 0  # noqa: SLF001


def test_translations_cover_the_sensor_states_and_issues() -> None:
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    icons = json.loads((COMPONENT / "icons.json").read_text())

    assert strings == english
    status = strings["entity"]["sensor"]["room_status"]
    assert status["name"] == "Status"
    assert set(status["state"]) == {"ok", "degraded", "error"}
    assert set(strings["issues"]) == {
        "trv_unreachable",
        "trv_firmware",
        "zha_api_incompatible",
        "profile_invalid",
        "room_invalid",
    }
    icon = icons["entity"]["sensor"]["room_status"]
    assert icon["default"] == "mdi:radiator"
    assert icon["state"] == {"error": "mdi:radiator-off"}
