"""The room's schedule: off lowers the room, a block temperature sets the wish."""

from dataclasses import replace

from custom_components.easy_trv.core.model import (
    Ok,
    OperatingMode,
    RestoreRoom,
    RoomConfig,
    RoomMode,
    RoomState,
    ScheduleChanged,
    SetHvacMode,
    SetRoomOperation,
    SetRoomSetpoint,
    SetSetpoint,
    Start,
    WindowChanged,
)
from custom_components.easy_trv.core.room_logic import RoomLogic
from custom_components.easy_trv.core.room_state import HeatingState, heating_state
from custom_components.easy_trv.core.room_state import state_facts as facts_of
from custom_components.easy_trv.core.setpoint import target
from tests.core.test_presence import AFTER, away, knob, setpoints
from tests.core.test_room_logic import (
    BOTH_AVAILABLE,
    CFG,
    DEFAULTS,
    T0,
    capability_of,
    executes,
    reading,
)
from tests.core.test_room_logic import (
    result as result_of,
)

A, B = "climate.a", "climate.b"
SCHED_CFG = replace(CFG, schedule_setback=3.0)
PARAMS = replace(DEFAULTS, sensor_timeout=None)


def room(cfg: RoomConfig = SCHED_CFG) -> RoomLogic:
    return RoomLogic(cfg, PARAMS, capability_of=capability_of)


def heating(
    cfg: RoomConfig = SCHED_CFG, setpoint: float = 21.0, *, schedule_on: bool = True
) -> RoomState:
    logic = room(cfg)
    st, _ = logic.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, schedule_on=schedule_on),
        T0,
        RoomState.initial(cfg),
    )
    st, _ = logic.handle(SetRoomSetpoint(setpoint), T0, st)
    return st


def schedule(
    st: RoomState,
    on: bool,  # noqa: FBT001
    temperature: float | None = None,
    cfg: RoomConfig = SCHED_CFG,
):
    """Change the schedule; the writes it makes arrive, as they do on a device."""
    st, cmds = room(cfg).handle(ScheduleChanged(on, temperature), AFTER, st)
    for trv, intent in executes(cmds):
        if isinstance(intent, SetSetpoint | SetHvacMode):
            st, _ = room(cfg).handle(result_of(trv, intent, Ok()), AFTER, st)
    return st, cmds


def state_of(st: RoomState, cfg: RoomConfig = SCHED_CFG) -> HeatingState:
    facts = facts_of(
        st,
        cfg,
        AFTER,
        PARAMS.refresh,
        window_pending=False,
        silent_contacts=frozenset(),
    )
    return heating_state(facts)[0]


def test_off_writes_the_lowered_target() -> None:
    st, cmds = schedule(heating(), on=False)
    assert setpoints(cmds) == [(A, SetSetpoint(18.0)), (B, SetSetpoint(18.0))]
    assert (st.setpoint, st.schedule_off) == (21.0, True)
    assert state_of(st) is HeatingState.SETBACK_SCHEDULE


def test_on_writes_the_wish_back() -> None:
    st, _ = schedule(heating(), on=False)
    st, cmds = schedule(st, on=True)
    assert setpoints(cmds) == [(A, SetSetpoint(21.0)), (B, SetSetpoint(21.0))]
    assert state_of(st) is HeatingState.HEATING


def test_the_same_schedule_twice_writes_nothing() -> None:
    st, _ = schedule(heating(), on=False)
    _, cmds = schedule(st, on=False)
    assert setpoints(cmds) == []


def test_a_room_without_schedule_ignores_it() -> None:
    _, cmds = schedule(heating(CFG), on=False, cfg=CFG)
    assert setpoints(cmds) == []


def test_a_block_temperature_becomes_the_wish_once() -> None:
    st, cmds = schedule(heating(), on=True, temperature=22.5)
    assert (st.setpoint, st.setpoint_source) == (22.5, "schedule")
    assert setpoints(cmds) == [(A, SetSetpoint(22.5)), (B, SetSetpoint(22.5))]
    # A target set by hand lasts until the next block.
    st, _ = room().handle(SetRoomSetpoint(20.0), AFTER, st)
    st, cmds = schedule(st, on=True, temperature=22.5)
    assert st.setpoint == 20.0
    assert setpoints(cmds) == []
    # The next block (after off) sets it again.
    st, _ = schedule(st, on=False)
    st, cmds = schedule(st, on=True, temperature=22.5)
    assert st.setpoint == 22.5
    assert setpoints(cmds) == [(A, SetSetpoint(22.5)), (B, SetSetpoint(22.5))]


def test_a_block_temperature_is_clamped() -> None:
    st, _ = schedule(heating(), on=True, temperature=99.0)
    assert st.setpoint == SCHED_CFG.setpoint_range[1]  # type: ignore[index]


def test_ha_setpoint_while_off_is_an_override_until_on() -> None:
    st, _ = schedule(heating(), on=False)
    st, cmds = room().handle(SetRoomSetpoint(22.0), AFTER, st)
    assert st.schedule_override is True
    assert setpoints(cmds) == [(A, SetSetpoint(22.0)), (B, SetSetpoint(22.0))]
    st, cmds = schedule(st, on=True)
    assert st.schedule_override is False
    assert setpoints(cmds) == []
    st, cmds = schedule(st, on=False)
    assert setpoints(cmds) == [(A, SetSetpoint(19.0)), (B, SetSetpoint(19.0))]


def test_ha_setpoint_while_on_is_no_override() -> None:
    st, _ = room().handle(SetRoomSetpoint(22.0), AFTER, heating())
    assert st.schedule_override is False


def test_knob_turn_while_off_is_an_override() -> None:
    st, _ = schedule(heating(), on=False)
    st, cmds = knob(st, 23.0, SCHED_CFG)
    assert (st.setpoint, st.schedule_override) == (23.0, True)
    assert setpoints(cmds) == [(B, SetSetpoint(23.0))]


def test_away_and_schedule_off_take_the_larger_setback() -> None:
    cfg = replace(
        SCHED_CFG, away_action="setback", away_setback=2.0, schedule_setback=4.0
    )
    st, _ = schedule(heating(cfg), on=False, cfg=cfg)
    st, cmds = away(st, cfg)
    assert target(cfg, st) == 17.0
    assert setpoints(cmds) == []
    cfg_away_more = replace(cfg, away_setback=5.0)
    assert target(cfg_away_more, st) == 16.0


def test_the_setback_stops_at_frost_protection() -> None:
    cfg = replace(SCHED_CFG, schedule_setback=15.0)
    st, _ = schedule(heating(cfg), on=False, cfg=cfg)
    assert target(cfg, st) == cfg.frost_temperature


def test_schedule_does_nothing_in_frost_or_off() -> None:
    for selected in (OperatingMode.FROST, OperatingMode.OFF):
        st, _ = room().handle(SetRoomOperation(selected), T0, heating())
        st, cmds = schedule(st, on=False)
        assert setpoints(cmds) == []
        assert st.schedule_off is True


def test_a_block_during_frost_is_where_frost_returns_to() -> None:
    st, _ = room().handle(SetRoomOperation(OperatingMode.FROST), T0, heating())
    st, cmds = schedule(st, on=True, temperature=22.0)
    assert setpoints(cmds) == []
    assert st.frost_return == 22.0
    st, cmds = room().handle(SetRoomOperation(OperatingMode.HEAT), AFTER, st)
    assert setpoints(cmds) == [(A, SetSetpoint(22.0)), (B, SetSetpoint(22.0))]


def test_a_block_while_off_is_remembered() -> None:
    st, _ = room().handle(SetRoomOperation(OperatingMode.OFF), T0, heating())
    st, cmds = schedule(st, on=True, temperature=22.0)
    assert setpoints(cmds) == []
    assert (st.mode, st.setpoint) == (RoomMode.OFF, 22.0)


def test_window_hold_ends_into_the_schedule() -> None:
    cfg = replace(SCHED_CFG, window_contacts=("binary_sensor.w",))
    st = heating(cfg)
    st, _ = room(cfg).handle(WindowChanged(open=True), AFTER, st)
    st, cmds = schedule(st, on=False, cfg=cfg)
    assert setpoints(cmds) == []
    st, cmds = room(cfg).handle(WindowChanged(open=False), AFTER, st)
    assert (A, SetSetpoint(18.0)) in setpoints(cmds)


def test_start_with_schedule_off_starts_lowered() -> None:
    reported = {A: (21.0, "heat"), B: (21.0, "heat")}
    _, cmds = room().handle(
        Start(
            (reading("sensor.t1", 21.3),),
            BOTH_AVAILABLE,
            reported,
            schedule_on=False,
        ),
        T0,
        RoomState.initial(SCHED_CFG),
    )
    assert setpoints(cmds) == [(A, SetSetpoint(18.0)), (B, SetSetpoint(18.0))]


def test_start_keeps_the_restored_wish_over_the_block() -> None:
    logic = room()
    st, _ = logic.handle(
        RestoreRoom(20.0, RoomMode.HEAT, None), T0, RoomState.initial(SCHED_CFG)
    )
    st, _ = logic.handle(
        Start(
            (reading("sensor.t1", 21.3),),
            BOTH_AVAILABLE,
            schedule_temperature=22.0,
        ),
        T0,
        st,
    )
    assert (st.setpoint, st.schedule_temperature) == (20.0, 22.0)
    st, _ = schedule(st, on=True, temperature=22.0)
    assert st.setpoint == 20.0


def test_a_restored_override_survives_only_while_off() -> None:
    restore = RestoreRoom(22.0, RoomMode.HEAT, None, schedule_override=True)
    for schedule_on, kept in ((False, True), (True, False)):
        logic = room()
        st, _ = logic.handle(restore, T0, RoomState.initial(SCHED_CFG))
        st, _ = logic.handle(
            Start(
                (reading("sensor.t1", 21.3),),
                BOTH_AVAILABLE,
                schedule_on=schedule_on,
            ),
            T0,
            st,
        )
        assert st.schedule_override is kept
