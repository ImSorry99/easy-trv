import pytest
from homeassistant.components.climate import (
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    ATTR_TARGET_TEMP_STEP,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    ATTR_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import State
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import async_get as async_restore_data
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.easy_trv.core.model import Note
from custom_components.easy_trv.profiles.loader import BUILTIN_DIR
from tests.helpers_entry import (
    FAST_OPTIONS,
    FIRMWARE,
    IEEE_A,
    IEEE_B,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)

CLIMATE = "climate.living_heating"
SETPOINT = 0x0012
MODE = 0x001C
HEAT, OFF = 4, 0
BUILTIN_TEXT = (BUILTIN_DIR / "sonoff_trvzb_native.yaml").read_text()


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


async def call(hass, service: str, **data) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: CLIMATE, **data}, blocking=True
    )
    await settle(hass)


async def test_room_climate_entity_properties(hass, entry) -> None:
    await start(hass, entry)

    state = hass.states.get(CLIMATE)
    assert state is not None
    assert state.state == "heat"
    attributes = state.attributes
    assert attributes[ATTR_TEMPERATURE] == 21.0
    assert attributes[ATTR_MIN_TEMP] == 5.0
    assert attributes[ATTR_MAX_TEMP] == 30.0
    assert attributes[ATTR_TARGET_TEMP_STEP] == 0.5
    assert attributes[ATTR_HVAC_MODES] == [HVACMode.HEAT, HVACMode.OFF]
    assert attributes[ATTR_PRESET_MODES] == ["none", "frost"]
    assert attributes[ATTR_PRESET_MODE] == "none"
    assert attributes[ATTR_SUPPORTED_FEATURES] == (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )
    assert "hvac_action" not in attributes
    assert attributes["current_temperature"] == 21.3
    assert attributes["setpoint_source"] == "initial"
    assert attributes["in_sync"] is True
    assert attributes["thermostats"] == ["climate.a", "climate.b"]
    assert attributes["window_open"] is False
    assert attributes["outdoor_cutoff"] is False
    assert attributes["note"] is None
    assert "frost_return" not in attributes
    assert attributes["friendly_name"] == "Living heating"

    registry_entry = er.async_get(hass).async_get(CLIMATE)
    assert registry_entry is not None
    subentry_id = next(iter(entry.subentries))
    assert registry_entry.unique_id == f"{subentry_id}_climate"
    assert registry_entry.config_subentry_id == subentry_id
    assert registry_entry.translation_key == "room"
    assert registry_entry.unit_of_measurement is None
    status = er.async_get(hass).async_get("sensor.living_heating_status")
    assert status is not None
    assert registry_entry.device_id == status.device_id
    device = dr.async_get(hass).async_get(registry_entry.device_id)
    assert device is not None
    assert device.name == "Living heating"


async def test_set_temperature_service_writes_the_thermostats(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)
    fake_gateway.writes.clear()

    await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 22.5})

    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 2250), (IEEE_B, 2250)]
    state = hass.states.get(CLIMATE)
    assert state.attributes[ATTR_TEMPERATURE] == 22.5
    assert state.attributes["setpoint_source"] == "ha"
    assert state.attributes["in_sync"] is True


async def test_set_temperature_is_clamped_into_the_range(hass, entry) -> None:
    await start(hass, entry)

    await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 29.9})
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 30.0


async def test_turn_off_and_on(hass, entry, fake_gateway) -> None:
    await start(hass, entry)
    fake_gateway.writes.clear()

    await call(hass, SERVICE_TURN_OFF)

    assert hass.states.get(CLIMATE).state == "off"
    assert sorted(writes(fake_gateway, MODE)) == [(IEEE_A, OFF), (IEEE_B, OFF)]

    fake_gateway.writes.clear()
    await call(hass, SERVICE_TURN_ON)

    assert hass.states.get(CLIMATE).state == "heat"
    assert sorted(writes(fake_gateway, MODE)) == [(IEEE_A, HEAT), (IEEE_B, HEAT)]

    fake_gateway.writes.clear()
    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.OFF)
    assert hass.states.get(CLIMATE).state == "off"
    assert writes(fake_gateway, MODE)


async def test_preset_frost_and_back(hass, entry, fake_gateway) -> None:
    await start(hass, entry)
    fake_gateway.writes.clear()

    await call(hass, SERVICE_SET_PRESET_MODE, preset_mode="frost")

    state = hass.states.get(CLIMATE)
    assert state.attributes[ATTR_PRESET_MODE] == "frost"
    assert state.attributes[ATTR_TEMPERATURE] == 5.0
    assert state.attributes["setpoint_source"] == "frost"
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 500), (IEEE_B, 500)]

    await call(hass, SERVICE_SET_PRESET_MODE, preset_mode="none")

    state = hass.states.get(CLIMATE)
    assert state.attributes[ATTR_PRESET_MODE] == "none"
    assert state.attributes[ATTR_TEMPERATURE] == 21.0


async def test_the_state_follows_the_controller_without_a_service(hass, entry) -> None:
    await start(hass, entry)
    controller = next(iter(entry.runtime_data.rooms.values()))

    await controller.async_set_setpoint(23.0)
    await settle(hass)

    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 23.0


async def test_schedule_note_is_shown(hass, entry, fake_gateway) -> None:
    await start(hass, entry)
    controller = next(iter(entry.runtime_data.rooms.values()))

    fake_gateway.inject(IEEE_A, MODE, 1)  # the thermostat runs its schedule
    await settle(hass)

    assert controller.notes == {"climate.a": Note.SCHEDULE}
    assert hass.states.get(CLIMATE).attributes["note"] == "schedule"
    assert hass.states.get(CLIMATE).attributes["in_sync"] is False


async def test_listener_is_released_when_the_entry_unloads(hass, entry) -> None:
    await start(hass, entry)
    controller = next(iter(entry.runtime_data.rooms.values()))
    assert controller._update_listeners  # noqa: SLF001

    assert await hass.config_entries.async_unload(entry.entry_id)
    await settle(hass)

    assert controller._update_listeners == []  # noqa: SLF001


# --- Restore -----------------------------------------------------------------


def remember(
    hass,
    state: str = "heat",
    temperature: float = 24.0,
    preset: str = "none",
    frost_return: float | None = None,
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(
                    CLIMATE,
                    state,
                    {ATTR_TEMPERATURE: temperature, ATTR_PRESET_MODE: preset},
                ),
                {"frost_return": frost_return},
            )
        ],
    )


async def test_restore_brings_back_setpoint_mode_and_preset(
    hass, entry, fake_gateway
) -> None:
    remember(hass, "heat", 24.0)

    await start(hass, entry)

    state = hass.states.get(CLIMATE)
    assert state.state == "heat"
    assert state.attributes[ATTR_TEMPERATURE] == 24.0
    assert state.attributes["setpoint_source"] == "restore"
    # The thermostats showed 21; the remembered room value wins.
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 2400), (IEEE_B, 2400)]


async def test_restore_of_a_switched_off_room(hass, entry, fake_gateway) -> None:
    remember(hass, "off", 22.0)

    await start(hass, entry)

    assert hass.states.get(CLIMATE).state == "off"
    assert sorted(writes(fake_gateway, MODE)) == [(IEEE_A, OFF), (IEEE_B, OFF)]


async def test_restore_keeps_the_setpoint_to_return_to_from_frost(hass, entry) -> None:
    remember(hass, "heat", 5.0, preset="frost", frost_return=22.0)

    await start(hass, entry)

    state = hass.states.get(CLIMATE)
    assert state.attributes[ATTR_PRESET_MODE] == "frost"
    assert state.attributes[ATTR_TEMPERATURE] == 5.0
    assert "frost_return" not in state.attributes

    await call(hass, SERVICE_SET_PRESET_MODE, preset_mode="none")

    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 22.0


async def test_the_extra_data_carries_the_frost_return(hass, entry) -> None:
    await start(hass, entry)
    await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 23.0})
    await call(hass, SERVICE_SET_PRESET_MODE, preset_mode="frost")

    stored = {
        stored.state.entity_id: stored.extra_data
        for stored in async_restore_data(hass).async_get_stored_states()
    }
    assert stored[CLIMATE].as_dict() == {"frost_return": 23.0}


@pytest.mark.parametrize(
    "restored",
    [
        State(CLIMATE, "unavailable", {}),
        State(CLIMATE, "unknown", {}),
        State(CLIMATE, "cool", {ATTR_TEMPERATURE: "warm", ATTR_PRESET_MODE: "eco"}),
    ],
)
async def test_unusable_restored_state_is_ignored(hass, entry, restored) -> None:
    mock_restore_cache_with_extra_data(hass, [(restored, {"frost_return": "x"})])

    await start(hass, entry)

    state = hass.states.get(CLIMATE)
    assert state.state == "heat"
    assert state.attributes[ATTR_TEMPERATURE] == 21.0
    assert state.attributes["setpoint_source"] == "initial"


async def test_no_remembered_state_adopts_the_thermostats(hass, entry) -> None:
    await start(hass, entry)

    assert hass.states.get(CLIMATE).attributes["setpoint_source"] == "initial"


async def test_restore_during_window_hold(hass, fake_gateway) -> None:
    hass.states.async_set("binary_sensor.window", "on")
    entry = make_entry(
        hass, [room_data(window_contacts=["binary_sensor.window"])], FAST_OPTIONS
    )
    remember(hass, "heat", 21.0)

    await start(hass, entry)

    # The window is open when Home Assistant comes back: the room is held off,
    # whatever it remembered, and the thermostats never get heat.
    assert hass.states.get(CLIMATE).attributes["window_open"] is True
    assert sorted(writes(fake_gateway, MODE)[-2:]) == [(IEEE_A, OFF), (IEEE_B, OFF)]
    assert (IEEE_A, HEAT) not in writes(fake_gateway, MODE)
    assert (IEEE_B, HEAT) not in writes(fake_gateway, MODE)

    # Closing the window brings back what was remembered.
    hass.states.async_set("binary_sensor.window", "off")
    await settle(hass)

    state = hass.states.get(CLIMATE)
    assert state.state == "heat"
    assert state.attributes[ATTR_TEMPERATURE] == 21.0
    assert state.attributes["window_open"] is False
    assert sorted(writes(fake_gateway, MODE)[-2:]) == [(IEEE_A, HEAT), (IEEE_B, HEAT)]


async def test_restore_with_the_window_closed(hass, fake_gateway) -> None:
    hass.states.async_set("binary_sensor.window", "off")
    entry = make_entry(
        hass, [room_data(window_contacts=["binary_sensor.window"])], FAST_OPTIONS
    )
    remember(hass, "heat", 24.0)

    await start(hass, entry)

    state = hass.states.get(CLIMATE)
    assert state.state == "heat"
    assert state.attributes[ATTR_TEMPERATURE] == 24.0
    assert state.attributes["window_open"] is False
    assert (IEEE_A, OFF) not in writes(fake_gateway, MODE)


# --- Which rooms get a climate entity ------------------------------------------


def profile_without(tmp_path, intent: str) -> str:
    """Write a user profile that lacks the capability of an intent; return its id."""
    lines = BUILTIN_TEXT.splitlines(keepends=True)
    block = next(i for i, line in enumerate(lines) if f"intent: {intent}" in line) - 1
    end = block + 1
    while end < len(lines) and lines[end].startswith("    "):
        end += 1
    text = "".join(lines[:block] + lines[end:])
    (use_config_dir_path(tmp_path) / "less.yaml").write_text(
        text.replace("id: sonoff_trvzb_native", "id: user_less", 1)
    )
    return "user_less"


def use_config_dir_path(tmp_path):
    folder = tmp_path / "easy_trv" / "profiles"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@pytest.mark.parametrize("intent", ["set_setpoint", "set_hvac_mode"])
async def test_no_climate_entity_without_setpoint_capability(
    hass, tmp_path, intent
) -> None:
    hass.config.config_dir = str(tmp_path)
    profile = profile_without(tmp_path, intent)
    entry = make_entry(hass, [room_data(profile=profile)], FAST_OPTIONS)

    await start(hass, entry)

    assert hass.states.get(CLIMATE) is None
    assert hass.states.get("sensor.living_heating_status").state == "ok"
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert [e.domain for e in entities] == ["sensor"]
    controller = next(iter(entry.runtime_data.rooms.values()))
    assert controller.effective_range is None


async def test_each_room_has_its_own_climate_entity(hass, fake_gateway) -> None:
    fake_gateway.add_device("climate.c", "00:33", firmware=FIRMWARE)
    hass.states.async_set("climate.c", "heat", {"temperature": 19.0})
    entry = make_entry(
        hass,
        [
            room_data("Living"),
            room_data("Kitchen", trvs=["climate.c"], temp_sensors=["sensor.t2"]),
        ],
        FAST_OPTIONS,
    )

    await start(hass, entry)

    assert hass.states.get("climate.living_heating") is not None
    assert hass.states.get("climate.kitchen_heating") is not None
    kitchen = hass.states.get("climate.kitchen_heating")
    assert kitchen.attributes[ATTR_TEMPERATURE] == 19.0
