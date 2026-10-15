from dataclasses import replace
from datetime import timedelta

from custom_components.easy_trv.core.model import (
    Failed,
    FireEvent,
    KnobSettled,
    Mismatch,
    Note,
    Ok,
    Preset,
    RaiseIssue,
    RestoreRoom,
    RoomMode,
    RoomParams,
    RoomState,
    ScheduleKnobSettle,
    ScheduleRetry,
    SetHvacMode,
    SetRoomMode,
    SetRoomPreset,
    SetRoomSetpoint,
    SetRoomTemperature,
    SetSetpoint,
    SetSource,
    Signal,
    Start,
    Tick,
    TrvAvailabilityChanged,
    TrvObserved,
)
from custom_components.easy_trv.core.observe import is_echo
from custom_components.easy_trv.core.room_logic import RoomLogic
from tests.core.test_room_logic import (
    CFG,
    T0,
    capability_of,
    executes,
    logic,
    of_type,
    result,
    started,
    with_trv,
)
from tests.core.test_setpoint import LATER, off_room, started_with_setpoint, writes

# --- observations: knob turns, echoes, device-driven values --------------

A, B = "climate.a", "climate.b"
AFTER = T0 + timedelta(seconds=300)
SETTLED = AFTER + timedelta(seconds=3)


def reported(trv: str, value: float, kind: str = "report") -> TrvObserved:
    return TrvObserved(trv, "setpoint", value, round(value * 100), kind)


def reported_mode(trv: str, mode: str | None, raw: int | None = None) -> TrvObserved:
    if raw is None:
        raw = 4 if mode == "heat" else 0
    return TrvObserved(trv, "hvac_mode", mode, raw, "report")


def auto(trv: str) -> TrvObserved:
    return reported_mode(trv, None, raw=1)


def settle(
    st: RoomState, trv: str = A, capability: str = "setpoint", at=SETTLED
) -> tuple[RoomState, list]:
    return logic().handle(KnobSettled(trv, capability), at, st)


def adopted_events(cmds: list) -> list[FireEvent]:
    return [
        c for c in of_type(cmds, FireEvent) if c.name == "easy_trv_setpoint_adopted"
    ]


def test_knob_turn_is_adopted_and_spread_to_siblings() -> None:
    st = started_with_setpoint(21.0)
    st, cmds = logic().handle(reported(A, 22.0), AFTER, st)
    assert executes(cmds) == []
    assert of_type(cmds, ScheduleKnobSettle) == [ScheduleKnobSettle(A, "setpoint", 3.0)]
    assert st.trvs[A].knob_pending == {"setpoint": 22.0}
    assert st.trvs[A].reported_setpoint == 22.0
    assert st.setpoint == 21.0

    st, cmds = settle(st)
    assert st.setpoint == 22.0
    assert st.setpoint_source == f"device:{A}"
    assert st.trvs[A].knob_pending == {}
    assert executes(cmds) == [(B, SetSetpoint(22.0))]
    assert st.trvs[B].written_recently["setpoint"][-1] == (22.0, SETTLED)
    assert adopted_events(cmds) == [
        FireEvent(
            "easy_trv_setpoint_adopted", {"room": "Living", "trv": A, "value": 22.0}
        )
    ]


def test_knob_steps_within_debounce_adopt_the_last_value_once() -> None:
    st = started_with_setpoint(20.0)
    for step, value in enumerate((20.5, 21.0, 21.5)):
        at = AFTER + timedelta(seconds=step)
        st, cmds = logic().handle(reported(A, value), at, st)
        assert executes(cmds) == []
        assert of_type(cmds, ScheduleKnobSettle) == [
            ScheduleKnobSettle(A, "setpoint", 3.0)
        ]
    assert st.setpoint == 20.0
    st, cmds = settle(st)
    assert st.setpoint == 21.5
    assert executes(cmds) == [(B, SetSetpoint(21.5))]
    assert len(adopted_events(cmds)) == 1
    again, cmds = settle(st)
    assert again == st
    assert executes(cmds) == []
    assert adopted_events(cmds) == []


def test_late_echo_within_120_s_is_not_adopted() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomSetpoint(22.0), T0 + timedelta(seconds=10), st)
    echo_at = T0 + timedelta(seconds=30)
    st, cmds = logic().handle(reported(A, 21.0), echo_at, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.trvs[A].knob_pending == {}
    assert st.trvs[A].reported_setpoint == 21.0
    assert st.setpoint == 22.0
    assert is_echo(st.trvs[A], "setpoint", 21.0, echo_at)


def test_echo_after_120_s_is_a_knob_turn() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomSetpoint(22.0), T0 + timedelta(seconds=10), st)
    late = T0 + timedelta(seconds=121)
    assert not is_echo(st.trvs[A], "setpoint", 21.0, late)
    st, cmds = logic().handle(reported(A, 21.0), late, st)
    assert of_type(cmds, ScheduleKnobSettle) == [ScheduleKnobSettle(A, "setpoint", 3.0)]
    st, cmds = settle(st, at=late + timedelta(seconds=3))
    assert st.setpoint == 21.0
    assert executes(cmds) == [(B, SetSetpoint(21.0))]


def test_knob_value_outside_the_range_is_clamped_and_written_back() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 35.0), AFTER, st)
    st, cmds = settle(st)
    assert st.setpoint == 30.0
    assert executes(cmds) == [(A, SetSetpoint(30.0)), (B, SetSetpoint(30.0))]
    assert st.trvs[A].written_recently["setpoint"][-1] == (30.0, SETTLED)
    assert adopted_events(cmds)[0].data["value"] == 30.0


def test_a_clamped_knob_value_equal_to_the_room_setpoint_is_only_written_back() -> None:
    st = started_with_setpoint(30.0)
    st, _ = logic().handle(reported(A, 35.0), AFTER, st)
    _, cmds = settle(st)
    assert executes(cmds) == [(A, SetSetpoint(30.0))]


def test_knob_turn_while_off_turns_the_room_on() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st, cmds = logic().handle(reported(A, 22.0), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle)
    st, cmds = settle(st)
    assert st.mode is RoomMode.HEAT
    assert st.setpoint == 22.0
    assert executes(cmds) == [
        (A, SetHvacMode("heat")),
        (A, SetSource("external")),
        (A, SetRoomTemperature(21.3)),
        (A, SetSetpoint(22.0)),
        (B, SetHvacMode("heat")),
        (B, SetSource("external")),
        (B, SetRoomTemperature(21.3)),
        (B, SetSetpoint(22.0)),
    ]


def test_knob_turn_leaves_the_frost_preset() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomPreset(Preset.FROST), T0, st)
    st, _ = logic().handle(reported(A, 19.0), AFTER, st)
    st, _ = settle(st)
    assert (st.preset, st.frost_return, st.setpoint) == (Preset.NONE, None, 19.0)


def test_a_knob_turned_back_before_it_settles_adopts_nothing() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 22.0), AFTER, st)
    st, _ = logic().handle(reported(A, 21.0), AFTER + timedelta(seconds=1), st)
    st, cmds = settle(st)
    assert executes(cmds) == []
    assert adopted_events(cmds) == []
    assert st.setpoint_source == "ha"


def test_a_repeated_report_of_the_room_value_is_not_a_knob_turn() -> None:
    st = started_with_setpoint(21.0)
    st, cmds = logic().handle(reported(A, 21.0), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.trvs[A].reported_setpoint == 21.0
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    _, cmds = logic().handle(reported_mode(A, "off"), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == []


def test_a_foreign_write_is_treated_like_a_report() -> None:
    st = started_with_setpoint(21.0)
    event = reported(A, 23.0, kind="written")
    st, cmds = logic().handle(event, AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle)


def test_the_mode_knob_turned_to_off_switches_the_room_off() -> None:
    st = with_trv(started_with_setpoint(21.0), A, reported_mode="heat")
    st, cmds = logic().handle(reported_mode(A, "off"), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == [
        ScheduleKnobSettle(A, "hvac_mode", 3.0)
    ]
    assert st.trvs[A].reported_mode == "off"
    st, cmds = settle(st, capability="hvac_mode")
    assert st.mode is RoomMode.OFF
    assert st.setpoint == 21.0
    assert st.setpoint_source == f"device:{A}"
    assert executes(cmds) == [(B, SetHvacMode("off"))]
    assert adopted_events(cmds)[0].data == {"room": "Living", "trv": A, "value": "off"}


def test_the_mode_knob_turned_to_heat_resumes_the_room() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st, _ = logic().handle(reported_mode(A, "heat"), AFTER, st)
    st, cmds = settle(st, capability="hvac_mode")
    assert st.mode is RoomMode.HEAT
    assert (A, SetHvacMode("heat")) in executes(cmds)
    assert (B, SetSetpoint(21.0)) in executes(cmds)
    assert (B, SetSource("external")) in executes(cmds)


def test_a_mode_echo_is_not_adopted() -> None:
    st, _ = started()
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st, _ = logic().handle(SetRoomMode(RoomMode.HEAT), T0 + timedelta(seconds=5), st)
    st, cmds = logic().handle(reported_mode(A, "off"), T0 + timedelta(seconds=20), st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.mode is RoomMode.HEAT


def test_auto_mode_marks_schedule_and_excludes_the_thermostat() -> None:
    st = started_with_setpoint(21.0)
    st, cmds = logic().handle(auto(A), AFTER, st)
    assert st.trvs[A].note is Note.SCHEDULE
    assert st.trvs[A].note_since == AFTER
    assert st.trvs[A].reported_mode == "auto"
    assert of_type(cmds, ScheduleKnobSettle) == []
    st, cmds = logic().handle(SetRoomSetpoint(22.0), AFTER, st)
    assert executes(cmds) == [(B, SetSetpoint(22.0))]
    _, cmds = logic().handle(SetRoomMode(RoomMode.OFF), AFTER, st)
    assert executes(cmds) == [(B, SetHvacMode("off"))]


def test_a_setpoint_report_of_a_schedule_thermostat_is_not_adopted() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(auto(A), AFTER, st)
    st, cmds = logic().handle(reported(A, 17.0), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.trvs[A].reported_setpoint == 17.0
    assert st.setpoint == 21.0


def test_heat_after_auto_writes_the_room_values_once() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(auto(A), AFTER, st)
    later = AFTER + timedelta(minutes=5)
    st, cmds = logic().handle(reported_mode(A, "heat"), later, st)
    assert st.trvs[A].note is None
    assert st.trvs[A].reported_mode == "heat"
    assert executes(cmds) == [(A, SetHvacMode("heat")), (A, SetSetpoint(21.0))]
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.trvs[A].resync_pending == {"setpoint": 21.0, "hvac_mode": "heat"}
    st, _ = logic().handle(reported(A, 17.0), later, st)
    assert st.trvs[A].knob_pending == {}
    st, _ = logic().handle(result(A, SetHvacMode("heat"), Ok()), later, st)
    st, _ = logic().handle(result(A, SetSetpoint(21.0), Ok()), later, st)
    assert st.trvs[A].resync_pending == {}
    _, cmds = logic().handle(reported(A, 19.0), later, st)
    assert of_type(cmds, ScheduleKnobSettle)


def test_a_schedule_signal_on_a_mode_write_marks_schedule_without_a_retry() -> None:
    st = started_with_setpoint(21.0)
    st, cmds = logic().handle(
        result(A, SetHvacMode("heat"), Signal("schedule")), AFTER, st
    )
    assert st.trvs[A].note is Note.SCHEDULE
    assert st.trvs[A].reported_mode == "auto"
    assert st.trvs[A].attempts == {}
    assert of_type(cmds, ScheduleRetry) == []
    assert of_type(cmds, RaiseIssue) == []
    _, cmds = logic().handle(SetRoomSetpoint(22.0), AFTER, st)
    assert executes(cmds) == [(B, SetSetpoint(22.0))]


def window_room(setpoint: float = 21.0) -> RoomState:
    return started_with_setpoint(setpoint)


def test_twelve_degrees_with_window_detection_is_a_window_phase() -> None:
    st = window_room()
    st, cmds = logic().handle(reported(A, 12.0), AFTER, st)
    assert st.trvs[A].note is Note.WINDOW_PHASE
    assert st.trvs[A].note_since == AFTER
    assert st.trvs[A].reported_setpoint == 12.0
    assert of_type(cmds, ScheduleKnobSettle) == []
    st, cmds = logic().handle(SetRoomSetpoint(22.0), AFTER, st)
    assert executes(cmds) == [(B, SetSetpoint(22.0))]
    # A second report of 12.0 does not restart the 30 minutes.
    st, _ = logic().handle(reported(A, 12.0), AFTER + timedelta(minutes=10), st)
    assert st.trvs[A].note_since == AFTER


def test_twelve_degrees_is_a_knob_turn_without_window_detection() -> None:
    cfg = replace(CFG, window_detection="off")
    room = RoomLogic(cfg, RoomParams.defaults(), capability_of=capability_of)
    st = window_room()
    st, cmds = room.handle(reported(A, 12.0), AFTER, st)
    assert st.trvs[A].note is None
    assert of_type(cmds, ScheduleKnobSettle)


def test_twelve_degrees_is_a_knob_turn_when_the_room_wants_twelve() -> None:
    st = window_room(12.0)
    st, _ = logic().handle(reported(A, 14.0), AFTER, st)
    st, _ = settle(st)
    assert st.setpoint == 14.0
    st, _ = logic().handle(SetRoomSetpoint(12.0), SETTLED + timedelta(seconds=10), st)
    _, cmds = logic().handle(reported(B, 12.0), AFTER + timedelta(minutes=5), st)
    assert of_type(cmds, ScheduleKnobSettle) == []


def test_another_value_ends_the_window_phase() -> None:
    st = window_room()
    st, _ = logic().handle(reported(A, 12.0), AFTER, st)
    st, cmds = logic().handle(reported(A, 21.0), AFTER + timedelta(minutes=2), st)
    assert st.trvs[A].note is None
    assert st.trvs[A].note_since is None
    assert of_type(cmds, ScheduleKnobSettle) == []
    st, cmds = logic().handle(SetRoomSetpoint(22.0), AFTER, st)
    assert executes(cmds) == [(A, SetSetpoint(22.0)), (B, SetSetpoint(22.0))]


def test_window_phase_ends_after_30_minutes_and_the_room_value_is_written() -> None:
    st = window_room()
    st, _ = logic().handle(reported(A, 12.0), AFTER, st)
    _, cmds = logic().handle(Tick(), AFTER + timedelta(minutes=29), st)
    assert writes(cmds) == []
    st, cmds = logic().handle(Tick(), AFTER + timedelta(minutes=30), st)
    assert writes(cmds) == [(A, SetSetpoint(21.0))]
    assert st.trvs[A].note is None
    assert st.trvs[A].note_since is None
    assert st.trvs[A].written_recently["setpoint"][-1] == (
        21.0,
        AFTER + timedelta(minutes=30),
    )
    _, cmds = logic().handle(Tick(), AFTER + timedelta(minutes=31), st)
    assert writes(cmds) == []


def test_window_phase_expiry_writes_nothing_while_the_room_is_off_or_held() -> None:
    st = window_room()
    st, _ = logic().handle(reported(A, 12.0), AFTER, st)
    off, _ = logic().handle(SetRoomMode(RoomMode.OFF), AFTER, st)
    off, cmds = logic().handle(Tick(), AFTER + timedelta(minutes=31), off)
    assert writes(cmds) == []
    assert off.trvs[A].note is None
    held = replace(st, window_open=True)
    held, cmds = logic().handle(Tick(), AFTER + timedelta(minutes=31), held)
    assert writes(cmds) == []
    assert held.trvs[A].note is None


def returning(st: RoomState, at=LATER) -> tuple[RoomState, list]:
    st = with_trv(st, A, available=False)
    return logic().handle(TrvAvailabilityChanged(A, available=True), at, st)


def test_a_returning_thermostat_gets_mode_and_setpoint_and_adopts_nothing() -> None:
    st = started_with_setpoint(21.0)
    st, cmds = returning(st)
    assert (A, SetHvacMode("heat")) in executes(cmds)
    assert (A, SetSetpoint(21.0)) in executes(cmds)
    assert st.trvs[A].resync_pending == {"setpoint": 21.0, "hvac_mode": "heat"}
    assert st.trvs[A].written_recently["setpoint"][-1] == (21.0, LATER)
    st, cmds = logic().handle(reported(A, 18.0), LATER, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.trvs[A].reported_setpoint == 18.0
    st, _ = logic().handle(result(A, SetSetpoint(21.0), Ok()), LATER, st)
    assert st.trvs[A].resync_pending == {"hvac_mode": "heat"}
    _, cmds = logic().handle(reported(A, 19.0), LATER, st)
    assert of_type(cmds, ScheduleKnobSettle)


def test_the_resync_guard_ends_on_failure_and_mismatch_too() -> None:
    st, _ = returning(started_with_setpoint(21.0))
    st, _ = logic().handle(result(A, SetSetpoint(21.0), Failed("busy")), LATER, st)
    st, _ = logic().handle(
        result(A, SetHvacMode("heat"), Mismatch(frozenset({4}), 0, observed="off")),
        LATER,
        st,
    )
    assert st.trvs[A].resync_pending == {}


def test_a_mismatch_during_the_resync_is_a_failure_not_an_adoption() -> None:
    st, _ = returning(started_with_setpoint(21.0))
    st, cmds = logic().handle(
        result(A, SetSetpoint(21.0), Mismatch(frozenset({2100}), 1800, observed=18.0)),
        LATER,
        st,
    )
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert of_type(cmds, ScheduleRetry)
    assert st.trvs[A].knob_pending == {}


def test_a_returning_thermostat_of_an_off_room_gets_off_and_no_setpoint() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st, cmds = returning(st)
    assert writes(cmds) == [(A, SetHvacMode("off"))]
    assert st.trvs[A].resync_pending == {"hvac_mode": "off"}


def test_a_returning_thermostat_gets_off_in_a_hold_and_nothing_on_its_schedule() -> (
    None
):
    st = replace(started_with_setpoint(21.0), window_open=True)
    _, cmds = returning(st)
    assert writes(cmds) == [(A, SetHvacMode("off"))]
    st = started_with_setpoint(21.0)
    st = with_trv(st, A, reported_mode="auto")
    _, cmds = returning(st)
    assert writes(cmds) == []


def test_a_thermostat_going_away_forgets_the_resync_guard() -> None:
    st, _ = returning(started_with_setpoint(21.0))
    st, _ = logic().handle(TrvAvailabilityChanged(A, available=False), LATER, st)
    assert st.trvs[A].resync_pending == {}


def test_observations_during_a_hold_update_the_wish_and_write_nothing() -> None:
    st = started_with_setpoint(21.0)
    st = replace(st, window_open=True)
    st, cmds = logic().handle(reported(A, 23.0), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle)
    st, cmds = settle(st)
    assert st.setpoint == 23.0
    assert st.setpoint_source == f"device:{A}"
    assert writes(cmds) == []
    st, cmds = logic().handle(reported_mode(A, "heat"), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.trvs[A].reported_mode == "heat"
    # The thermostats are meant to be off: the stray heat is corrected.
    assert writes(cmds) == [(A, SetHvacMode("off"))]
    assert st.mode is RoomMode.HEAT


def test_a_knob_turn_while_off_and_held_keeps_the_room_off() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st = replace(st, outdoor_hold=True)
    st, _ = logic().handle(reported(A, 22.0), AFTER, st)
    st, cmds = settle(st)
    assert st.mode is RoomMode.OFF
    assert st.setpoint == 22.0
    assert writes(cmds) == []


def test_mismatch_with_a_foreign_value_is_an_adoption_not_a_retry() -> None:
    st = started_with_setpoint(21.0)
    mismatch = Mismatch(frozenset({2100}), 2250, observed=22.5)
    st, cmds = logic().handle(result(A, SetSetpoint(21.0), mismatch), AFTER, st)
    assert of_type(cmds, ScheduleRetry) == []
    assert of_type(cmds, RaiseIssue) == []
    assert of_type(cmds, ScheduleKnobSettle) == [ScheduleKnobSettle(A, "setpoint", 3.0)]
    assert st.trvs[A].attempts == {}
    assert st.trvs[A].knob_pending == {"setpoint": 22.5}
    st, cmds = settle(st)
    assert st.setpoint == 22.5
    assert executes(cmds) == [(B, SetSetpoint(22.5))]


def test_mismatch_with_a_foreign_mode_is_an_adoption_too() -> None:
    st = with_trv(started_with_setpoint(21.0), A, reported_mode="heat")
    mismatch = Mismatch(frozenset({4}), 0, observed="off")
    st, cmds = logic().handle(result(A, SetHvacMode("heat"), mismatch), AFTER, st)
    assert of_type(cmds, ScheduleRetry) == []
    assert st.trvs[A].knob_pending == {"hvac_mode": "off"}


def test_mismatch_with_our_own_value_is_retried() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomSetpoint(22.0), T0 + timedelta(seconds=10), st)
    stale = Mismatch(frozenset({2200}), 2100, observed=21.0)
    st, cmds = logic().handle(
        result(A, SetSetpoint(22.0), stale), T0 + timedelta(seconds=20), st
    )
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert of_type(cmds, ScheduleRetry)
    assert st.trvs[A].attempts == {"setpoint": 1}
    assert "setpoint" in st.trvs[A].pending_retry


def test_a_mismatch_without_an_observed_value_is_a_failure() -> None:
    st = started_with_setpoint(21.0)
    _, cmds = logic().handle(
        result(A, SetSetpoint(21.0), Mismatch(frozenset({2100}), 2250)), AFTER, st
    )
    assert of_type(cmds, ScheduleRetry)


def test_observations_of_other_capabilities_and_without_climate_are_ignored() -> None:
    st = started_with_setpoint(21.0)
    other = TrvObserved(A, "source", "external", 1, "report")
    after, cmds = logic().handle(other, AFTER, st)
    assert after == st
    assert of_type(cmds, ScheduleKnobSettle) == []
    cfg = replace(CFG, setpoint_range=None)
    room = RoomLogic(cfg, RoomParams.defaults(), capability_of=capability_of)
    plain = RoomState.initial(cfg)
    after, cmds = room.handle(reported(A, 22.0), AFTER, plain)
    assert after == plain
    assert of_type(cmds, ScheduleKnobSettle) == []
    after, _ = room.handle(KnobSettled(A, "setpoint"), AFTER, plain)
    assert after == plain


def test_nothing_is_adopted_for_a_settled_knob_that_is_gone_or_unusable() -> None:
    off = off_room()
    after, cmds = settle(off)
    assert after == off
    assert executes(cmds) == []
    # A turn that became a schedule or resync case before it settled.
    for changes in (
        {"note": Note.SCHEDULE},
        {"resync_pending": frozenset({"setpoint"})},
    ):
        st = with_trv(started_with_setpoint(21.0), A, knob_pending={"setpoint": 22.0})
        st = with_trv(st, A, **changes)
        after, cmds = settle(st)
        assert after.setpoint == 21.0
        assert after.trvs[A].knob_pending == {}
        assert executes(cmds) == []
        assert adopted_events(cmds) == []
    # A value of a capability the room does not own.
    st = with_trv(started_with_setpoint(21.0), A, knob_pending={"source": 1.0})
    after, cmds = settle(st, capability="source")
    assert after.setpoint == 21.0
    assert executes(cmds) == []
    assert adopted_events(cmds) == []


def test_a_mode_turn_that_the_room_overtook_adopts_nothing() -> None:
    st = with_trv(started_with_setpoint(21.0), A, reported_mode="heat")
    st, _ = logic().handle(reported_mode(A, "off"), AFTER, st)
    held = replace(st, window_open=True)
    after, cmds = settle(held, capability="hvac_mode")
    assert after.mode is RoomMode.HEAT
    assert executes(cmds) == []
    assert adopted_events(cmds) == []
    off, _ = logic().handle(SetRoomMode(RoomMode.OFF), AFTER, st)
    after, cmds = settle(off, capability="hvac_mode")
    assert after.setpoint_source == "ha"
    assert executes(cmds) == []
    assert adopted_events(cmds) == []


def test_reports_without_a_usable_value_are_ignored() -> None:
    st = started_with_setpoint(21.0)
    assert not is_echo(st.trvs[A], "setpoint", None, AFTER)
    for event in (
        TrvObserved(A, "setpoint", None, 0, "report"),
        TrvObserved(A, "hvac_mode", None, 0, "report"),
        TrvObserved("climate.unknown", "setpoint", 22.0, 2200, "report"),
    ):
        after, cmds = logic().handle(event, AFTER, st)
        assert after == st
        assert of_type(cmds, ScheduleKnobSettle) == []


# --- fix round 1: stale reports, converging knobs, tagged guard ----------


def test_a_stale_report_while_the_room_is_off_is_not_a_knob_turn() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st, cmds = logic().handle(SetRoomSetpoint(23.0), AFTER, st)
    assert writes(cmds) == []
    st, cmds = logic().handle(reported(A, 21.0), AFTER + timedelta(minutes=5), st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.mode is RoomMode.OFF
    assert st.setpoint == 23.0
    assert st.trvs[A].reported_setpoint == 21.0


def test_a_stale_report_during_a_hold_is_not_a_knob_turn() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomPreset(Preset.FROST), T0, st)
    st = replace(st, window_open=True)
    st, _ = logic().handle(SetRoomSetpoint(23.0), AFTER, st)
    st, cmds = logic().handle(reported(A, 7.0), AFTER + timedelta(minutes=5), st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert (st.setpoint, st.preset) == (23.0, Preset.NONE)
    st = started_with_setpoint(21.0)
    st = replace(st, window_open=True)
    st, _ = logic().handle(SetRoomSetpoint(23.0), AFTER, st)
    st, cmds = logic().handle(reported(A, 21.0), AFTER + timedelta(minutes=5), st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.setpoint == 23.0


def test_leaving_the_window_phase_writes_the_room_value_instead_of_adopting() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 12.0), AFTER, st)
    assert st.trvs[A].note is Note.WINDOW_PHASE
    st, cmds = logic().handle(SetRoomSetpoint(23.0), AFTER, st)
    assert executes(cmds) == [(B, SetSetpoint(23.0))]
    back = AFTER + timedelta(minutes=5)
    st, cmds = logic().handle(reported(A, 21.0), back, st)
    assert st.trvs[A].note is None
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert executes(cmds) == [(A, SetSetpoint(23.0))]
    assert st.setpoint == 23.0
    assert st.trvs[A].resync_pending == {"setpoint": 23.0}


def test_leaving_the_window_phase_for_the_room_value_writes_nothing() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 12.0), AFTER, st)
    st, cmds = logic().handle(reported(A, 21.0), AFTER + timedelta(minutes=5), st)
    assert executes(cmds) == []
    assert st.trvs[A].note is None


def test_leaving_the_window_phase_while_off_writes_nothing() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 12.0), AFTER, st)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), AFTER, st)
    st, cmds = logic().handle(reported(A, 19.0), AFTER + timedelta(minutes=5), st)
    assert writes(cmds) == []
    assert of_type(cmds, ScheduleKnobSettle) == []


def test_the_first_report_of_a_thermostat_is_only_recorded() -> None:
    st, _ = started()
    st, cmds = logic().handle(reported(A, 22.0), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.trvs[A].reported_setpoint == 22.0
    assert st.setpoint is None
    st, cmds = logic().handle(reported(A, 23.0), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == [ScheduleKnobSettle(A, "setpoint", 3.0)]
    st, cmds = logic().handle(reported_mode(B, "off"), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.trvs[B].reported_mode == "off"
    st, cmds = logic().handle(reported_mode(B, "heat"), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == [
        ScheduleKnobSettle(B, "hvac_mode", 3.0)
    ]


def test_a_stale_mode_report_is_not_a_knob_turn() -> None:
    st = started_with_setpoint(21.0)
    st = with_trv(st, A, reported_mode="heat")
    st = replace(st, mode=RoomMode.OFF)
    st, cmds = logic().handle(reported_mode(A, "heat"), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.mode is RoomMode.OFF


def test_a_report_is_compared_with_the_newer_of_report_and_write() -> None:
    st = started_with_setpoint(21.0)
    st = with_trv(st, A, reported_setpoint=19.0)
    # The write (21.0 at T0) is newer than a report that carries no time.
    later = T0 + timedelta(minutes=10)
    _, cmds = logic().handle(reported(A, 21.0), later, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    st, cmds = logic().handle(reported(A, 19.0), later, st)
    assert of_type(cmds, ScheduleKnobSettle)
    # A report newer than the write is what the thermostat shows now.
    _, cmds = logic().handle(reported(A, 19.0), later + timedelta(minutes=10), st)
    assert of_type(cmds, ScheduleKnobSettle) == []


def test_two_knobs_within_the_debounce_leave_every_thermostat_at_the_last_one() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 22.0), AFTER, st)
    st, _ = logic().handle(reported(B, 23.0), AFTER + timedelta(seconds=1), st)
    st, _ = settle(st, A, at=AFTER + timedelta(seconds=3))
    assert st.setpoint == 22.0
    st, cmds = settle(st, B, at=AFTER + timedelta(seconds=4))
    assert st.setpoint == 23.0
    assert st.setpoint_source == f"device:{B}"
    assert executes(cmds) == [(A, SetSetpoint(23.0)), (B, SetSetpoint(23.0))]
    for trv in (A, B):
        assert st.trvs[trv].written_recently["setpoint"][-1][0] == 23.0


def test_an_adoption_writes_to_thermostats_that_are_out_of_line() -> None:
    st = started_with_setpoint(21.0)
    st = with_trv(st, B, reported_setpoint=19.0, reported_at={"setpoint": LATER})
    st, _ = logic().handle(reported(A, 22.0), LATER, st)
    st, cmds = settle(st, at=LATER + timedelta(seconds=3))
    assert executes(cmds) == [(B, SetSetpoint(22.0))]
    # Everything in line: a turn back to the room value changes nothing.
    st, _ = logic().handle(reported(A, 21.0), LATER + timedelta(seconds=10), st)
    st = with_trv(st, B, reported_setpoint=22.0, reported_at={"setpoint": LATER})
    st, _ = logic().handle(SetRoomSetpoint(21.0), LATER + timedelta(seconds=11), st)
    assert st.setpoint == 21.0


def test_only_the_result_of_the_resync_write_releases_the_guard() -> None:
    st, _ = returning(started_with_setpoint(21.0))
    assert st.trvs[A].resync_pending == {"setpoint": 21.0, "hvac_mode": "heat"}
    st, _ = logic().handle(result(A, SetSetpoint(20.0), Ok()), LATER, st)
    assert st.trvs[A].resync_pending == {"setpoint": 21.0, "hvac_mode": "heat"}
    st, cmds = logic().handle(reported(A, 18.0), LATER, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    st, _ = logic().handle(result(A, SetSetpoint(21.0), Ok()), LATER, st)
    assert st.trvs[A].resync_pending == {"hvac_mode": "heat"}
    st, _ = logic().handle(result(A, SetHvacMode("off"), Ok()), LATER, st)
    assert st.trvs[A].resync_pending == {"hvac_mode": "heat"}


def test_what_the_thermostats_reported_at_start_is_the_known_value() -> None:
    reported_at_start = {A: (19.0, "heat"), B: (19.0, "heat")}
    st, _ = logic().handle(
        Start((), {A: True, B: True}, reported_at_start), T0, RoomState.initial(CFG)
    )
    assert st.trvs[A].reported_at == {"setpoint": T0, "hvac_mode": T0}
    _, cmds = logic().handle(reported(A, 19.0), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle) == []
    _, cmds = logic().handle(reported(A, 20.0), AFTER, st)
    assert of_type(cmds, ScheduleKnobSettle)


# --- fix round 2 ---------------------------------------------------------


def test_a_newer_ha_setpoint_drops_a_pending_knob_value() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 22.0), AFTER, st)
    assert st.trvs[A].knob_pending == {"setpoint": 22.0}
    st, cmds = logic().handle(SetRoomSetpoint(24.0), AFTER + timedelta(seconds=1), st)
    assert executes(cmds) == [(A, SetSetpoint(24.0)), (B, SetSetpoint(24.0))]
    assert st.trvs[A].knob_pending == {}
    st, _ = logic().handle(reported(A, 24.0), AFTER + timedelta(seconds=2), st)
    st, cmds = settle(st, at=SETTLED)
    assert st.setpoint == 24.0
    assert st.setpoint_source == "ha"
    assert executes(cmds) == []
    assert adopted_events(cmds) == []


def test_a_newer_ha_preset_or_restore_drops_a_pending_knob_value() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 22.0), AFTER, st)
    frost, _ = logic().handle(SetRoomPreset(Preset.FROST), AFTER, st)
    assert frost.trvs[A].knob_pending == {}
    restored, _ = logic().handle(
        RestoreRoom(20.0, RoomMode.HEAT, Preset.NONE), AFTER, st
    )
    assert restored.trvs[A].knob_pending == {}


def test_a_newer_ha_mode_drops_a_pending_knob_mode() -> None:
    st = with_trv(started_with_setpoint(21.0), A, reported_mode="heat")
    st, _ = logic().handle(reported_mode(A, "off"), AFTER, st)
    assert st.trvs[A].knob_pending == {"hvac_mode": "off"}
    st, _ = logic().handle(SetRoomMode(RoomMode.HEAT), AFTER, st)
    assert st.trvs[A].knob_pending == {}
    st, cmds = settle(st, capability="hvac_mode", at=SETTLED)
    assert st.mode is RoomMode.HEAT
    assert executes(cmds) == []
    assert adopted_events(cmds) == []


def test_an_adoption_keeps_the_pending_knob_value_of_another_thermostat() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 22.0), AFTER, st)
    st, _ = logic().handle(reported(B, 23.0), AFTER + timedelta(seconds=1), st)
    st, _ = settle(st, A, at=AFTER + timedelta(seconds=3))
    assert st.trvs[B].knob_pending == {"setpoint": 23.0}


def test_leaving_the_window_phase_with_an_echo_value_still_writes_the_room_value() -> (
    None
):
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 12.0), T0 + timedelta(seconds=20), st)
    st, cmds = logic().handle(SetRoomSetpoint(23.0), T0 + timedelta(seconds=30), st)
    assert executes(cmds) == [(B, SetSetpoint(23.0))]
    st, cmds = logic().handle(reported(A, 21.0), T0 + timedelta(seconds=60), st)
    assert st.trvs[A].note is None
    assert executes(cmds) == [(A, SetSetpoint(23.0))]
    assert of_type(cmds, ScheduleKnobSettle) == []


# --- fix round 3 ---------------------------------------------------------


def test_switching_the_room_off_drops_a_pending_setpoint_knob() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 22.0), AFTER, st)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), AFTER + timedelta(seconds=1), st)
    assert st.trvs[A].knob_pending == {}
    st, cmds = settle(st)
    assert st.mode is RoomMode.OFF
    assert st.setpoint == 21.0
    assert executes(cmds) == []
    assert adopted_events(cmds) == []


def test_switching_the_room_on_keeps_a_pending_setpoint_knob() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(SetRoomMode(RoomMode.OFF), T0 + timedelta(seconds=1), st)
    st, _ = logic().handle(reported(A, 22.0), AFTER, st)
    st, _ = logic().handle(SetRoomMode(RoomMode.HEAT), AFTER + timedelta(seconds=1), st)
    assert st.trvs[A].knob_pending == {"setpoint": 22.0}


def test_a_restore_drops_a_pending_knob_mode() -> None:
    st = with_trv(started_with_setpoint(21.0), A, reported_mode="heat")
    st, _ = logic().handle(reported_mode(A, "off"), AFTER, st)
    assert st.trvs[A].knob_pending == {"hvac_mode": "off"}
    restored, _ = logic().handle(
        RestoreRoom(20.0, RoomMode.HEAT, Preset.NONE), AFTER, st
    )
    assert restored.trvs[A].knob_pending == {}


def test_leaving_the_window_phase_on_an_echo_waits_for_a_pending_resync() -> None:
    st = started_with_setpoint(21.0)
    st, _ = logic().handle(reported(A, 12.0), T0 + timedelta(seconds=20), st)
    st, _ = logic().handle(SetRoomSetpoint(23.0), T0 + timedelta(seconds=30), st)
    st = with_trv(st, A, resync_pending={"setpoint": 23.0})
    st, cmds = logic().handle(reported(A, 21.0), T0 + timedelta(seconds=60), st)
    assert st.trvs[A].note is None
    assert executes(cmds) == []
