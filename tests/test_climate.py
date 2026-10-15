import pytest
from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_HVAC_MODES,
    ATTR_MAX_TEMP,
    ATTR_MIN_TEMP,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    ATTR_TARGET_TEMP_STEP,
    SERVICE_SET_HVAC_MODE,
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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import async_get as async_restore_data
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.easy_trv.core.model import Note, OperatingMode
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
    # The thermostats show no limits: the range of the profile applies.
    assert attributes[ATTR_MIN_TEMP] == 4.0
    assert attributes[ATTR_MAX_TEMP] == 35.0
    assert attributes[ATTR_TARGET_TEMP_STEP] == 0.5
    # ``auto`` carries frost protection (Home Assistant has no mode for it).
    assert attributes[ATTR_HVAC_MODES] == [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]
    # Frost protection is a mode of the room, not a preset.
    assert ATTR_PRESET_MODES not in attributes
    assert ATTR_PRESET_MODE not in attributes
    assert attributes[ATTR_SUPPORTED_FEATURES] == (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
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
    state_sensor = er.async_get(hass).async_get("sensor.living_heating_state")
    assert state_sensor is not None
    assert registry_entry.device_id == state_sensor.device_id
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


async def test_set_temperature_is_rounded_to_the_step(hass, entry) -> None:
    await start(hass, entry)

    await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 21.7})
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 21.5


async def test_set_setpoint_is_clamped_into_the_range(hass, entry) -> None:
    await start(hass, entry)
    controller = next(iter(entry.runtime_data.rooms.values()))

    await controller.async_set_setpoint(40.0)
    await settle(hass)
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 35.0

    await controller.async_set_setpoint(1.0)
    await settle(hass)
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 4.0


async def test_the_range_follows_the_limits_the_thermostats_show(hass) -> None:
    hass.states.async_set("climate.a", "heat", {"min_temp": 6.0, "max_temp": 30.0})
    hass.states.async_set("climate.b", "heat", {"min_temp": 8.0, "max_temp": 26.0})
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)

    await start(hass, entry)

    # The narrowest range that every thermostat accepts.
    attributes = hass.states.get(CLIMATE).attributes
    assert (attributes[ATTR_MIN_TEMP], attributes[ATTR_MAX_TEMP]) == (8.0, 26.0)


async def test_a_manual_range_ignores_the_thermostats(hass) -> None:
    hass.states.async_set("climate.a", "heat", {"min_temp": 8.0, "max_temp": 26.0})
    options = {**FAST_OPTIONS, "setpoint_range": "manual", "setpoint_max": 24.0}
    entry = make_entry(hass, [room_data()], options)

    await start(hass, entry)

    attributes = hass.states.get(CLIMATE).attributes
    assert (attributes[ATTR_MIN_TEMP], attributes[ATTR_MAX_TEMP]) == (5.0, 24.0)


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


async def test_off_ends_frost_protection(hass, entry) -> None:
    await start(hass, entry)
    controller = next(iter(entry.runtime_data.rooms.values()))
    await controller.async_set_operation(OperatingMode.FROST)
    await settle(hass)
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 5.0

    await call(hass, SERVICE_TURN_OFF)
    await call(hass, SERVICE_TURN_ON)

    state = hass.states.get(CLIMATE)
    assert state.state == "heat"
    assert state.attributes[ATTR_TEMPERATURE] == 21.0
    assert controller.operation is OperatingMode.HEAT


async def test_auto_is_frost_protection_and_heat_returns_to_the_setpoint(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)
    controller = next(iter(entry.runtime_data.rooms.values()))
    fake_gateway.writes.clear()

    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.AUTO)

    state = hass.states.get(CLIMATE)
    assert state.state == "auto"
    assert state.attributes[ATTR_TEMPERATURE] == 5.0
    assert state.attributes["setpoint_source"] == "frost"
    assert controller.operation is OperatingMode.FROST
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 500), (IEEE_B, 500)]
    assert writes(fake_gateway, MODE) == []

    fake_gateway.writes.clear()
    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.HEAT)

    state = hass.states.get(CLIMATE)
    assert state.state == "heat"
    assert state.attributes[ATTR_TEMPERATURE] == 21.0
    assert controller.operation is OperatingMode.HEAT
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 2100), (IEEE_B, 2100)]


async def test_auto_from_off_switches_the_room_on_at_frost_protection(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)
    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.OFF)
    fake_gateway.writes.clear()

    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.AUTO)

    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("auto", 5.0)
    assert sorted(writes(fake_gateway, MODE)) == [(IEEE_A, HEAT), (IEEE_B, HEAT)]
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 500), (IEEE_B, 500)]


async def test_off_from_frost_protection_and_back_to_heat(hass, entry) -> None:
    await start(hass, entry)
    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.AUTO)

    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.OFF)
    assert hass.states.get(CLIMATE).state == "off"

    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.HEAT)
    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("heat", 21.0)


async def test_turn_on_leaves_frost_protection(hass, entry) -> None:
    await start(hass, entry)
    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.AUTO)

    await call(hass, SERVICE_TURN_ON)

    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("heat", 21.0)


async def test_a_new_target_in_frost_protection_switches_to_heat(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)
    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.AUTO)
    fake_gateway.writes.clear()

    await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 19.5})

    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("heat", 19.5)
    assert state.attributes["setpoint_source"] == "ha"
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 1950), (IEEE_B, 1950)]

    # Frost protection forgot the target from before: heat stays at 19.5.
    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.AUTO)
    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.HEAT)
    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 19.5


async def test_set_temperature_with_the_mode_auto_keeps_the_target_to_return_to(
    hass, entry
) -> None:
    await start(hass, entry)

    await call(
        hass,
        SERVICE_SET_TEMPERATURE,
        **{ATTR_TEMPERATURE: 18.0, ATTR_HVAC_MODE: HVACMode.AUTO},
    )

    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("auto", 5.0)

    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.HEAT)

    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 18.0


async def test_set_temperature_with_the_mode_off_switches_off_at_the_target(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)

    await call(
        hass,
        SERVICE_SET_TEMPERATURE,
        **{ATTR_TEMPERATURE: 20.0, ATTR_HVAC_MODE: HVACMode.OFF},
    )

    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("off", 20.0)
    assert sorted(writes(fake_gateway, MODE)[-2:]) == [(IEEE_A, OFF), (IEEE_B, OFF)]


async def test_set_temperature_with_the_mode_heat_leaves_frost_protection(
    hass, entry
) -> None:
    await start(hass, entry)
    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.AUTO)

    await call(
        hass,
        SERVICE_SET_TEMPERATURE,
        **{ATTR_TEMPERATURE: 22.0, ATTR_HVAC_MODE: HVACMode.HEAT},
    )

    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("heat", 22.0)


async def test_set_temperature_with_an_unsupported_mode_changes_nothing(
    hass, entry
) -> None:
    await start(hass, entry)

    with pytest.raises(ServiceValidationError):
        await call(
            hass,
            SERVICE_SET_TEMPERATURE,
            **{ATTR_TEMPERATURE: 18.0, ATTR_HVAC_MODE: HVACMode.COOL},
        )

    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("heat", 21.0)


async def test_auto_during_a_window_hold_waits_for_the_window(
    hass, fake_gateway
) -> None:
    thermostats_report_off(hass)
    hass.states.async_set("binary_sensor.window", "on")
    entry = make_entry(
        hass, [room_data(window_contacts=["binary_sensor.window"])], FAST_OPTIONS
    )
    await start(hass, entry)
    fake_gateway.writes.clear()

    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.AUTO)

    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("auto", 5.0)
    assert state.attributes["window_open"] is True
    assert writes(fake_gateway, MODE) == []
    assert writes(fake_gateway, SETPOINT) == []

    hass.states.async_set("binary_sensor.window", "off")
    await settle(hass)

    state = hass.states.get(CLIMATE)
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("auto", 5.0)
    assert sorted(writes(fake_gateway, MODE)[-2:]) == [(IEEE_A, HEAT), (IEEE_B, HEAT)]
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 500), (IEEE_B, 500)]


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


def remember(  # noqa: PLR0913
    hass,
    state: str = "heat",
    temperature: float = 24.0,
    preset: str | None = "none",
    frost_return: float | None = None,
    legacy_preset: str | None = None,
) -> None:
    """Remember a room state; ``legacy_preset`` is the 0.3.0 preset attribute."""
    attributes: dict = {ATTR_TEMPERATURE: temperature}
    if legacy_preset is not None:
        attributes[ATTR_PRESET_MODE] = legacy_preset
    extra: dict = {"frost_return": frost_return}
    if preset is not None:
        extra["preset"] = preset
    mock_restore_cache_with_extra_data(
        hass, [(State(CLIMATE, state, attributes), extra)]
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


@pytest.mark.parametrize(
    ("preset", "legacy_preset"),
    [("frost", None), (None, "frost")],
    ids=["stored", "remembered by 0.3.0"],
)
async def test_restore_keeps_frost_protection_and_the_setpoint_to_return_to(
    hass, entry, preset, legacy_preset
) -> None:
    remember(
        hass, "heat", 5.0, preset=preset, frost_return=22.0, legacy_preset=legacy_preset
    )

    await start(hass, entry)

    controller = next(iter(entry.runtime_data.rooms.values()))
    state = hass.states.get(CLIMATE)
    assert controller.operation is OperatingMode.FROST
    assert state.attributes[ATTR_TEMPERATURE] == 5.0
    assert "frost_return" not in state.attributes

    await controller.async_set_operation(OperatingMode.HEAT)
    await settle(hass)

    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 22.0


@pytest.mark.parametrize("preset", ["frost", "none", None])
async def test_a_restored_auto_state_is_frost_protection(hass, entry, preset) -> None:
    remember(hass, "auto", 5.0, preset=preset, frost_return=22.0)

    await start(hass, entry)

    controller = next(iter(entry.runtime_data.rooms.values()))
    state = hass.states.get(CLIMATE)
    assert controller.operation is OperatingMode.FROST
    assert (state.state, state.attributes[ATTR_TEMPERATURE]) == ("auto", 5.0)

    await call(hass, SERVICE_SET_HVAC_MODE, hvac_mode=HVACMode.HEAT)

    assert hass.states.get(CLIMATE).attributes[ATTR_TEMPERATURE] == 22.0


async def test_the_extra_data_carries_frost_protection(hass, entry) -> None:
    await start(hass, entry)
    await call(hass, SERVICE_SET_TEMPERATURE, **{ATTR_TEMPERATURE: 23.0})
    controller = next(iter(entry.runtime_data.rooms.values()))
    await controller.async_set_operation(OperatingMode.FROST)
    await settle(hass)

    stored = {
        stored.state.entity_id: stored.extra_data
        for stored in async_restore_data(hass).async_get_stored_states()
    }
    assert stored[CLIMATE].as_dict() == {"frost_return": 23.0, "preset": "frost"}


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
    assert hass.states.get("sensor.living_heating_state").state == "heating"
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    # The room keeps its sensor, only the thermostat entity is missing.
    assert [e.entity_id for e in entities] == ["sensor.living_heating_state"]
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


# --- A restart during a hold --------------------------------------------------


def thermostats_report_off(hass) -> None:
    for entity_id in ("climate.a", "climate.b"):
        hass.states.async_set(entity_id, "off", {"temperature": 18.0})


async def test_restart_during_window_hold_writes_no_heat(hass, fake_gateway) -> None:
    thermostats_report_off(hass)
    hass.states.async_set("binary_sensor.window", "on")
    entry = make_entry(
        hass, [room_data(window_contacts=["binary_sensor.window"])], FAST_OPTIONS
    )
    remember(hass, "heat", 21.0)

    await start(hass, entry)

    # The hold is in force from the first moment: the thermostats are off
    # already, so nothing is written, no heat and no setpoint.
    assert writes(fake_gateway, MODE) == []
    assert writes(fake_gateway, SETPOINT) == []
    state = hass.states.get(CLIMATE)
    # The room still wishes to heat at 21.
    assert state.state == "heat"
    assert state.attributes[ATTR_TEMPERATURE] == 21.0
    assert state.attributes["window_open"] is True

    hass.states.async_set("binary_sensor.window", "off")
    await settle(hass)

    assert sorted(writes(fake_gateway, MODE)[-2:]) == [(IEEE_A, HEAT), (IEEE_B, HEAT)]
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 2100), (IEEE_B, 2100)]
    assert hass.states.get(CLIMATE).attributes["window_open"] is False


async def test_restart_during_outdoor_hold_writes_no_heat(hass, fake_gateway) -> None:
    thermostats_report_off(hass)
    hass.states.async_set("sensor.outdoor", "25", {"unit_of_measurement": "°C"})
    entry = make_entry(
        hass,
        [room_data()],
        {
            **FAST_OPTIONS,
            "outdoor_sensor": "sensor.outdoor",
            "outdoor_cutoff_celsius": 18.0,
        },
    )
    remember(hass, "heat", 21.0)

    await start(hass, entry)

    assert writes(fake_gateway, MODE) == []
    assert writes(fake_gateway, SETPOINT) == []
    state = hass.states.get(CLIMATE)
    assert state.state == "heat"
    assert state.attributes["outdoor_cutoff"] is True

    hass.states.async_set("sensor.outdoor", "10", {"unit_of_measurement": "°C"})
    await settle(hass)

    assert sorted(writes(fake_gateway, MODE)[-2:]) == [(IEEE_A, HEAT), (IEEE_B, HEAT)]
    assert sorted(writes(fake_gateway, SETPOINT)) == [(IEEE_A, 2100), (IEEE_B, 2100)]


async def test_restart_of_an_off_room_during_a_window_hold(hass, fake_gateway) -> None:
    # The thermostats were left heating: they must go off, once, and stay off.
    hass.states.async_set("binary_sensor.window", "on")
    entry = make_entry(
        hass, [room_data(window_contacts=["binary_sensor.window"])], FAST_OPTIONS
    )
    remember(hass, "off", 21.0)

    await start(hass, entry)

    assert sorted(writes(fake_gateway, MODE)) == [(IEEE_A, OFF), (IEEE_B, OFF)]
    assert writes(fake_gateway, SETPOINT) == []
    assert hass.states.get(CLIMATE).state == "off"

    fake_gateway.writes.clear()
    hass.states.async_set("binary_sensor.window", "off")
    await settle(hass)

    assert fake_gateway.writes == []
    assert hass.states.get(CLIMATE).state == "off"


async def test_restart_of_an_off_room_during_an_outdoor_hold(
    hass, fake_gateway
) -> None:
    hass.states.async_set("sensor.outdoor", "25", {"unit_of_measurement": "°C"})
    entry = make_entry(
        hass,
        [room_data()],
        {
            **FAST_OPTIONS,
            "outdoor_sensor": "sensor.outdoor",
            "outdoor_cutoff_celsius": 18.0,
        },
    )
    remember(hass, "off", 21.0)

    await start(hass, entry)

    assert sorted(writes(fake_gateway, MODE)) == [(IEEE_A, OFF), (IEEE_B, OFF)]
    assert writes(fake_gateway, SETPOINT) == []

    fake_gateway.writes.clear()
    hass.states.async_set("sensor.outdoor", "10", {"unit_of_measurement": "°C"})
    await settle(hass)

    assert fake_gateway.writes == []


async def test_restart_during_a_hold_writes_nothing_to_thermostats_already_off(
    hass, fake_gateway
) -> None:
    thermostats_report_off(hass)
    hass.states.async_set("binary_sensor.window", "on")
    entry = make_entry(
        hass, [room_data(window_contacts=["binary_sensor.window"])], FAST_OPTIONS
    )

    await start(hass, entry)

    assert writes(fake_gateway, MODE) == []
    assert writes(fake_gateway, SETPOINT) == []
