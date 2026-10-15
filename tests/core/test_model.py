from datetime import timedelta

from custom_components.easy_trv.core.model import (
    INTENT_NAMES,
    SETTING_INTENTS,
    RoomParams,
    SetAdaptiveMode,
    SetFrostTemperature,
    SetHvacMode,
    SetHysteresis,
    SetSetpoint,
    SetWindowDetection,
)
from custom_components.easy_trv.core.settings import resolve_settings


def params(options: dict[str, object]) -> RoomParams:
    return RoomParams.from_settings(resolve_settings(options, {}))


def test_defaults_are_derived_from_the_settings_module() -> None:
    assert RoomParams.defaults() == params({})


def test_the_default_numbers_are_the_documented_ones() -> None:
    defaults = RoomParams.defaults()
    assert defaults.refresh == timedelta(minutes=20)
    assert defaults.verify_delay == 10.0
    assert defaults.max_attempts == 3
    assert defaults.min_delta == 0.1
    assert defaults.sensor_timeout == timedelta(minutes=120)


def test_settings_are_read_and_converted() -> None:
    # A NumberSelector delivers floats, also for whole numbers.
    result = params(
        {
            "refresh_minutes": 30.0,
            "verify_delay_seconds": 5.0,
            "max_attempts": 4.0,
            "min_delta_kelvin": 0.3,
            "sensor_timeout_minutes": 60.0,
        }
    )
    assert result.refresh == timedelta(minutes=30)
    assert result.verify_delay == 5.0
    assert result.max_attempts == 4
    assert isinstance(result.max_attempts, int)
    assert result.min_delta == 0.3
    assert result.sensor_timeout == timedelta(minutes=60)


def test_sensor_timeout_zero_switches_the_timeout_off() -> None:
    assert params({"sensor_timeout_minutes": 0}).sensor_timeout is None
    assert params({"sensor_timeout_minutes": 0.0}).sensor_timeout is None


def test_missing_and_unknown_settings_fall_back_to_defaults() -> None:
    result = params({"refresh_minutes": 45, "unrelated": "x"})
    assert result.refresh == timedelta(minutes=45)
    assert result.verify_delay == RoomParams.defaults().verify_delay
    assert result.sensor_timeout == timedelta(minutes=120)


def test_timing_constants_stay_at_their_defaults() -> None:
    result = params({})
    assert result.debounce == 5.0
    assert result.backoff == (5.0, 15.0, 45.0)


def test_the_new_intents_have_stable_names() -> None:
    assert INTENT_NAMES[SetAdaptiveMode] == "set_adaptive_mode"
    assert INTENT_NAMES[SetFrostTemperature] == "set_frost_temperature"


def test_setting_intents_are_in_write_order() -> None:
    assert (
        SetWindowDetection,
        SetAdaptiveMode,
        SetHysteresis,
        SetFrostTemperature,
    ) == SETTING_INTENTS


def test_room_state_intents_have_stable_names_and_are_not_settings() -> None:
    assert INTENT_NAMES[SetSetpoint] == "set_setpoint"
    assert INTENT_NAMES[SetHvacMode] == "set_hvac_mode"
    assert SetSetpoint not in SETTING_INTENTS
    assert SetHvacMode not in SETTING_INTENTS
