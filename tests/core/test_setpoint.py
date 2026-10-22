from dataclasses import replace
from datetime import timedelta

import pytest

from custom_components.easy_trv.core.model import (
    DebounceElapsed,
    Execute,
    Failed,
    Note,
    OperatingMode,
    Preset,
    RestoreRoom,
    RetryDue,
    RoomMode,
    RoomState,
    SensorChanged,
    SetHvacMode,
    SetRoomMode,
    SetRoomOperation,
    SetRoomSetpoint,
    SetRoomTemperature,
    SetSetpoint,
    SetSource,
    Signal,
    Source,
    Start,
    Tick,
    TrvAvailabilityChanged,
    TrvHealth,
)
from custom_components.easy_trv.core.observe import shown
from custom_components.easy_trv.core.room_logic import RoomLogic
from custom_components.easy_trv.core.setpoint import clamp, operation, target
from tests.core.test_room_logic import (
    BOTH_AVAILABLE,
    CFG,
    DEFAULTS,
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


def test_ha_setpoint_leaves_frost_protection() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomOperation(OperatingMode.FROST), T0, st)
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


# --- operation -----------------------------------------------------------


def test_frost_remembers_and_heat_restores_the_setpoint() -> None:
    st = started_with_setpoint(21.0)
    st, cmds = logic().handle(SetRoomOperation(OperatingMode.FROST), T0, st)
    assert (st.setpoint, st.preset, st.frost_return) == (7.0, Preset.FROST, 21.0)
    assert operation(st) is OperatingMode.FROST
    assert st.setpoint_source == "frost"
    assert writes(cmds) == [
        ("climate.a", SetSetpoint(7.0)),
        ("climate.b", SetSetpoint(7.0)),
    ]
    st, cmds = logic().handle(SetRoomOperation(OperatingMode.HEAT), T0, st)
    assert (st.setpoint, st.preset, st.frost_return) == (21.0, Preset.NONE, None)
    assert operation(st) is OperatingMode.HEAT
    assert writes(cmds) == [
        ("climate.a", SetSetpoint(21.0)),
        ("climate.b", SetSetpoint(21.0)),
    ]


def test_frost_twice_keeps_the_remembered_setpoint() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomOperation(OperatingMode.FROST), T0, st)
    st, cmds = logic().handle(SetRoomOperation(OperatingMode.FROST), T0, st)
    assert st.frost_return == 21.0
    assert writes(cmds) == []


def test_heat_on_a_heating_room_writes_nothing() -> None:
    st = started_with_setpoint(21.0)
    st, cmds = logic().handle(SetRoomOperation(OperatingMode.HEAT), T0, st)
    assert operation(st) is OperatingMode.HEAT
    assert writes(cmds) == []


def test_frost_temperature_is_clamped_into_the_range() -> None:
    cfg = replace(CFG, frost_temperature=4.0)
    room = RoomLogic(cfg, logic()._params, capability_of=capability_of)  # noqa: SLF001
    st, _ = room.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE), T0, RoomState.initial(cfg)
    )
    st, cmds = room.handle(SetRoomOperation(OperatingMode.FROST), T0, st)
    assert st.setpoint == 5.0
    assert executes(cmds)[0] == ("climate.a", SetSetpoint(5.0))


def test_frost_from_off_switches_the_room_on_at_the_frost_temperature() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomOperation(OperatingMode.OFF), T0, st)
    assert operation(st) is OperatingMode.OFF
    st, cmds = logic().handle(SetRoomOperation(OperatingMode.FROST), T0, st)
    assert (st.mode, st.setpoint, st.frost_return) == (RoomMode.HEAT, 7.0, 21.0)
    assert operation(st) is OperatingMode.FROST
    assert writes(cmds) == [
        ("climate.a", SetHvacMode("heat")),
        ("climate.a", SetSetpoint(7.0)),
        ("climate.b", SetHvacMode("heat")),
        ("climate.b", SetSetpoint(7.0)),
    ]


def test_off_ends_frost_and_heat_comes_back_at_the_setpoint_from_before() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomOperation(OperatingMode.FROST), T0, st)
    st, cmds = logic().handle(SetRoomOperation(OperatingMode.OFF), T0, st)
    assert (st.mode, st.preset, st.setpoint) == (RoomMode.OFF, Preset.NONE, 21.0)
    assert st.frost_return is None
    assert writes(cmds) == [
        ("climate.a", SetHvacMode("off")),
        ("climate.b", SetHvacMode("off")),
    ]
    st, cmds = logic().handle(SetRoomOperation(OperatingMode.HEAT), T0, st)
    assert writes(cmds) == [
        ("climate.a", SetHvacMode("heat")),
        ("climate.a", SetSetpoint(21.0)),
        ("climate.b", SetHvacMode("heat")),
        ("climate.b", SetSetpoint(21.0)),
    ]


def test_switching_the_room_off_ends_frost_too() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomOperation(OperatingMode.FROST), T0, st)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    assert (st.preset, st.setpoint, st.frost_return) == (Preset.NONE, 21.0, None)
    assert operation(st) is OperatingMode.OFF


def test_frost_during_a_hold_only_updates_the_state() -> None:
    st = started_with_setpoint(21.0)
    st = replace(st, window_open=True)
    st, cmds = logic().handle(SetRoomOperation(OperatingMode.FROST), T0, st)
    assert (st.setpoint, operation(st)) == (7.0, OperatingMode.FROST)
    assert writes(cmds) == []


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


def test_a_write_made_at_start_is_what_the_thermostat_is_known_to_show() -> None:
    room = logic()
    st, _ = room.handle(
        RestoreRoom(22.0, RoomMode.HEAT, Preset.NONE), T0, RoomState.initial(CFG)
    )
    reported = {"climate.a": (22.0, "heat"), "climate.b": (19.0, "heat")}
    st, _ = room.handle(Start((), BOTH_AVAILABLE, reported), T0, st)
    known = st.trvs["climate.b"]
    assert known.reported_setpoint == 19.0
    assert shown(known, "setpoint", known.reported_setpoint) == 22.0
    assert shown(st.trvs["climate.a"], "setpoint", 22.0) == 22.0


def test_switching_a_heating_room_to_heat_again_writes_nothing() -> None:
    st = started_with_setpoint(21.0)
    after, cmds = logic().handle(SetRoomMode(RoomMode.HEAT), LATER, st)
    assert after == st
    assert executes(cmds) == []
    # A held room keeps its wish; an off room is switched on as before.
    off, _ = logic().handle(SetRoomMode(RoomMode.OFF), LATER, st)
    on, cmds = logic().handle(SetRoomMode(RoomMode.HEAT), LATER, off)
    assert on.mode is RoomMode.HEAT
    assert (("climate.a", SetHvacMode("heat"))) in writes(cmds)


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


def test_first_start_turns_a_thermostat_that_reports_off_on_in_a_heating_room() -> None:
    mixed = {"climate.a": (19.0, "off"), "climate.b": (19.0, "heat")}
    st, cmds = logic().handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, mixed),
        T0,
        RoomState.initial(CFG),
    )
    assert st.mode is RoomMode.HEAT
    assert writes(cmds) == [("climate.a", SetHvacMode("heat"))]
    assert st.trvs["climate.a"].written_recently["hvac_mode"][-1] == ("heat", T0)
    # A mode nobody reported is not written, and one on its schedule is left alone.
    unknown = {"climate.a": (19.0, None), "climate.b": (19.0, "auto")}
    _, cmds = logic().handle(
        Start((), BOTH_AVAILABLE, unknown), T0, RoomState.initial(CFG)
    )
    assert writes(cmds) == []


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
        SetRoomOperation(OperatingMode.FROST),
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
    st, _ = room.handle(SetRoomMode(RoomMode.OFF), T0, st)
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
    # During a hold every thermostat is meant to be off, whatever the room wants.
    held = replace(st, mode=RoomMode.HEAT, outdoor_hold=True)
    _, cmds = logic().handle(RetryDue("climate.a", "hvac_mode"), LATER, held)
    assert executes(cmds) == [("climate.a", SetHvacMode("off"))]


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
    st, _ = logic().handle(SetRoomOperation(OperatingMode.HEAT), T0, st)
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


# --- Exhausted setpoint and mode writes are sent again at the refresh interval ---


def exhausted(st: RoomState, trv: str, intent) -> RoomState:
    for _ in range(3):
        st, _ = logic().handle(result(trv, intent, Failed("busy")), T0, st)
    assert st.trvs[trv].error_capability == capability_of(intent)
    return st


def test_an_exhausted_setpoint_write_is_sent_again_with_the_current_setpoint() -> None:
    st = exhausted(started_with_setpoint(21.0), "climate.a", SetSetpoint(21.0))
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=19), st)
    assert writes(cmds) == []
    st, _ = logic().handle(SetRoomSetpoint(23.0), T0 + timedelta(minutes=20), st)
    # The new setpoint fails as well; its result is in before the next round.
    st, _ = logic().handle(
        result("climate.a", SetSetpoint(23.0), Failed("busy")),
        T0 + timedelta(minutes=20),
        st,
    )
    again = T0 + timedelta(minutes=21)
    st, cmds = logic().handle(Tick(), again, st)
    assert (("climate.a", SetSetpoint(23.0))) in writes(cmds)
    assert [w for w in writes(cmds) if w[0] == "climate.b"] == []
    assert st.trvs["climate.a"].written_recently["setpoint"][-1] == (23.0, again)
    _, cmds = logic().handle(Tick(), again + timedelta(minutes=1), st)
    assert writes(cmds) == []


def test_an_exhausted_mode_write_is_sent_again_as_heat() -> None:
    st = exhausted(started_with_setpoint(21.0), "climate.a", SetHvacMode("heat"))
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=21), st)
    assert writes(cmds) == [("climate.a", SetHvacMode("heat"))]


def test_an_exhausted_setpoint_write_is_not_sent_while_the_room_is_off() -> None:
    st = exhausted(started_with_setpoint(21.0), "climate.a", SetSetpoint(21.0))
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=21), st)
    assert writes(cmds) == []


def test_exhausted_writes_are_not_sent_to_a_thermostat_left_alone() -> None:
    st = exhausted(started_with_setpoint(21.0), "climate.a", SetSetpoint(21.0))
    st = with_trv(st, "climate.a", note=Note.SCHEDULE)
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=21), st)
    assert writes(cmds) == []


def test_a_room_without_a_climate_does_not_resend_setpoint_or_mode() -> None:
    cfg = replace(CFG, setpoint_range=None)
    room = RoomLogic(cfg, DEFAULTS, capability_of=capability_of)
    st, _ = room.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE), T0, RoomState.initial(cfg)
    )
    st = with_trv(
        st,
        "climate.a",
        health=TrvHealth.ERROR,
        error_capability="setpoint",
        given_up=frozenset({"setpoint"}),
        last_error="busy",
        last_error_retry=T0,
    )
    _, cmds = room.handle(Tick(), T0 + timedelta(minutes=21), st)
    assert writes(cmds) == []


# --- target while away ----------------------------------------------------


@pytest.mark.parametrize(
    ("wish", "setback", "expected"),
    [(21.0, 3.0, 18.0), (9.0, 3.0, 7.0), (6.0, 3.0, 6.0), (21.0, 2.3, 18.5)],
)
def test_target_while_away(wish, setback, expected) -> None:
    cfg = replace(CFG, away_action="setback", away_setback=setback)
    st = replace(started_with_setpoint(wish), away=True)
    assert target(cfg, st) == expected
    assert target(cfg, replace(st, away_override=True)) == wish
    assert target(replace(cfg, away_action="off"), st) == wish
    assert target(replace(cfg, away_action=None), st) == wish


def test_target_at_home_or_not_heating_is_the_wish() -> None:
    cfg = replace(CFG, away_action="setback", away_setback=3.0)
    st = started_with_setpoint(21.0)
    assert target(cfg, st) == 21.0
    away = replace(st, away=True)
    assert target(cfg, replace(away, mode=RoomMode.OFF)) == 21.0
    assert target(cfg, replace(away, setpoint=None)) is None
