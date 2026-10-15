from datetime import UTC, datetime, timedelta
from typing import get_args

from custom_components.easy_trv.core.model import (
    INTENT_NAMES,
    SETTING_INTENTS,
    Command,
    Event,
    KnobSettled,
    Mismatch,
    Note,
    OutdoorChanged,
    Preset,
    RestoreRoom,
    RoomConfig,
    RoomMode,
    RoomParams,
    RoomState,
    ScheduleKnobSettle,
    SetAdaptiveMode,
    SetFrostTemperature,
    SetHvacMode,
    SetHysteresis,
    SetRoomMode,
    SetRoomPreset,
    SetRoomSetpoint,
    SetSetpoint,
    SetWindowDetection,
    Start,
    TrvConfig,
    TrvObserved,
    TrvState,
    WindowChanged,
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


def config() -> RoomConfig:
    return RoomConfig(
        "r", "Room", (TrvConfig("climate.a", "00:11", None),), (), "on", 0.4, "on", 7.0
    )


def test_the_new_enums_have_stable_values() -> None:
    assert (RoomMode.HEAT, RoomMode.OFF) == ("heat", "off")
    assert (Preset.NONE, Preset.FROST) == ("none", "frost")
    assert (Note.WINDOW_PHASE, Note.SCHEDULE) == ("window_phase", "schedule")


def test_room_config_new_fields_default_to_off() -> None:
    cfg = config()
    assert cfg.setpoint_range is None
    assert cfg.window_contacts == ()
    assert cfg.window_warn is None
    assert cfg.window_check is None
    assert cfg.window_check_drop == 0.3
    assert cfg.outdoor_cutoff is None


def test_initial_room_state_is_heating_without_a_hold() -> None:
    state = RoomState.initial(config())
    assert state.mode is RoomMode.HEAT
    assert state.preset is Preset.NONE
    assert state.setpoint is None
    assert state.frost_return is None
    assert state.setpoint_source is None
    assert state.window_open is False
    assert state.window_opened_at is None
    assert state.window_open_value is None
    assert state.outdoor_hold is False
    assert state.window_warned is False
    assert state.window_suspect is False
    assert state.window_checked is False


def test_trv_state_new_fields_are_empty_by_default() -> None:
    trv = TrvState()
    assert trv.reported_setpoint is None
    assert trv.reported_mode is None
    assert trv.note is None
    assert trv.note_since is None
    assert trv.written_recently == {}
    assert trv.knob_pending == {}


def test_trv_state_defaults_are_not_shared_between_instances() -> None:
    assert TrvState().written_recently is not TrvState().written_recently
    assert TrvState().knob_pending is not TrvState().knob_pending


def test_start_reported_defaults_to_empty() -> None:
    start = Start(sensors=(), trv_available={})
    assert start.reported == {}
    assert Start((), {}, {"climate.a": (21.0, "heat")}).reported["climate.a"] == (
        21.0,
        "heat",
    )


def test_mismatch_observed_defaults_to_none() -> None:
    assert Mismatch(frozenset({1}), 2).observed is None
    assert Mismatch(frozenset({1}), 2, 21.5).observed == 21.5


def test_the_new_events_are_constructible() -> None:
    assert SetRoomSetpoint(21.0).celsius == 21.0
    assert SetRoomMode(RoomMode.OFF).mode is RoomMode.OFF
    assert SetRoomPreset(Preset.FROST).preset is Preset.FROST
    restore = RestoreRoom(20.0, RoomMode.HEAT, Preset.NONE)
    assert (restore.setpoint, restore.mode, restore.preset) == (
        20.0,
        RoomMode.HEAT,
        Preset.NONE,
    )
    observed = TrvObserved("climate.a", "setpoint", 21.5, 2150, "knob")
    assert (observed.trv, observed.capability, observed.value) == (
        "climate.a",
        "setpoint",
        21.5,
    )
    assert (observed.raw, observed.kind) == (2150, "knob")
    assert KnobSettled("climate.a", "setpoint").capability == "setpoint"
    assert WindowChanged(open=True).open is True
    assert OutdoorChanged(None).celsius is None


def test_schedule_knob_settle_is_constructible() -> None:
    command = ScheduleKnobSettle("climate.a", "setpoint", 3.0)
    assert (command.trv, command.capability, command.seconds) == (
        "climate.a",
        "setpoint",
        3.0,
    )


def test_the_unions_contain_the_new_types() -> None:
    events = set(get_args(Event))
    commands = set(get_args(Command))
    for cls in (
        SetRoomSetpoint,
        SetRoomMode,
        SetRoomPreset,
        RestoreRoom,
        TrvObserved,
        KnobSettled,
        WindowChanged,
        OutdoorChanged,
    ):
        assert cls in events
    assert ScheduleKnobSettle in commands


def test_a_trv_state_can_hold_a_recent_write() -> None:
    at = datetime(2026, 10, 15, tzinfo=UTC)
    trv = TrvState(written_recently={"setpoint": ((21.0, at),)})
    assert trv.written_recently["setpoint"] == ((21.0, at),)
