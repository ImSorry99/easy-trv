from dataclasses import replace
from datetime import timedelta

import pytest

from custom_components.easy_trv.core.model import (
    ClearIssue,
    Command,
    DebounceElapsed,
    Failed,
    FireEvent,
    KnobSettled,
    Log,
    Mismatch,
    Note,
    OutdoorChanged,
    RaiseIssue,
    RestoreRoom,
    RetryDue,
    RoomConfig,
    RoomMode,
    RoomParams,
    RoomState,
    ScheduleKnobSettle,
    ScheduleRetry,
    SensorChanged,
    SetHvacMode,
    SetRoomMode,
    SetRoomSetpoint,
    SetRoomTemperature,
    SetSetpoint,
    SetSource,
    Signal,
    Start,
    Tick,
    TrvAvailabilityChanged,
    TrvHealth,
    TrvObserved,
    WindowChanged,
)
from custom_components.easy_trv.core.room_logic import RoomLogic
from tests.core.test_room_logic import (
    BOTH_AVAILABLE,
    CFG,
    T0,
    capability_of,
    executes,
    of_type,
    reading,
    result,
    with_trv,
)

A, B = "climate.a", "climate.b"
OPEN_AT = T0 + timedelta(minutes=5)
HOLD_CFG = replace(CFG, outdoor_cutoff=16.0)
# No sensor timeout: the readings of these tests are not kept fresh.
PARAMS = replace(RoomParams.defaults(), sensor_timeout=None)


def room(cfg: RoomConfig = HOLD_CFG) -> RoomLogic:
    return RoomLogic(cfg, PARAMS, capability_of=capability_of)


def heating(cfg: RoomConfig = HOLD_CFG, setpoint: float = 21.0) -> RoomState:
    """Return a started room that wants ``setpoint``."""
    logic = room(cfg)
    st, _ = logic.handle(
        Start((reading("sensor.t1", 21.3), reading("sensor.t2", 20.0)), BOTH_AVAILABLE),
        T0,
        RoomState.initial(cfg),
    )
    st, _ = logic.handle(SetRoomSetpoint(setpoint), T0, st)
    return st


def cooled(st: RoomState, value: float, at) -> RoomState:
    st, _ = room().handle(SensorChanged(reading("sensor.t1", value, at)), at, st)
    return st


def hold_events(cmds: list[Command]) -> list[FireEvent]:
    return [
        c
        for c in of_type(cmds, FireEvent)
        if c.name in ("easy_trv_window_hold", "easy_trv_outdoor_hold")
    ]


def issue_kinds(cmds: list[Command]) -> tuple[list[str], list[str]]:
    return (
        [c.kind for c in of_type(cmds, RaiseIssue)],
        [c.kind for c in of_type(cmds, ClearIssue)],
    )


def open_window(st: RoomState) -> tuple[RoomState, list[Command]]:
    return room().handle(WindowChanged(open=True), OPEN_AT, st)


# --- Start inside a hold ---------------------------------------------------


def room_writes(cmds: list[Command]) -> list[tuple[str, object]]:
    """Return the mode and setpoint writes (the start also writes settings)."""
    return [e for e in executes(cmds) if isinstance(e[1], SetHvacMode | SetSetpoint)]


def started_in_hold(
    restore: RestoreRoom | None,
    reported: dict,
    **hold,
) -> tuple[RoomLogic, RoomState, list[Command]]:
    logic = room()
    st = RoomState.initial(HOLD_CFG)
    if restore is not None:
        st, _ = logic.handle(restore, T0, st)
    st, cmds = logic.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, reported, **hold), T0, st
    )
    return logic, st, cmds


def test_start_held_off_room_turns_heating_thermostats_off_once() -> None:
    reported = {A: (21.0, "heat"), B: (21.0, "heat")}
    for hold in ({"window_open": True}, {"outdoor": 20.0}):
        logic, st, cmds = started_in_hold(
            RestoreRoom(21.0, RoomMode.OFF, None), reported, **hold
        )

        assert st.mode is RoomMode.OFF
        assert room_writes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]
        # The hold ends: an off room writes nothing.
        end = (
            WindowChanged(open=False) if "window_open" in hold else OutdoorChanged(10.0)
        )
        _, closing = logic.handle(end, OPEN_AT, st)
        assert room_writes(closing) == []


def test_start_held_heat_room_does_not_repeat_the_off_of_thermostats() -> None:
    reported = {A: (18.0, "off"), B: (18.0, "off")}
    logic, st, cmds = started_in_hold(
        RestoreRoom(21.0, RoomMode.HEAT, None), reported, window_open=True
    )

    assert st.window_open is True
    assert (st.mode, st.setpoint) == (RoomMode.HEAT, 21.0)
    assert room_writes(cmds) == []
    _, closing = logic.handle(WindowChanged(open=False), OPEN_AT, st)
    assert (A, SetHvacMode("heat")) in room_writes(closing)
    assert (A, SetSetpoint(21.0)) in room_writes(closing)


def test_start_held_heat_room_turns_a_heating_thermostat_off_once() -> None:
    reported = {A: (18.0, "off"), B: (18.0, "heat")}
    _, _, cmds = started_in_hold(
        RestoreRoom(21.0, RoomMode.HEAT, None), reported, window_open=True
    )

    assert room_writes(cmds) == [(B, SetHvacMode("off"))]


def test_start_held_without_a_restore_and_all_off_writes_nothing() -> None:
    reported = {A: (18.0, "off"), B: (18.0, "off")}
    _, st, cmds = started_in_hold(None, reported, window_open=True)

    assert st.window_open is True
    assert room_writes(cmds) == []


def test_start_held_without_a_restore_turns_heating_thermostats_off() -> None:
    reported = {A: (19.0, "heat"), B: (19.0, "heat")}
    _, st, cmds = started_in_hold(None, reported, window_open=True)

    assert st.setpoint == 19.0
    assert room_writes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]


def test_start_held_switches_thermostats_on_their_schedule_off() -> None:
    reported: dict = {A: (19.0, "auto"), B: (19.0, "heat")}
    _, st, cmds = started_in_hold(None, reported, window_open=True)

    assert room_writes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]
    assert (st.trvs[A].left_off, st.trvs[B].left_off) == (True, False)
    raised = [
        c for c in of_type(cmds, RaiseIssue) if c.kind == "schedule_thermostat_off"
    ]
    assert raised == [RaiseIssue("schedule_thermostat_off", A, {"room": "Living"})]


@pytest.mark.parametrize("restore", [RestoreRoom(21.0, RoomMode.HEAT, None), None])
@pytest.mark.parametrize("hold", [{"window_open": True}, {"outdoor": 20.0}])
def test_start_held_turns_off_a_thermostat_with_an_unknown_mode(restore, hold) -> None:
    reported = {A: (21.0, None), B: (21.0, "heat")}
    _, _, cmds = started_in_hold(restore, reported, **hold)

    assert (A, SetHvacMode("off")) in room_writes(cmds)
    assert (B, SetHvacMode("off")) in room_writes(cmds)


def test_start_held_turns_off_when_no_mode_is_known() -> None:
    reported: dict = {A: (None, None), B: (None, None)}
    _, _, cmds = started_in_hold(
        RestoreRoom(21.0, RoomMode.HEAT, None), reported, window_open=True
    )

    assert room_writes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]


def test_start_not_held_writes_no_heat_to_an_unknown_mode() -> None:
    reported: dict = {A: (21.0, None), B: (21.0, "off")}
    _, _, cmds = started_in_hold(RestoreRoom(21.0, RoomMode.HEAT, None), reported)

    assert [w for w in room_writes(cmds) if w[0] == A] == []


def test_start_with_a_warm_outdoor_holds() -> None:
    reported = {A: (19.0, "heat"), B: (19.0, "heat")}
    _, st, cmds = started_in_hold(None, reported, outdoor=20.0)

    assert st.outdoor_hold is True
    assert (A, SetHvacMode("off")) in room_writes(cmds)


# --- Window: enter and leave ---------------------------------------------


def test_window_open_turns_every_thermostat_off() -> None:
    st, cmds = open_window(heating())
    assert st.window_open is True
    assert st.window_opened_at == OPEN_AT
    assert st.window_open_value == 21.3
    assert executes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]
    assert st.trvs[A].written_recently["hvac_mode"][-1] == ("off", OPEN_AT)
    # The room keeps its wish; the climate entity shows it.
    assert (st.mode, st.setpoint) == (RoomMode.HEAT, 21.0)
    assert [(e.name, dict(e.data)) for e in hold_events(cmds)] == [
        ("easy_trv_window_hold", {"room": "Living", "state": "open"})
    ]


def test_window_close_restores_heat_and_setpoint() -> None:
    st, _ = open_window(heating())
    closed = OPEN_AT + timedelta(minutes=10)
    st, cmds = room().handle(WindowChanged(open=False), closed, st)
    assert st.window_open is False
    assert st.window_opened_at is None
    assert st.window_open_value is None
    assert executes(cmds) == [
        (A, SetHvacMode("heat")),
        (A, SetSource("external")),
        (A, SetRoomTemperature(21.3)),
        (A, SetSetpoint(21.0)),
        (B, SetHvacMode("heat")),
        (B, SetSource("external")),
        (B, SetRoomTemperature(21.3)),
        (B, SetSetpoint(21.0)),
    ]
    assert [(e.name, dict(e.data)) for e in hold_events(cmds)] == [
        ("easy_trv_window_hold", {"room": "Living", "state": "closed"})
    ]


def test_window_closing_restores_off_when_room_was_off() -> None:
    st = heating()
    st, _ = room().handle(SetRoomMode(RoomMode.OFF), T0, st)
    st, cmds = open_window(st)
    assert st.window_open is True
    assert executes(cmds) == []
    st, cmds = room().handle(WindowChanged(open=False), OPEN_AT, st)
    assert st.window_open is False
    assert st.mode is RoomMode.OFF
    assert executes(cmds) == []
    assert hold_events(cmds)[0].data["state"] == "closed"


def test_setpoint_changed_during_hold_is_applied_at_close() -> None:
    st, _ = open_window(heating())
    st, cmds = room().handle(SetRoomSetpoint(23.0), OPEN_AT, st)
    assert executes(cmds) == []
    st, cmds = room().handle(WindowChanged(open=False), OPEN_AT, st)
    assert (A, SetSetpoint(23.0)) in executes(cmds)
    assert (B, SetSetpoint(23.0)) in executes(cmds)


def test_room_switched_off_during_hold_stays_off_at_close() -> None:
    st, _ = open_window(heating())
    st, _ = room().handle(SetRoomMode(RoomMode.OFF), OPEN_AT, st)
    st, cmds = room().handle(WindowChanged(open=False), OPEN_AT, st)
    assert executes(cmds) == []


def test_no_value_writes_during_a_hold() -> None:
    st, _ = open_window(heating())
    later = OPEN_AT + timedelta(minutes=2)
    st, cmds = room().handle(
        SensorChanged(reading("sensor.t1", 19.0, later)), later, st
    )
    assert of_type(cmds, SetRoomTemperature) == []
    assert not [i for _, i in executes(cmds) if isinstance(i, SetRoomTemperature)]
    st, cmds = room().handle(DebounceElapsed(), later, st)
    assert executes(cmds) == []
    # A refresh well past the interval writes nothing either.
    st, cmds = room().handle(Tick(), later + timedelta(minutes=30), st)
    assert not [i for _, i in executes(cmds) if isinstance(i, SetRoomTemperature)]


def test_repeated_window_events_change_nothing() -> None:
    st, _ = open_window(heating())
    again, cmds = room().handle(
        WindowChanged(open=True), OPEN_AT + timedelta(minutes=1), st
    )
    assert again == st
    assert executes(cmds) == []
    assert hold_events(cmds) == []
    closed, _ = room().handle(WindowChanged(open=False), OPEN_AT, st)
    again, cmds = room().handle(WindowChanged(open=False), OPEN_AT, closed)
    assert again == closed
    assert executes(cmds) == []
    assert hold_events(cmds) == []
    assert issue_kinds(cmds) == ([], [])


def schedule_issues(cmds: list[Command]) -> tuple[list[RaiseIssue], list[ClearIssue]]:
    return (
        [c for c in of_type(cmds, RaiseIssue) if c.kind == "schedule_thermostat_off"],
        [c for c in of_type(cmds, ClearIssue) if c.kind == "schedule_thermostat_off"],
    )


def test_a_schedule_thermostat_is_switched_off_by_the_hold() -> None:
    st = with_trv(heating(), B, note=Note.SCHEDULE)
    st, cmds = open_window(st)
    assert executes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]
    assert st.trvs[B].left_off is True
    assert st.trvs[A].left_off is False
    assert st.trvs[B].written_recently["hvac_mode"][-1] == ("off", OPEN_AT)
    raised, cleared = schedule_issues(cmds)
    assert raised == [RaiseIssue("schedule_thermostat_off", B, {"room": "Living"})]
    assert cleared == []


def test_a_schedule_thermostat_stays_off_after_the_hold() -> None:
    st = with_trv(heating(), B, reported_mode="auto")
    st, _ = open_window(st)
    st, cmds = room().handle(WindowChanged(open=False), LATER, st)
    assert (A, SetHvacMode("heat")) in executes(cmds)
    assert [
        e for e in executes(cmds) if e[0] == B and isinstance(e[1], SetHvacMode)
    ] == []
    assert [
        e for e in executes(cmds) if e[0] == B and isinstance(e[1], SetSetpoint)
    ] == []
    assert st.trvs[B].left_off is True
    assert schedule_issues(cmds) == ([], [])
    # Nothing else the room writes reaches it either.
    st, cmds = room().handle(SetRoomSetpoint(23.0), LATER, st)
    assert executes(cmds) == [(A, SetSetpoint(23.0))]


def test_the_schedule_issue_clears_when_the_thermostat_reports_auto_or_heat() -> None:
    st = with_trv(heating(), B, reported_mode="auto")
    st, _ = open_window(st)
    st, _ = room().handle(WindowChanged(open=False), LATER, st)
    # The off it reports is the echo of the hold's write.
    st, cmds = room().handle(mode_report(B, "off"), LATER, st)
    assert (st.trvs[B].left_off, executes(cmds)) == (True, [])
    back, cmds = room().handle(
        TrvObserved(B, "hvac_mode", None, 1, "report"), LATER, st
    )
    assert back.trvs[B].left_off is False
    assert schedule_issues(cmds) == ([], [ClearIssue("schedule_thermostat_off", B)])
    heated, cmds = room().handle(mode_report(B, "heat"), LATER, st)
    assert heated.trvs[B].left_off is False
    assert schedule_issues(cmds) == ([], [ClearIssue("schedule_thermostat_off", B)])
    # Heat again: the thermostat is brought to the room's values.
    assert (B, SetSetpoint(21.0)) in executes(cmds)


def test_a_heat_report_of_a_schedule_thermostat_during_the_hold_is_put_right() -> None:
    st = with_trv(heating(), B, reported_mode="auto")
    st, _ = open_window(st)
    st, cmds = room().handle(mode_report(B, "heat"), LATER, st)
    assert executes(cmds) == [(B, SetHvacMode("off"))]
    assert st.trvs[B].left_off is True
    assert schedule_issues(cmds) == ([], [])


def test_a_hold_off_that_reads_back_as_the_schedule_is_tried_again() -> None:
    st = with_trv(heating(), B, reported_mode="auto")
    st, _ = open_window(st)
    st, cmds = room().handle(
        result(B, SetHvacMode("off"), Signal("schedule")), OPEN_AT, st
    )
    assert [c.capability for c in of_type(cmds, ScheduleRetry)] == ["hvac_mode"]
    assert st.trvs[B].left_off is True
    assert st.trvs[B].attempts == {"hvac_mode": 1}
    assert schedule_issues(cmds) == ([], [])
    # Outside a hold the same read-back leaves the thermostat alone.
    other, _ = room().handle(WindowChanged(open=False), LATER, st)
    other = replace(other, trvs={**other.trvs, B: replace(other.trvs[B], attempts={})})
    _, cmds = room().handle(
        result(B, SetHvacMode("off"), Signal("schedule")), LATER, other
    )
    assert of_type(cmds, ScheduleRetry) == []


def auto_report(trv: str) -> TrvObserved:
    return TrvObserved(trv, "hvac_mode", None, 1, "report")


def test_a_schedule_thermostat_reporting_auto_in_a_hold_is_switched_off() -> None:
    st = with_trv(heating(), B, reported_mode="auto")
    st, _ = open_window(st)
    st, _ = room().handle(mode_report(B, "off"), OPEN_AT + timedelta(seconds=5), st)
    again = OPEN_AT + timedelta(minutes=2)
    st, cmds = room().handle(auto_report(B), again, st)
    assert room_writes(cmds) == [(B, SetHvacMode("off"))]
    assert st.trvs[B].left_off is True
    assert st.trvs[B].written_recently["hvac_mode"][-1] == ("off", again)
    assert schedule_issues(cmds) == ([], [])
    # After the hold it stays off.
    st, cmds = room().handle(WindowChanged(open=False), LATER, st)
    assert [w for w in room_writes(cmds) if w[0] == B] == []
    assert st.trvs[B].left_off is True


def test_a_thermostat_switched_to_its_schedule_during_the_hold_is_switched_off() -> (
    None
):
    st, _ = open_window(heating())
    st, _ = room().handle(mode_report(A, "off"), OPEN_AT + timedelta(seconds=5), st)
    assert st.trvs[A].left_off is False
    st, cmds = room().handle(auto_report(A), OPEN_AT + timedelta(minutes=2), st)
    assert room_writes(cmds) == [(A, SetHvacMode("off"))]
    assert st.trvs[A].left_off is True
    raised, cleared = schedule_issues(cmds)
    assert raised == [RaiseIssue("schedule_thermostat_off", A, {"room": "Living"})]
    assert cleared == []
    st, cmds = room().handle(WindowChanged(open=False), LATER, st)
    assert [w for w in room_writes(cmds) if w[0] == A] == []
    assert (B, SetHvacMode("heat")) in room_writes(cmds)


def test_a_schedule_read_back_of_a_heat_write_during_the_hold_is_switched_off() -> None:
    st, _ = open_window(heating())
    st, cmds = room().handle(
        result(A, SetHvacMode("heat"), Signal("schedule")), OPEN_AT, st
    )
    assert room_writes(cmds) == [(A, SetHvacMode("off"))]
    assert st.trvs[A].left_off is True


def test_a_window_phase_thermostat_is_switched_off_and_resumed() -> None:
    st = heating()
    st, _ = room().handle(setpoint_report(A, 12.0), T0 + timedelta(minutes=1), st)
    assert st.trvs[A].note is Note.WINDOW_PHASE
    st, cmds = open_window(st)
    assert executes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]
    # It shows its window phase, not a schedule: no issue, nothing is left off.
    assert st.trvs[A].left_off is False
    assert schedule_issues(cmds) == ([], [])
    st, cmds = room().handle(WindowChanged(open=False), LATER, st)
    assert (A, SetHvacMode("heat")) in executes(cmds)
    assert (A, SetSetpoint(21.0)) in executes(cmds)


def test_an_off_room_still_switches_its_schedule_thermostat_off() -> None:
    st, _ = room().handle(SetRoomMode(RoomMode.OFF), T0, heating())
    st = with_trv(st, B, reported_mode="auto")
    st, cmds = open_window(st)
    assert executes(cmds) == [(B, SetHvacMode("off"))]
    assert st.trvs[B].left_off is True
    st, cmds = room().handle(WindowChanged(open=False), LATER, st)
    assert executes(cmds) == []


def test_a_room_without_climate_enters_the_hold_and_writes_nothing() -> None:
    cfg = replace(HOLD_CFG, setpoint_range=None)
    logic = RoomLogic(cfg, PARAMS, capability_of=capability_of)
    st = heating(cfg)
    st, cmds = logic.handle(WindowChanged(open=True), OPEN_AT, st)
    assert st.window_open is True
    assert executes(cmds) == []
    assert [c.level for c in of_type(cmds, Log)] == ["debug"]
    st, cmds = logic.handle(WindowChanged(open=False), OPEN_AT, st)
    assert st.window_open is False
    assert executes(cmds) == []


# --- Window: warning and stuck contact ------------------------------------


def test_window_open_long_issue_after_warn_minutes_and_cleared_on_close() -> None:
    cfg = replace(HOLD_CFG, window_check=None)
    logic = room(cfg)
    st, _ = logic.handle(WindowChanged(open=True), OPEN_AT, heating(cfg))
    st, cmds = logic.handle(Tick(), OPEN_AT + timedelta(minutes=119), st)
    assert of_type(cmds, RaiseIssue) == []
    st, cmds = logic.handle(Tick(), OPEN_AT + timedelta(minutes=121), st)
    assert of_type(cmds, RaiseIssue) == [
        RaiseIssue("window_open_long", "room1", {"room": "Living", "minutes": "121"})
    ]
    assert st.window_warned is True
    # Raised once.
    st, cmds = logic.handle(Tick(), OPEN_AT + timedelta(minutes=130), st)
    assert of_type(cmds, RaiseIssue) == []
    st, cmds = logic.handle(WindowChanged(open=False), OPEN_AT, st)
    assert ClearIssue("window_open_long", "room1") in of_type(cmds, ClearIssue)
    assert st.window_warned is False
    # Open again: the warning counts from the new opening.
    st, _ = logic.handle(WindowChanged(open=True), OPEN_AT, st)
    assert st.window_warned is False


def test_no_warning_when_window_warn_is_off() -> None:
    cfg = replace(HOLD_CFG, window_warn=None, window_check=None)
    st = heating(cfg)
    st, _ = room(cfg).handle(WindowChanged(open=True), OPEN_AT, st)
    _, cmds = room(cfg).handle(Tick(), OPEN_AT + timedelta(hours=5), st)
    assert of_type(cmds, RaiseIssue) == []


COLDER_CFG = replace(
    HOLD_CFG, outdoor_cutoff=None, window_check=None, window_warn_only_colder=True
)
WARN_AT = OPEN_AT + timedelta(minutes=121)


def outdoor(st: RoomState, celsius: float | None, cfg: RoomConfig = COLDER_CFG):
    st, _ = room(cfg).handle(OutdoorChanged(celsius), OPEN_AT, st)
    return st


def test_the_room_keeps_the_outdoor_temperature_without_a_cut_off() -> None:
    st = outdoor(heating(COLDER_CFG), 12.5)
    assert st.outdoor == 12.5
    assert st.outdoor_hold is False
    assert outdoor(st, None).outdoor is None


def test_no_warning_while_it_is_warmer_outside() -> None:
    st = outdoor(heating(COLDER_CFG), 24.0)  # the room shows 21.3
    st, _ = room(COLDER_CFG).handle(WindowChanged(open=True), OPEN_AT, st)
    st, cmds = room(COLDER_CFG).handle(Tick(), WARN_AT, st)
    assert of_type(cmds, RaiseIssue) == []
    assert st.window_warned is False
    # It gets colder outside: the warning comes then.
    st = outdoor(st, 15.0)
    st, cmds = room(COLDER_CFG).handle(Tick(), WARN_AT + timedelta(minutes=30), st)
    assert of_type(cmds, RaiseIssue) == [
        RaiseIssue("window_open_long", "room1", {"room": "Living", "minutes": "151"})
    ]


def test_warning_when_it_is_colder_outside() -> None:
    st = outdoor(heating(COLDER_CFG), 10.0)
    st, _ = room(COLDER_CFG).handle(WindowChanged(open=True), OPEN_AT, st)
    _, cmds = room(COLDER_CFG).handle(Tick(), WARN_AT, st)
    assert [i.kind for i in of_type(cmds, RaiseIssue)] == ["window_open_long"]


def test_warning_when_the_outdoor_temperature_is_unknown() -> None:
    st = heating(COLDER_CFG)
    st, _ = room(COLDER_CFG).handle(WindowChanged(open=True), OPEN_AT, st)
    _, cmds = room(COLDER_CFG).handle(Tick(), WARN_AT, st)
    assert [i.kind for i in of_type(cmds, RaiseIssue)] == ["window_open_long"]


def test_warning_regardless_of_the_outdoor_temperature_when_switched_off() -> None:
    cfg = replace(COLDER_CFG, window_warn_only_colder=False)
    st = outdoor(heating(cfg), 24.0, cfg)
    st, _ = room(cfg).handle(WindowChanged(open=True), OPEN_AT, st)
    _, cmds = room(cfg).handle(Tick(), WARN_AT, st)
    assert [i.kind for i in of_type(cmds, RaiseIssue)] == ["window_open_long"]


def test_suspect_contact_when_the_room_does_not_cool() -> None:
    st, _ = open_window(heating())
    at = OPEN_AT + timedelta(minutes=29)
    st = cooled(st, 21.2, at)  # 0.1 K only
    st, cmds = room().handle(Tick(), at, st)
    assert of_type(cmds, RaiseIssue) == []
    at = OPEN_AT + timedelta(minutes=30)
    st, cmds = room().handle(Tick(), at, st)
    assert of_type(cmds, RaiseIssue) == [
        RaiseIssue(
            "window_contact_suspect",
            "room1",
            {"room": "Living", "minutes": "30", "drop": "0.3"},
        )
    ]
    assert st.window_suspect is True
    # The hold goes on.
    assert st.window_open is True
    # Once only.
    st, cmds = room().handle(Tick(), at + timedelta(minutes=5), st)
    assert of_type(cmds, RaiseIssue) == []
    st, cmds = room().handle(WindowChanged(open=False), at, st)
    assert ClearIssue("window_contact_suspect", "room1") in of_type(cmds, ClearIssue)
    assert st.window_suspect is False


def test_no_suspect_issue_when_the_room_cools() -> None:
    st, _ = open_window(heating())
    at = OPEN_AT + timedelta(minutes=30)
    st = cooled(st, 20.9, at)  # 0.4 K below 21.3
    st, cmds = room().handle(Tick(), at, st)
    assert of_type(cmds, RaiseIssue) == []
    assert st.window_suspect is False
    # The check ran: a later warm-up raises nothing.
    st = cooled(st, 21.3, at + timedelta(minutes=10))
    _, cmds = room().handle(Tick(), at + timedelta(minutes=10), st)
    assert of_type(cmds, RaiseIssue) == []


def test_no_stuck_check_without_a_usable_sensor() -> None:
    st = heating()
    st = replace(st, readings={})
    st, _ = open_window(st)
    assert st.window_open_value is None
    _, cmds = room().handle(Tick(), OPEN_AT + timedelta(minutes=40), st)
    assert of_type(cmds, RaiseIssue) == []


def test_no_stuck_check_when_the_sensor_is_gone_at_the_check() -> None:
    st, _ = open_window(heating())
    st = replace(st, readings={})
    st, cmds = room().handle(Tick(), OPEN_AT + timedelta(minutes=40), st)
    assert of_type(cmds, RaiseIssue) == []
    assert st.window_suspect is False


def test_the_checks_are_for_windows_not_for_the_outdoor_hold() -> None:
    st = heating()
    st, _ = room().handle(OutdoorChanged(18.0), T0, st)
    _, cmds = room().handle(Tick(), T0 + timedelta(hours=5), st)
    assert of_type(cmds, RaiseIssue) == []


# --- Outdoor ----------------------------------------------------------------


def test_outdoor_hold_with_half_kelvin_deadband() -> None:
    st = heating()
    st, cmds = room().handle(OutdoorChanged(16.0), OPEN_AT, st)
    assert st.outdoor_hold is True
    assert executes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]
    assert [(e.name, dict(e.data)) for e in hold_events(cmds)] == [
        ("easy_trv_outdoor_hold", {"room": "Living", "state": "on"})
    ]
    st, cmds = room().handle(OutdoorChanged(15.6), OPEN_AT, st)
    assert st.outdoor_hold is True
    assert executes(cmds) == []
    assert hold_events(cmds) == []
    st, cmds = room().handle(OutdoorChanged(15.4), OPEN_AT, st)
    assert st.outdoor_hold is False
    assert (A, SetHvacMode("heat")) in executes(cmds)
    assert (B, SetSetpoint(21.0)) in executes(cmds)
    assert [(e.name, dict(e.data)) for e in hold_events(cmds)] == [
        ("easy_trv_outdoor_hold", {"room": "Living", "state": "off"})
    ]


def test_outdoor_below_the_cutoff_does_not_hold() -> None:
    st = heating()
    st, cmds = room().handle(OutdoorChanged(15.9), OPEN_AT, st)
    assert st.outdoor_hold is False
    assert executes(cmds) == []
    assert hold_events(cmds) == []


def test_outdoor_events_are_ignored_without_a_cutoff() -> None:
    st = heating(CFG)
    st2, cmds = room(CFG).handle(OutdoorChanged(30.0), OPEN_AT, st)
    assert st2.outdoor_hold is False
    assert executes(cmds) == []
    assert hold_events(cmds) == []


def test_window_and_outdoor_holds_are_independent() -> None:
    st = heating()
    st, cmds = room().handle(OutdoorChanged(18.0), OPEN_AT, st)
    assert len(executes(cmds)) == 2
    # The window joins a hold that is already on: no second off write.
    st, cmds = room().handle(WindowChanged(open=True), OPEN_AT, st)
    assert st.window_open
    assert st.outdoor_hold
    assert executes(cmds) == []
    # Window closes, outdoor hold remains: still nothing restored.
    st, cmds = room().handle(WindowChanged(open=False), OPEN_AT, st)
    assert st.outdoor_hold
    assert not st.window_open
    assert executes(cmds) == []
    # Window again, then the outdoor hold ends: the window keeps the hold.
    st, _ = room().handle(WindowChanged(open=True), OPEN_AT, st)
    st, cmds = room().handle(OutdoorChanged(10.0), OPEN_AT, st)
    assert not st.outdoor_hold
    assert st.window_open
    assert executes(cmds) == []
    # Last hold ends: restored.
    st, cmds = room().handle(WindowChanged(open=False), OPEN_AT, st)
    assert (A, SetHvacMode("heat")) in executes(cmds)
    assert (B, SetSetpoint(21.0)) in executes(cmds)


def test_unavailable_outdoor_sensor_ends_the_outdoor_hold() -> None:
    st = heating()
    st, _ = room().handle(OutdoorChanged(18.0), OPEN_AT, st)
    st, cmds = room().handle(OutdoorChanged(None), OPEN_AT, st)
    assert st.outdoor_hold is False
    assert (A, SetHvacMode("heat")) in executes(cmds)
    assert of_type(cmds, Log) == [
        Log("warning", "Living: outdoor sensor unavailable, outdoor cut-off ended")
    ]
    assert hold_events(cmds)[0].data["state"] == "off"
    # Unknown while not holding: nothing happens.
    st, cmds = room().handle(OutdoorChanged(None), OPEN_AT, st)
    assert of_type(cmds, Log) == []
    assert executes(cmds) == []


# --- The thermostats are off for the whole hold --------------------------------

LATER = OPEN_AT + timedelta(minutes=10)


def setpoint_report(trv: str, value: float) -> TrvObserved:
    return TrvObserved(trv, "setpoint", value, round(value * 100), "report")


def mode_report(trv: str, mode: str) -> TrvObserved:
    return TrvObserved(trv, "hvac_mode", mode, 4 if mode == "heat" else 0, "report")


def test_a_failed_off_write_is_retried_as_off_during_the_hold() -> None:
    st, _ = open_window(heating())
    st, cmds = room().handle(
        result(A, SetHvacMode("off"), Failed("timeout")), OPEN_AT, st
    )
    assert st.trvs[A].pending_retry["hvac_mode"] == SetHvacMode("off")
    st, cmds = room().handle(RetryDue(A, "hvac_mode"), LATER, st)
    assert executes(cmds) == [(A, SetHvacMode("off"))]
    assert st.trvs[A].written_recently["hvac_mode"][-1] == ("off", LATER)


def test_a_retry_pending_from_the_hold_sends_heat_after_the_close() -> None:
    st, _ = open_window(heating())
    st, _ = room().handle(result(A, SetHvacMode("off"), Failed("timeout")), OPEN_AT, st)
    st, _ = room().handle(WindowChanged(open=False), OPEN_AT, st)
    st, cmds = room().handle(RetryDue(A, "hvac_mode"), LATER, st)
    assert executes(cmds) == [(A, SetHvacMode("heat"))]


def test_a_setpoint_retry_is_still_suppressed_during_the_hold() -> None:
    st = heating()
    st, _ = room().handle(result(A, SetSetpoint(21.0), Failed("busy")), OPEN_AT, st)
    st, _ = open_window(st)
    _, cmds = room().handle(RetryDue(A, "setpoint"), LATER, st)
    assert executes(cmds) == []


def test_a_thermostat_that_returns_during_the_hold_is_turned_off() -> None:
    st = with_trv(heating(), B, available=False)
    st, cmds = open_window(st)
    assert executes(cmds) == [(A, SetHvacMode("off"))]
    st, cmds = room().handle(TrvAvailabilityChanged(B, available=True), LATER, st)
    assert executes(cmds) == [(B, SetHvacMode("off"))]
    assert st.trvs[B].resync_pending == {"hvac_mode": "off"}
    # At the end of the hold it is brought back with the others.
    _, cmds = room().handle(WindowChanged(open=False), LATER, st)
    assert (B, SetHvacMode("heat")) in executes(cmds)
    assert (B, SetSetpoint(21.0)) in executes(cmds)


def test_a_heat_report_during_the_hold_is_corrected_not_adopted() -> None:
    st, _ = open_window(heating())
    st, cmds = room().handle(mode_report(A, "heat"), LATER, st)
    assert executes(cmds) == [(A, SetHvacMode("off"))]
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert st.mode is RoomMode.HEAT
    assert st.setpoint == 21.0
    # Its off report afterwards is the echo of the correction.
    st, cmds = room().handle(mode_report(A, "off"), LATER, st)
    assert executes(cmds) == []
    # An off report that is no echo needs no correction either.
    st, cmds = room().handle(mode_report(B, "off"), LATER + timedelta(minutes=10), st)
    assert executes(cmds) == []
    assert of_type(cmds, ScheduleKnobSettle) == []


def test_leaving_the_window_phase_during_a_hold_writes_off() -> None:
    st, _ = open_window(heating())
    st, _ = room().handle(setpoint_report(A, 12.0), OPEN_AT + timedelta(minutes=1), st)
    assert st.trvs[A].note is Note.WINDOW_PHASE
    st, cmds = room().handle(setpoint_report(A, 21.0), LATER, st)
    assert st.trvs[A].note is None
    assert executes(cmds) == [(A, SetHvacMode("off"))]


def test_a_window_phase_that_ends_by_itself_during_a_hold_writes_off() -> None:
    st, _ = open_window(heating())
    start = OPEN_AT + timedelta(minutes=1)
    st, _ = room().handle(setpoint_report(A, 12.0), start, st)
    assert st.trvs[A].note is Note.WINDOW_PHASE
    st, cmds = room().handle(Tick(), start + timedelta(minutes=30), st)
    assert executes(cmds) == [(A, SetHvacMode("off"))]
    assert st.trvs[A].note is None


# --- An exhausted off write is sent again at the refresh interval ------------------


def exhausted(st: RoomState, trv: str, intent, at=OPEN_AT) -> RoomState:
    for _ in range(3):
        st, _ = room().handle(result(trv, intent, Failed("timeout")), at, st)
    assert st.trvs[trv].health is TrvHealth.ERROR
    return st


def test_an_exhausted_off_write_of_a_hold_is_sent_again_at_the_refresh() -> None:
    st, _ = open_window(heating())
    st = exhausted(st, A, SetHvacMode("off"))
    assert st.trvs[A].error_capability == "hvac_mode"
    _, cmds = room().handle(Tick(), OPEN_AT + timedelta(minutes=19), st)
    assert writes_of(cmds) == []
    again = OPEN_AT + timedelta(minutes=21)
    st, cmds = room().handle(Tick(), again, st)
    assert writes_of(cmds) == [(A, SetHvacMode("off"))]
    assert st.trvs[A].written_recently["hvac_mode"][-1] == ("off", again)
    # Once per interval, not on every tick.
    _, cmds = room().handle(Tick(), again + timedelta(minutes=1), st)
    assert writes_of(cmds) == []


def test_an_exhausted_mode_write_follows_the_room_after_the_hold() -> None:
    st, _ = open_window(heating())
    st = exhausted(st, A, SetHvacMode("off"))
    st, _ = room().handle(WindowChanged(open=False), OPEN_AT, st)
    _, cmds = room().handle(Tick(), OPEN_AT + timedelta(minutes=21), st)
    assert writes_of(cmds) == [(A, SetHvacMode("heat"))]


def test_an_exhausted_mode_write_of_an_off_room_is_off() -> None:
    st, _ = room().handle(SetRoomMode(RoomMode.OFF), T0, heating())
    st = exhausted(st, A, SetHvacMode("off"), T0)
    _, cmds = room().handle(Tick(), T0 + timedelta(minutes=21), st)
    assert writes_of(cmds) == [(A, SetHvacMode("off"))]


def test_an_exhausted_setpoint_write_is_not_sent_while_the_room_is_held() -> None:
    st = exhausted(heating(), A, SetSetpoint(21.0), T0)
    st, _ = open_window(st)
    _, cmds = room().handle(Tick(), T0 + timedelta(minutes=21), st)
    assert writes_of(cmds) == []


def test_an_exhausted_off_write_of_a_schedule_thermostat_is_sent_again() -> None:
    st = with_trv(heating(), B, note=Note.SCHEDULE)
    st, _ = open_window(st)
    st = exhausted(st, B, SetHvacMode("off"))
    _, cmds = room().handle(Tick(), OPEN_AT + timedelta(minutes=21), st)
    assert writes_of(cmds) == [(B, SetHvacMode("off"))]


def writes_of(cmds: list[Command]) -> list[tuple[str, object]]:
    return room_writes(cmds)


# --- A write the thermostat has not applied yet is no knob turn -------------------

CLOSED_AT = OPEN_AT + timedelta(minutes=9)


def adoptions(cmds: list[Command]) -> list[FireEvent]:
    return [
        c for c in of_type(cmds, FireEvent) if c.name == "easy_trv_setpoint_adopted"
    ]


def test_a_heat_that_reads_back_as_the_old_off_is_retried_not_adopted() -> None:
    st, _ = open_window(heating())
    st, _ = room().handle(mode_report(A, "off"), OPEN_AT + timedelta(seconds=2), st)
    st, _ = room().handle(WindowChanged(open=False), CLOSED_AT, st)
    stale = Mismatch(frozenset({4}), 0, observed="off")
    st, cmds = room().handle(
        result(A, SetHvacMode("heat"), stale), CLOSED_AT + timedelta(seconds=10), st
    )
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert adoptions(cmds) == []
    assert [c.capability for c in of_type(cmds, ScheduleRetry)] == ["hvac_mode"]
    assert st.trvs[A].attempts == {"hvac_mode": 1}
    assert st.trvs[A].knob_pending == {}
    assert st.mode is RoomMode.HEAT


def test_a_setpoint_changed_in_ha_during_the_hold_is_retried_not_adopted() -> None:
    st = heating()
    st, _ = room().handle(setpoint_report(A, 21.0), T0 + timedelta(minutes=1), st)
    st, _ = open_window(st)
    st, _ = room().handle(SetRoomSetpoint(23.0), OPEN_AT, st)
    st, _ = room().handle(WindowChanged(open=False), CLOSED_AT, st)
    stale = Mismatch(frozenset({2300}), 2100, observed=21.0)
    st, cmds = room().handle(
        result(A, SetSetpoint(23.0), stale), CLOSED_AT + timedelta(seconds=10), st
    )
    assert of_type(cmds, ScheduleKnobSettle) == []
    assert adoptions(cmds) == []
    assert [c.capability for c in of_type(cmds, ScheduleRetry)] == ["setpoint"]
    assert st.setpoint == 23.0


def test_a_third_value_read_back_is_still_a_knob_turn() -> None:
    st = heating()
    st, _ = room().handle(setpoint_report(A, 21.0), T0 + timedelta(minutes=1), st)
    st, _ = open_window(st)
    st, _ = room().handle(SetRoomSetpoint(23.0), OPEN_AT, st)
    st, _ = room().handle(WindowChanged(open=False), CLOSED_AT, st)
    other = Mismatch(frozenset({2300}), 2500, observed=25.0)
    st, cmds = room().handle(
        result(A, SetSetpoint(23.0), other), CLOSED_AT + timedelta(seconds=10), st
    )
    assert of_type(cmds, ScheduleRetry) == []
    assert of_type(cmds, ScheduleKnobSettle) == [ScheduleKnobSettle(A, "setpoint", 3.0)]
    assert st.trvs[A].knob_pending == {"setpoint": 25.0}


def test_a_value_reported_after_our_write_is_not_taken_for_a_stale_read_back() -> None:
    st = heating()
    st, _ = room().handle(SetRoomSetpoint(23.0), OPEN_AT, st)
    # The knob went to 21 after our write: the report is newer than the write.
    st, _ = room().handle(setpoint_report(A, 21.0), OPEN_AT + timedelta(seconds=5), st)
    seen = Mismatch(frozenset({2300}), 2100, observed=21.0)
    st, cmds = room().handle(
        result(A, SetSetpoint(23.0), seen), OPEN_AT + timedelta(seconds=10), st
    )
    assert of_type(cmds, ScheduleRetry) == []
    assert st.trvs[A].knob_pending == {"setpoint": 21.0}


# --- A knob turn during a hold changes the wish, not the mode ---------------------


def knob_turn_during_hold(st: RoomState, value: float = 23.5) -> RoomState:
    st, _ = open_window(st)
    st, _ = room().handle(
        setpoint_report(A, value), OPEN_AT + timedelta(seconds=10), st
    )
    st, cmds = room().handle(
        KnobSettled(A, "setpoint"), OPEN_AT + timedelta(seconds=20), st
    )
    assert executes(cmds) == []
    return st


def test_a_knob_turn_during_a_hold_keeps_an_off_room_off() -> None:
    st, _ = room().handle(SetRoomMode(RoomMode.OFF), T0, heating())
    st = knob_turn_during_hold(st)
    assert st.mode is RoomMode.OFF
    assert st.setpoint == 23.5
    st, cmds = room().handle(WindowChanged(open=False), LATER, st)
    assert executes(cmds) == []
    assert st.mode is RoomMode.OFF
    assert st.setpoint == 23.5


def test_a_knob_turn_during_a_hold_is_written_at_the_close_of_a_heating_room() -> None:
    st = knob_turn_during_hold(heating())
    assert st.mode is RoomMode.HEAT
    st, cmds = room().handle(WindowChanged(open=False), LATER, st)
    assert (A, SetSetpoint(23.5)) in executes(cmds)
    assert (B, SetSetpoint(23.5)) in executes(cmds)


# --- Stuck check: sensor identity, sensor loss -----------------------------------


def test_the_stuck_check_is_skipped_when_another_sensor_is_active() -> None:
    st, _ = open_window(heating())
    assert st.window_open_sensor == "sensor.t1"
    at = OPEN_AT + timedelta(minutes=30)
    # t1 is gone; t2 takes over and sits within 0.3 K of the opening value, so
    # without the identity check the contact would be called suspect.
    only_t2 = {"sensor.t2": reading("sensor.t2", 21.2, at)}
    st = replace(st, readings=only_t2)
    st, cmds = room().handle(Tick(), at, st)
    assert of_type(cmds, RaiseIssue) == []
    assert st.window_suspect is False
    assert st.window_checked is False


def test_the_stuck_check_fires_once_the_sensor_is_usable_again() -> None:
    st, _ = open_window(heating())
    readings = st.readings
    st = replace(st, readings={})
    at = OPEN_AT + timedelta(minutes=40)
    st, cmds = room().handle(Tick(), at, st)
    assert of_type(cmds, RaiseIssue) == []
    st = replace(st, readings=readings)
    st, cmds = room().handle(Tick(), at + timedelta(minutes=1), st)
    assert [c.kind for c in of_type(cmds, RaiseIssue)] == ["window_contact_suspect"]
    st, cmds = room().handle(Tick(), at + timedelta(minutes=2), st)
    assert of_type(cmds, RaiseIssue) == []


# --- Boundaries and an off room -------------------------------------------------


def test_the_outdoor_deadband_boundaries_are_exact() -> None:
    st = heating()
    st, _ = room().handle(OutdoorChanged(16.0), OPEN_AT, st)
    assert st.outdoor_hold is True
    st, _ = room().handle(OutdoorChanged(15.5), OPEN_AT, st)
    assert st.outdoor_hold is True
    st, _ = room().handle(OutdoorChanged(15.49), OPEN_AT, st)
    assert st.outdoor_hold is False


def test_warning_and_stuck_check_also_run_in_an_off_room() -> None:
    st, _ = room().handle(SetRoomMode(RoomMode.OFF), T0, heating())
    st, _ = open_window(st)
    st, cmds = room().handle(Tick(), OPEN_AT + timedelta(minutes=30), st)
    assert [c.kind for c in of_type(cmds, RaiseIssue)] == ["window_contact_suspect"]
    st, cmds = room().handle(Tick(), OPEN_AT + timedelta(minutes=120), st)
    assert [c.kind for c in of_type(cmds, RaiseIssue)] == ["window_open_long"]
