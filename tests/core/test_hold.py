from dataclasses import replace
from datetime import timedelta

from custom_components.easy_trv.core.model import (
    ClearIssue,
    Command,
    DebounceElapsed,
    FireEvent,
    Log,
    Note,
    OutdoorChanged,
    RaiseIssue,
    RoomConfig,
    RoomMode,
    RoomParams,
    RoomState,
    SensorChanged,
    SetHvacMode,
    SetRoomMode,
    SetRoomSetpoint,
    SetRoomTemperature,
    SetSetpoint,
    SetSource,
    Start,
    Tick,
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


def test_a_schedule_thermostat_gets_no_off_write() -> None:
    st = with_trv(heating(), B, note=Note.SCHEDULE)
    _, cmds = open_window(st)
    assert executes(cmds) == [(A, SetHvacMode("off"))]


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
