import pytest
from homeassistant.components.select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    SERVICE_SELECT_OPTION,
)
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.helpers import entity_registry as er

from custom_components.easy_trv.core.model import OperatingMode
from tests.helpers_entry import (
    FAST_OPTIONS,
    IEEE_A,
    IEEE_B,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)
from tests.test_climate import profile_without

SELECT = "select.living_heating_mode"
CLIMATE = "climate.living_heating"
SETPOINT = 0x0012
MODE = 0x001C
HEAT, OFF = 4, 0


@pytest.fixture(autouse=True)
def fake_gateway(hass):
    with patched_gateway(hass) as gateway:
        # Both thermostats show 21 °C at start, so the room adopts it.
        for entity_id in ("climate.a", "climate.b"):
            hass.states.async_set(entity_id, "heat", {"temperature": 21.0})
        yield gateway


@pytest.fixture
def entry(hass):
    return make_entry(hass, [room_data()], FAST_OPTIONS)


def writes(gateway, attribute: int) -> list[tuple[str, int]]:
    """Return the ``(ieee, raw)`` pairs written to one attribute so far."""
    return [(w[0], w[4]) for w in gateway.writes if w[3] == attribute]


async def select(hass, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN,
        SERVICE_SELECT_OPTION,
        {ATTR_ENTITY_ID: SELECT, ATTR_OPTION: option},
        blocking=True,
    )
    await settle(hass)


async def test_the_operating_mode_entity(hass, entry) -> None:
    await start(hass, entry)

    state = hass.states.get(SELECT)
    assert state is not None
    assert state.state == "heat"
    assert state.attributes[ATTR_OPTIONS] == ["off", "frost", "heat"]
    assert state.attributes["friendly_name"] == "Living heating Operating mode"

    registry = er.async_get(hass)
    registry_entry = registry.async_get(SELECT)
    assert registry_entry is not None
    subentry_id = next(iter(entry.subentries))
    assert registry_entry.unique_id == f"{subentry_id}_operating_mode"
    assert registry_entry.config_subentry_id == subentry_id
    assert registry_entry.translation_key == "operating_mode"
    assert registry_entry.device_id == registry.async_get(CLIMATE).device_id


async def test_frost_protection_and_back_to_heating(hass, entry, fake_gateway) -> None:
    await start(hass, entry)
    fake_gateway.writes.clear()

    await select(hass, "frost")

    assert hass.states.get(SELECT).state == "frost"
    climate = hass.states.get(CLIMATE)
    assert climate.state == "heat"
    assert climate.attributes[ATTR_TEMPERATURE] == 5.0
    assert climate.attributes["setpoint_source"] == "frost"
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 500), (IEEE_B, 500)]
    assert writes(fake_gateway, MODE) == []

    fake_gateway.writes.clear()
    await select(hass, "heat")

    assert hass.states.get(SELECT).state == "heat"
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 21.0
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 2100), (IEEE_B, 2100)]


async def test_off_ends_frost_protection(hass, entry, fake_gateway) -> None:
    await start(hass, entry)
    await select(hass, "frost")
    fake_gateway.writes.clear()

    await select(hass, "off")

    assert hass.states.get(SELECT).state == "off"
    assert hass.states.get(CLIMATE).state == "off"
    assert sorted(writes(fake_gateway, MODE)) == [(IEEE_A, OFF), (IEEE_B, OFF)]

    await select(hass, "heat")

    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 21.0
    assert sorted(writes(fake_gateway, MODE)[-2:]) == [(IEEE_A, HEAT), (IEEE_B, HEAT)]


async def test_frost_from_off_switches_the_room_on(hass, entry, fake_gateway) -> None:
    await start(hass, entry)
    await select(hass, "off")
    fake_gateway.writes.clear()

    await select(hass, "frost")

    climate = hass.states.get(CLIMATE)
    assert (climate.state, climate.attributes[ATTR_TEMPERATURE]) == ("heat", 5.0)
    assert sorted(writes(fake_gateway, MODE)) == [(IEEE_A, HEAT), (IEEE_B, HEAT)]
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 500), (IEEE_B, 500)]


async def test_the_selection_follows_the_room_thermostat(hass, entry) -> None:
    await start(hass, entry)
    controller = next(iter(entry.runtime_data.rooms.values()))
    await select(hass, "frost")

    # A new target temperature ends frost protection.
    await controller.async_set_setpoint(19.0)
    await settle(hass)
    assert hass.states.get(SELECT).state == "heat"

    await controller.async_set_operation(OperatingMode.OFF)
    await settle(hass)
    assert hass.states.get(SELECT).state == "off"


async def test_a_room_without_a_room_thermostat_has_no_operating_mode(
    hass, tmp_path
) -> None:
    hass.config.config_dir = str(tmp_path)
    profile = profile_without(tmp_path, "set_setpoint")
    entry = make_entry(hass, [room_data(profile=profile)], FAST_OPTIONS)

    await start(hass, entry)

    assert hass.states.get("sensor.living_heating_status") is not None
    assert hass.states.get(SELECT) is None
