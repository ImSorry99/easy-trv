from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easy_trv.labels import (
    async_capability_labels,
    async_option_labels,
    thermostat_name,
)

INTENTS = {
    "set_room_temperature",
    "set_source",
    "set_window_detection",
    "set_hysteresis",
    "set_adaptive_mode",
    "set_frost_temperature",
}


async def test_capability_labels_come_from_translations(hass) -> None:
    labels = await async_capability_labels(hass)
    assert set(labels) == INTENTS
    assert labels["set_window_detection"] == "Thermostat's open-window detection"
    assert labels["set_room_temperature"] == "Room temperature"


async def test_capability_labels_follow_the_language(hass) -> None:
    hass.config.language = "de"
    labels = await async_capability_labels(hass)
    assert labels["set_window_detection"] == "Fenster-offen-Erkennung des Thermostats"
    assert labels["set_room_temperature"] == "Raumtemperatur"


async def test_option_labels_are_read_by_option_value(hass) -> None:
    labels = await async_option_labels(hass, "window_detection")
    assert labels["on"] == "Detection on"
    assert labels["leave"] == "Leave as set in the thermostat"
    assert await async_option_labels(hass, "no_such_selector") == {}


def _device_entity(hass, *, name, name_by_user=None) -> str:
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={("test", "dev1")},
        name=name,
    )
    if name_by_user is not None:
        dr.async_get(hass).async_update_device(device.id, name_by_user=name_by_user)
    entity = er.async_get(hass).async_get_or_create(
        "climate", "test", "dev1-climate", device_id=device.id
    )
    return entity.entity_id


async def test_thermostat_name_is_the_device_name(hass) -> None:
    entity_id = _device_entity(hass, name="Living TRV")
    assert thermostat_name(hass, entity_id) == "Living TRV"


async def test_thermostat_name_prefers_the_name_chosen_by_the_user(hass) -> None:
    entity_id = _device_entity(hass, name="Living TRV", name_by_user="Sofa valve")
    assert thermostat_name(hass, entity_id) == "Sofa valve"


async def test_thermostat_name_falls_back_to_the_entity_id(hass) -> None:
    # Not in the registry at all.
    assert thermostat_name(hass, "climate.ghost") == "climate.ghost"
    # In the registry, but without a device.
    entity = er.async_get(hass).async_get_or_create("climate", "test", "loner")
    assert thermostat_name(hass, entity.entity_id) == entity.entity_id
