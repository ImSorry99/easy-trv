from dataclasses import replace
from datetime import timedelta

from custom_components.easy_trv.core.model import (
    DebounceElapsed,
    Execute,
    Failed,
    Note,
    Preset,
    RestoreRoom,
    RetryDue,
    RoomMode,
    RoomState,
    SensorChanged,
    SetHvacMode,
    SetRoomMode,
    SetRoomPreset,
    SetRoomSetpoint,
    SetRoomTemperature,
    SetSetpoint,
    SetSource,
    Signal,
    Source,
    Start,
    Tick,
    TrvAvailabilityChanged,
)
from custom_components.easy_trv.core.room_logic import RoomLogic
from custom_components.easy_trv.core.setpoint import clamp
from tests.core.test_room_logic import (
    BOTH_AVAILABLE,
    CFG,
    T0,
    capability_of,
    executes,
    logic,
    of_type,
    reading,
    result,
    started,
    with_trv,
)

RANGE = (5.0, 30.0, 0.5)
LATER = T0 + timedelta(hours=1)


def writes(cmds: list) -> list:
    """Return the setpoint and mode writes among ``cmds``."""
    return [
        (trv, intent)
        for trv, intent in executes(cmds)
        if isinstance(intent, SetSetpoint | SetHvacMode)
    ]


def started_with_setpoint(celsius: float = 21.0) -> RoomState:
    st, _ = started()
    st, _ = logic().handle(SetRoomSetpoint(celsius), T0, st)
    return st


# --- clamp ---------------------------------------------------------------


def test_clamp_rounds_half_up_and_limits() -> None:
    assert clamp(21.25, RANGE) == 21.5
    assert clamp(21.24, RANGE) == 21.0
    assert clamp(2.0, RANGE) == 5.0
    assert clamp(99.0, RANGE) == 30.0


# --- HA writes -----------------------------------------------------------


def test_ha_setpoint_is_clamped_and_written_to_every_thermostat() -> None:
    st, _ = started()
    st, cmds = logic().handle(SetRoomSetpoint(40.0), T0, st)
    assert st.setpoint == 30.0
    assert st.setpoint_source == "ha"
    assert executes(cmds) == [
        ("climate.a", SetSetpoint(30.0)),
        ("climate.b", SetSetpoint(30.0)),
    ]
    assert st.trvs["climate.a"].written_recently["setpoint"] == ((30.0, T0),)
    assert st.trvs["climate.b"].written_recently["setpoint"] == ((30.0, T0),)


def test_setpoint_rounds_to_the_step() -> None:
    st, _ = started()
    st, cmds = logic().handle(SetRoomSetpoint(21.3), T0, st)
    assert st.setpoint == 21.5
    assert executes(cmds)[0] == ("climate.a", SetSetpoint(21.5))


def test_setpoint_skips_unavailable_thermostats() -> None:
    st, _ = started()
    st = with_trv(st, "climate.b", available=False)
    _, cmds = logic().handle(SetRoomSetpoint(22.0), T0, st)
    assert executes(cmds) == [("climate.a", SetSetpoint(22.0))]


def test_ha_setpoint_leaves_the_frost_preset() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomPreset(Preset.FROST), T0, st)
    st, _ = logic().handle(SetRoomSetpoint(19.0), T0, st)
    assert st.preset is Preset.NONE
    assert st.setpoint == 19.0
    assert st.frost_return is None


def test_written_recently_keeps_two_minutes() -> None:
    st, _ = started()
    st, _ = logic().handle(SetRoomSetpoint(21.0), T0, st)
    st, _ = logic().handle(SetRoomSetpoint(22.0), T0 + timedelta(seconds=100), st)
    assert st.trvs["climate.a"].written_recently["setpoint"] == (
        (21.0, T0),
        (22.0, T0 + timedelta(seconds=100)),
    )
    late = T0 + timedelta(seconds=300)
    st, _ = logic().handle(SetRoomSetpoint(23.0), late, st)
    assert st.trvs["climate.a"].written_recently["setpoint"] == ((23.0, late),)


def test_setpoint_during_a_hold_only_updates_the_state() -> None:
    st, _ = started()
    st = replace(st, window_open=True)
    st, cmds = logic().handle(SetRoomSetpoint(22.0), T0, st)
    assert st.setpoint == 22.0
    assert writes(cmds) == []
    st = replace(st, window_open=False, outdoor_hold=True)
    st, cmds = logic().handle(SetRoomSetpoint(23.0), T0, st)
    assert st.setpoint == 23.0
    assert writes(cmds) == []


# --- mode ----------------------------------------------------------------


def test_mode_off_writes_off_and_stops_value_writes() -> None:
    st, _ = started()
    st, cmds = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    assert st.mode is RoomMode.OFF
    assert executes(cmds) == [
        ("climate.a", SetHvacMode("off")),
        ("climate.b", SetHvacMode("off")),
    ]
    assert st.trvs["climate.a"].written_recently["hvac_mode"] == (("off", T0),)

    refresh = logic()._params.refresh  # noqa: SLF001
    _, tick = logic().handle(Tick(), T0 + refresh + timedelta(seconds=1), st)
    assert of_type(tick, Execute) == []


def test_mode_off_suppresses_debounced_writes_and_setpoint_writes() -> None:
    st, _ = started()
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st, cmds = logic().handle(
        SensorChanged(reading("sensor.t1", 23.0, LATER)), LATER, st
    )
    assert executes(cmds) == []
    st, cmds = logic().handle(DebounceElapsed(), LATER, st)
    assert executes(cmds) == []
    assert not st.debounce_pending
    st, cmds = logic().handle(SetRoomSetpoint(22.0), LATER, st)
    assert st.setpoint == 22.0
    assert executes(cmds) == []


def test_mode_off_suppresses_the_failsafe_reenable() -> None:
    st, _ = started()
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    _, cmds = logic().handle(
        result("climate.a", SetRoomTemperature(21.3), Signal("failsafe")), LATER, st
    )
    assert executes(cmds) == []


def test_a_hold_suppresses_value_writes() -> None:
    st, _ = started()
    st = replace(st, window_open=True)
    refresh = logic()._params.refresh  # noqa: SLF001
    _, cmds = logic().handle(Tick(), T0 + refresh + timedelta(seconds=1), st)
    assert executes(cmds) == []


def test_mode_heat_resyncs_source_value_mode_and_setpoint() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st, cmds = logic().handle(SetRoomMode(RoomMode.HEAT), LATER, st)
    assert st.mode is RoomMode.HEAT
    assert executes(cmds) == [
        ("climate.a", SetHvacMode("heat")),
        ("climate.a", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.a", SetSetpoint(21.0)),
        ("climate.b", SetHvacMode("heat")),
        ("climate.b", SetSource("external")),
        ("climate.b", SetRoomTemperature(21.3)),
        ("climate.b", SetSetpoint(21.0)),
    ]
    assert st.last_value_issued == LATER
    assert st.trvs["climate.a"].written_recently["hvac_mode"][-1] == ("heat", LATER)


def test_mode_heat_without_a_known_setpoint_writes_no_setpoint() -> None:
    st, _ = started()
    st, cmds = logic().handle(SetRoomMode(RoomMode.HEAT), T0, st)
    assert [i for _, i in executes(cmds) if isinstance(i, SetSetpoint)] == []


def test_mode_during_a_hold_only_updates_the_state() -> None:
    st, _ = started()
    st = replace(st, outdoor_hold=True)
    st, cmds = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    assert st.mode is RoomMode.OFF
    assert executes(cmds) == []


# --- preset --------------------------------------------------------------


def test_frost_preset_remembers_and_restores_the_setpoint() -> None:
    st = started_with_setpoint(21.0)
    st, cmds = logic().handle(SetRoomPreset(Preset.FROST), T0, st)
    assert (st.setpoint, st.preset, st.frost_return) == (7.0, Preset.FROST, 21.0)
    assert st.setpoint_source == "frost"
    assert executes(cmds) == [
        ("climate.a", SetSetpoint(7.0)),
        ("climate.b", SetSetpoint(7.0)),
    ]
    st, cmds = logic().handle(SetRoomPreset(Preset.NONE), T0, st)
    assert (st.setpoint, st.preset, st.frost_return) == (21.0, Preset.NONE, None)
    assert executes(cmds) == [
        ("climate.a", SetSetpoint(21.0)),
        ("climate.b", SetSetpoint(21.0)),
    ]


def test_frost_twice_keeps_the_remembered_setpoint() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomPreset(Preset.FROST), T0, st)
    st, cmds = logic().handle(SetRoomPreset(Preset.FROST), T0, st)
    assert st.frost_return == 21.0
    assert executes(cmds) == []


def test_frost_temperature_is_clamped_into_the_range() -> None:
    cfg = replace(CFG, frost_temperature=4.0)
    room = RoomLogic(cfg, logic()._params, capability_of=capability_of)  # noqa: SLF001
    st, _ = room.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE), T0, RoomState.initial(cfg)
    )
    st, cmds = room.handle(SetRoomPreset(Preset.FROST), T0, st)
    assert st.setpoint == 5.0
    assert executes(cmds)[0] == ("climate.a", SetSetpoint(5.0))


def test_frost_while_off_only_updates_the_state() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st, cmds = logic().handle(SetRoomPreset(Preset.FROST), T0, st)
    assert st.setpoint == 7.0
    assert executes(cmds) == []


# --- restore and start ---------------------------------------------------


def test_restore_wins_and_writes_only_differing_thermostats() -> None:
    room = logic()
    st, _ = room.handle(
        RestoreRoom(22.0, RoomMode.HEAT, Preset.NONE), T0, RoomState.initial(CFG)
    )
    assert (st.setpoint, st.mode, st.setpoint_source) == (
        22.0,
        RoomMode.HEAT,
        "restore",
    )
    reported = {"climate.a": (22.0, "heat"), "climate.b": (19.0, "heat")}
    st, cmds = room.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, reported), T0, st
    )
    assert st.setpoint == 22.0
    assert writes(cmds) == [("climate.b", SetSetpoint(22.0))]
    assert st.trvs["climate.a"].reported_setpoint == 22.0
    assert st.trvs["climate.b"].reported_setpoint == 19.0


def test_restored_off_is_written_to_thermostats_still_heating() -> None:
    room = logic()
    st, _ = room.handle(
        RestoreRoom(22.0, RoomMode.OFF, Preset.NONE), T0, RoomState.initial(CFG)
    )
    reported = {"climate.a": (22.0, "off"), "climate.b": (22.0, "heat")}
    st, cmds = room.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, reported), T0, st
    )
    assert st.mode is RoomMode.OFF
    assert writes(cmds) == [("climate.b", SetHvacMode("off"))]
    assert not any(isinstance(i, SetRoomTemperature) for _, i in executes(cmds))


def test_restore_restores_the_preset() -> None:
    st, _ = logic().handle(
        RestoreRoom(7.0, RoomMode.HEAT, Preset.FROST), T0, RoomState.initial(CFG)
    )
    assert (st.setpoint, st.preset) == (7.0, Preset.FROST)


def test_restore_after_start_writes_only_known_differing_values() -> None:
    st, _ = started()
    st = with_trv(st, "climate.a", reported_setpoint=22.0, reported_mode="heat")
    st = with_trv(st, "climate.b", reported_setpoint=19.0, reported_mode="heat")
    st, cmds = logic().handle(RestoreRoom(22.0, RoomMode.HEAT, None), T0, st)
    assert writes(cmds) == [("climate.b", SetSetpoint(22.0))]


def test_initial_setpoint_comes_from_the_first_thermostat() -> None:
    reported = {"climate.a": (19.5, "heat"), "climate.b": (21.0, "heat")}
    st, cmds = logic().handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, reported),
        T0,
        RoomState.initial(CFG),
    )
    assert st.setpoint == 19.5
    assert st.setpoint_source == "initial"
    assert st.mode is RoomMode.HEAT
    assert writes(cmds) == [("climate.b", SetSetpoint(19.5))]


def test_initial_setpoint_is_clamped_and_written_back_too() -> None:
    reported = {"climate.a": (35.0, "heat"), "climate.b": (30.0, "heat")}
    st, cmds = logic().handle(
        Start((), BOTH_AVAILABLE, reported), T0, RoomState.initial(CFG)
    )
    assert st.setpoint == 30.0
    assert writes(cmds) == [("climate.a", SetSetpoint(30.0))]


def test_initial_mode_is_off_only_when_every_thermostat_is_off() -> None:
    reported = {"climate.a": (19.0, "off"), "climate.b": (19.0, "off")}
    st, cmds = logic().handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, reported),
        T0,
        RoomState.initial(CFG),
    )
    assert st.mode is RoomMode.OFF
    assert writes(cmds) == []
    assert not any(isinstance(i, SetRoomTemperature) for _, i in executes(cmds))
    mixed = {"climate.a": (19.0, "off"), "climate.b": (19.0, "heat")}
    st, _ = logic().handle(Start((), BOTH_AVAILABLE, mixed), T0, RoomState.initial(CFG))
    assert st.mode is RoomMode.HEAT


def test_initial_setpoint_skips_unavailable_and_unreported_thermostats() -> None:
    reported = {"climate.a": (None, None), "climate.b": (21.0, "heat")}
    st, _ = logic().handle(
        Start((), {"climate.a": True, "climate.b": True}, reported),
        T0,
        RoomState.initial(CFG),
    )
    assert st.setpoint == 21.0
    unavailable = {"climate.a": (18.0, "heat"), "climate.b": (21.0, "heat")}
    st, _ = logic().handle(
        Start((), {"climate.a": False, "climate.b": True}, unavailable),
        T0,
        RoomState.initial(CFG),
    )
    assert st.setpoint == 21.0


def test_no_reported_setpoint_leaves_the_setpoint_unset() -> None:
    st, cmds = logic().handle(
        Start((), BOTH_AVAILABLE, {"climate.a": (None, None)}),
        T0,
        RoomState.initial(CFG),
    )
    assert st.setpoint is None
    assert writes(cmds) == []


# --- schedule note and rooms without a climate ---------------------------


def test_schedule_thermostat_gets_no_setpoint_writes() -> None:
    st, _ = started()
    st = with_trv(st, "climate.a", note=Note.SCHEDULE)
    st, cmds = logic().handle(SetRoomSetpoint(22.0), T0, st)
    assert executes(cmds) == [("climate.b", SetSetpoint(22.0))]
    assert "setpoint" not in st.trvs["climate.a"].written_recently
    _, cmds = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    assert executes(cmds) == [("climate.b", SetHvacMode("off"))]


def test_schedule_thermostat_still_gets_the_source_on_heat() -> None:
    st = started_with_setpoint(21.0)
    st = with_trv(st, "climate.a", note=Note.SCHEDULE)
    st = replace(st, mode=RoomMode.OFF)
    _, cmds = logic().handle(SetRoomMode(RoomMode.HEAT), T0, st)
    assert [i for t, i in executes(cmds) if t == "climate.a"] == [
        SetSource("external"),
        SetRoomTemperature(21.3),
    ]


def test_room_without_climate_ignores_setpoint_events() -> None:
    cfg = replace(CFG, setpoint_range=None)
    room = RoomLogic(cfg, logic()._params, capability_of=capability_of)  # noqa: SLF001
    reported = {"climate.a": (19.0, "off"), "climate.b": (21.0, "off")}
    st, start_cmds = room.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, reported),
        T0,
        RoomState.initial(cfg),
    )
    assert st.setpoint is None
    assert st.mode is RoomMode.HEAT
    assert st.source is Source.EXTERNAL
    assert writes(start_cmds) == []
    for event in (
        SetRoomSetpoint(22.0),
        SetRoomMode(RoomMode.OFF),
        SetRoomPreset(Preset.FROST),
        RestoreRoom(22.0, RoomMode.OFF, Preset.FROST),
    ):
        after, cmds = room.handle(event, T0, st)
        assert after == st
        assert executes(cmds) == []


# --- suppression details -------------------------------------------------


def off_room() -> RoomState:
    st, _ = started()
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    return st


def test_a_pending_source_or_value_retry_is_dropped_while_off() -> None:
    st, _ = started()
    st, _ = logic().handle(
        result("climate.a", SetSource("external"), Failed("busy")), T0, st
    )
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    _, cmds = logic().handle(RetryDue("climate.a", "source"), T0, st)
    assert executes(cmds) == []


def test_a_failed_source_is_not_reissued_while_off() -> None:
    st, _ = started()
    for _ in range(3):
        st, _ = logic().handle(
            result("climate.a", SetSource("external"), Failed("busy")), T0, st
        )
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    late = T0 + timedelta(hours=2)
    _, cmds = logic().handle(Tick(), late, st)
    assert [i for _, i in executes(cmds) if isinstance(i, SetSource)] == []


def test_a_returning_thermostat_gets_only_off_while_off() -> None:
    st = off_room()
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    _, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), LATER, st
    )
    assert executes(cmds) == [("climate.a", SetHvacMode("off"))]


def test_heat_resyncs_the_internal_source_too() -> None:
    st, _ = logic().handle(
        Start((reading("sensor.t1", None),), BOTH_AVAILABLE), T0, RoomState.initial(CFG)
    )
    assert st.source is Source.INTERNAL
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    _, cmds = logic().handle(SetRoomMode(RoomMode.HEAT), T0, st)
    assert executes(cmds)[:2] == [
        ("climate.a", SetHvacMode("heat")),
        ("climate.a", SetSource("internal")),
    ]


def test_a_room_without_sensors_resyncs_only_mode_and_setpoint() -> None:
    cfg = replace(CFG, temp_sensors=())
    room = RoomLogic(cfg, logic()._params, capability_of=capability_of)  # noqa: SLF001
    st, _ = room.handle(Start((), BOTH_AVAILABLE), T0, RoomState.initial(cfg))
    st, _ = room.handle(SetRoomSetpoint(21.0), T0, st)
    _, cmds = room.handle(SetRoomMode(RoomMode.HEAT), T0, st)
    assert executes(cmds)[:2] == [
        ("climate.a", SetHvacMode("heat")),
        ("climate.a", SetSetpoint(21.0)),
    ]


def test_a_thermostat_on_its_schedule_is_left_alone_at_start() -> None:
    st, _ = logic().handle(
        RestoreRoom(22.0, RoomMode.HEAT, Preset.NONE), T0, RoomState.initial(CFG)
    )
    reported = {"climate.a": (19.0, "auto"), "climate.b": (19.0, "heat")}
    _, cmds = logic().handle(Start((), BOTH_AVAILABLE, reported), T0, st)
    assert writes(cmds) == [("climate.b", SetSetpoint(22.0))]


def test_a_capability_the_thermostat_lacks_is_not_written_or_remembered() -> None:
    st, _ = started()
    st = with_trv(st, "climate.a", unsupported=frozenset({"setpoint"}))
    st, cmds = logic().handle(SetRoomSetpoint(22.0), T0, st)
    assert executes(cmds) == [("climate.b", SetSetpoint(22.0))]
    assert "setpoint" not in st.trvs["climate.a"].written_recently


# --- fix round 1 ---------------------------------------------------------


def failed_setpoint(value: float = 21.0) -> RoomState:
    """Return a started room whose SetSetpoint write to climate.a failed once."""
    st, _ = started()
    st, _ = logic().handle(SetRoomSetpoint(value), T0, st)
    st, _ = logic().handle(
        result("climate.a", SetSetpoint(value), Failed("busy")), T0, st
    )
    assert "setpoint" in st.trvs["climate.a"].pending_retry
    return st


def test_a_setpoint_retry_sends_the_newest_setpoint_and_records_it() -> None:
    st = failed_setpoint(21.0)
    st = replace(st, setpoint=23.0)
    st, cmds = logic().handle(RetryDue("climate.a", "setpoint"), LATER, st)
    assert executes(cmds) == [("climate.a", SetSetpoint(23.0))]
    assert st.trvs["climate.a"].written_recently["setpoint"][-1] == (23.0, LATER)


def test_the_pending_setpoint_retry_holds_the_newest_setpoint() -> None:
    st = failed_setpoint(21.0)
    st = replace(st, setpoint=23.0)
    st, _ = logic().handle(
        result("climate.a", SetSetpoint(21.0), Failed("busy")), T0, st
    )
    assert st.trvs["climate.a"].pending_retry["setpoint"] == SetSetpoint(23.0)


def test_no_setpoint_retry_while_off_or_held() -> None:
    st = failed_setpoint(21.0)
    off, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    _, cmds = logic().handle(RetryDue("climate.a", "setpoint"), LATER, off)
    assert executes(cmds) == []
    held = replace(st, window_open=True)
    _, cmds = logic().handle(RetryDue("climate.a", "setpoint"), LATER, held)
    assert executes(cmds) == []


def test_a_mode_retry_sends_the_current_mode_and_records_it() -> None:
    st, _ = started()
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st, _ = logic().handle(
        result("climate.a", SetHvacMode("off"), Failed("busy")), T0, st
    )
    st, cmds = logic().handle(RetryDue("climate.a", "hvac_mode"), LATER, st)
    assert executes(cmds) == [("climate.a", SetHvacMode("off"))]
    assert st.trvs["climate.a"].written_recently["hvac_mode"][-1] == ("off", LATER)
    st, _ = logic().handle(
        result("climate.a", SetHvacMode("off"), Failed("busy")), LATER, st
    )
    held = replace(st, outdoor_hold=True)
    _, cmds = logic().handle(RetryDue("climate.a", "hvac_mode"), LATER, held)
    assert executes(cmds) == []


def test_initial_setpoint_ignores_a_thermostat_on_its_schedule() -> None:
    reported = {"climate.a": (17.0, "auto"), "climate.b": (21.0, "heat")}
    st, cmds = logic().handle(
        Start((), BOTH_AVAILABLE, reported), T0, RoomState.initial(CFG)
    )
    assert st.setpoint == 21.0
    assert writes(cmds) == []
    only_auto = {"climate.a": (17.0, "auto"), "climate.b": (None, None)}
    st, cmds = logic().handle(
        Start((), BOTH_AVAILABLE, only_auto), T0, RoomState.initial(CFG)
    )
    assert st.setpoint is None
    assert writes(cmds) == []


def test_restore_out_of_off_resyncs_mode_source_value_and_setpoint() -> None:
    reported = {"climate.a": (19.0, "off"), "climate.b": (19.0, "off")}
    st, _ = logic().handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, reported),
        T0,
        RoomState.initial(CFG),
    )
    assert st.mode is RoomMode.OFF
    st, cmds = logic().handle(RestoreRoom(22.0, RoomMode.HEAT, Preset.NONE), LATER, st)
    assert executes(cmds) == [
        ("climate.a", SetHvacMode("heat")),
        ("climate.a", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.a", SetSetpoint(22.0)),
        ("climate.b", SetHvacMode("heat")),
        ("climate.b", SetSource("external")),
        ("climate.b", SetRoomTemperature(21.3)),
        ("climate.b", SetSetpoint(22.0)),
    ]
    refresh = logic()._params.refresh  # noqa: SLF001
    _, tick = logic().handle(Tick(), LATER + refresh + timedelta(seconds=1), st)
    assert [i for _, i in executes(tick) if isinstance(i, SetRoomTemperature)]


def test_restore_remembers_the_setpoint_before_frost() -> None:
    st, _ = logic().handle(
        RestoreRoom(7.0, RoomMode.HEAT, Preset.FROST, frost_return=21.0),
        T0,
        RoomState.initial(CFG),
    )
    assert (st.preset, st.setpoint, st.frost_return) == (Preset.FROST, 7.0, 21.0)
    st, _ = logic().handle(SetRoomPreset(Preset.NONE), T0, st)
    assert st.setpoint == 21.0


def test_a_thermostat_reporting_auto_gets_no_setpoint_write() -> None:
    st, _ = started()
    st = with_trv(st, "climate.a", reported_mode="auto")
    _, cmds = logic().handle(SetRoomSetpoint(22.0), T0, st)
    assert executes(cmds) == [("climate.b", SetSetpoint(22.0))]


def test_a_setpoint_retry_without_a_room_setpoint_sends_nothing() -> None:
    st = replace(failed_setpoint(21.0), setpoint=None)
    _, cmds = logic().handle(RetryDue("climate.a", "setpoint"), LATER, st)
    assert executes(cmds) == []
