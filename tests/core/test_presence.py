from dataclasses import replace
from datetime import timedelta

from custom_components.easy_trv.core.model import (
    Failed,
    FireEvent,
    KnobSettled,
    Ok,
    OperatingMode,
    PresenceChanged,
    RetryDue,
    RoomConfig,
    RoomMode,
    RoomParams,
    RoomState,
    SetHvacMode,
    SetRoomOperation,
    SetRoomSetpoint,
    SetSetpoint,
    Start,
    TrvObserved,
    WindowChanged,
)
from custom_components.easy_trv.core.room_logic import RoomLogic
from custom_components.easy_trv.core.setpoint import holding
from tests.core.test_room_logic import (
    BOTH_AVAILABLE,
    CFG,
    T0,
    capability_of,
    executes,
    of_type,
    reading,
    result,
)

A, B = "climate.a", "climate.b"
LOWER_CFG = replace(CFG, away_action="setback", away_setback=3.0)
AFTER = T0 + timedelta(seconds=300)
PARAMS = replace(RoomParams.defaults(), sensor_timeout=None)


def room(cfg: RoomConfig = LOWER_CFG) -> RoomLogic:
    return RoomLogic(cfg, PARAMS, capability_of=capability_of)


def heating(cfg: RoomConfig = LOWER_CFG, setpoint: float = 21.0) -> RoomState:
    logic = room(cfg)
    st, _ = logic.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE),
        T0,
        RoomState.initial(cfg),
    )
    st, _ = logic.handle(SetRoomSetpoint(setpoint), T0, st)
    return st


def setpoints(cmds: list) -> list:
    return [(trv, i) for trv, i in executes(cmds) if isinstance(i, SetSetpoint)]


def away_events(cmds: list) -> list[FireEvent]:
    return [c for c in of_type(cmds, FireEvent) if c.name == "easy_trv_away"]


def away(st: RoomState, cfg: RoomConfig = LOWER_CFG, *, is_away: bool = True):
    """Change presence; the writes it makes arrive, as they do on a device."""
    st, cmds = room(cfg).handle(PresenceChanged(away=is_away), AFTER, st)
    for trv, intent in executes(cmds):
        if isinstance(intent, SetSetpoint | SetHvacMode):
            st, _ = room(cfg).handle(result(trv, intent, Ok()), AFTER, st)
    return st, cmds


def knob(st: RoomState, value: float, cfg: RoomConfig = LOWER_CFG):
    """Turn the knob of climate.a well after the room's last write."""
    turned = AFTER + timedelta(minutes=5)
    st, _ = room(cfg).handle(
        TrvObserved(A, "setpoint", value, round(value * 100), "report"), turned, st
    )
    return room(cfg).handle(
        KnobSettled(A, "setpoint"), turned + timedelta(seconds=3), st
    )


def test_away_writes_the_lowered_target() -> None:
    st, cmds = away(heating())
    assert setpoints(cmds) == [(A, SetSetpoint(18.0)), (B, SetSetpoint(18.0))]
    assert away_events(cmds) == [
        FireEvent("easy_trv_away", {"room": "Living", "state": "on"})
    ]
    assert (st.setpoint, st.away) == (21.0, True)


def test_home_writes_the_wish_back() -> None:
    st, _ = away(heating())
    st, cmds = away(st, is_away=False)
    assert setpoints(cmds) == [(A, SetSetpoint(21.0)), (B, SetSetpoint(21.0))]
    assert away_events(cmds) == [
        FireEvent("easy_trv_away", {"room": "Living", "state": "off"})
    ]
    assert st.away is False


def test_the_same_presence_twice_writes_nothing() -> None:
    st, _ = away(heating())
    st, cmds = away(st)
    assert setpoints(cmds) == []
    assert away_events(cmds) == []


def test_a_room_without_presence_ignores_it() -> None:
    cfg = replace(LOWER_CFG, away_action=None)
    _, cmds = away(heating(cfg), cfg)
    assert setpoints(cmds) == []
    assert away_events(cmds) == []


def test_retry_sends_the_target() -> None:
    st, _ = away(heating())
    st, _ = room().handle(result(A, SetSetpoint(18.0), Failed("busy")), AFTER, st)
    assert "setpoint" in st.trvs[A].pending_retry
    later = AFTER + timedelta(hours=1)
    _, cmds = room().handle(RetryDue(A, "setpoint"), later, st)
    assert setpoints(cmds) == [(A, SetSetpoint(18.0))]


def test_ha_setpoint_while_away_is_an_override() -> None:
    st, _ = away(heating())
    st, cmds = room().handle(SetRoomSetpoint(22.0), AFTER, st)
    assert st.away_override is True
    assert setpoints(cmds) == [(A, SetSetpoint(22.0)), (B, SetSetpoint(22.0))]
    assert away_events(cmds) == [
        FireEvent("easy_trv_away", {"room": "Living", "state": "off"})
    ]
    st, cmds = away(st, is_away=False)
    assert st.away_override is False
    assert setpoints(cmds) == []


def test_an_ha_setpoint_at_home_is_no_override() -> None:
    st, _ = room().handle(SetRoomSetpoint(22.0), AFTER, heating())
    assert st.away_override is False


def test_knob_turn_while_lowered_is_an_override() -> None:
    st, _ = away(heating())
    st, cmds = knob(st, 23.0)
    assert (st.setpoint, st.away_override) == (23.0, True)
    assert setpoints(cmds) == [(B, SetSetpoint(23.0))]


def test_a_stale_wish_while_lowered_is_drift() -> None:
    st, _ = away(heating())
    st, cmds = knob(st, 21.0)
    assert (st.setpoint, st.away_override) == (21.0, False)
    assert setpoints(cmds) == [(A, SetSetpoint(18.0))]


def test_the_lowered_value_reported_back_is_no_turn() -> None:
    st, _ = away(heating())
    st, cmds = knob(st, 18.0)
    assert (st.setpoint, st.away_override) == (21.0, False)
    assert setpoints(cmds) == []


def test_presence_does_nothing_in_frost_or_off() -> None:
    for selected in (OperatingMode.FROST, OperatingMode.OFF):
        st, _ = room().handle(SetRoomOperation(selected), T0, heating())
        st, cmds = away(st)
        assert setpoints(cmds) == []
        assert not [i for _, i in executes(cmds) if isinstance(i, SetHvacMode)]
        assert away_events(cmds) == []
        assert st.away is True


def test_heat_selected_while_away_starts_lowered() -> None:
    st, _ = room().handle(SetRoomOperation(OperatingMode.OFF), T0, heating())
    st, _ = away(st)
    st, cmds = room().handle(SetRoomOperation(OperatingMode.HEAT), AFTER, st)
    assert setpoints(cmds) == [(A, SetSetpoint(18.0)), (B, SetSetpoint(18.0))]


def test_start_away_starts_lowered() -> None:
    reported = {A: (21.0, "heat"), B: (21.0, "heat")}
    _, cmds = room().handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, reported, away=True),
        T0,
        RoomState.initial(LOWER_CFG),
    )
    assert setpoints(cmds) == [(A, SetSetpoint(18.0)), (B, SetSetpoint(18.0))]


# --- away switches the room off ---------------------------------------------

OFF_CFG = replace(CFG, away_action="off")


def modes(cmds: list) -> list:
    return [(trv, i) for trv, i in executes(cmds) if isinstance(i, SetHvacMode)]


def test_away_off_switches_every_thermostat_off() -> None:
    st, cmds = away(heating(OFF_CFG), OFF_CFG)
    assert modes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]
    assert setpoints(cmds) == []
    assert holding(st)
    assert st.mode is RoomMode.HEAT
    assert away_events(cmds) == [
        FireEvent("easy_trv_away", {"room": "Living", "state": "on"})
    ]


def test_home_resumes_heat_and_the_wish() -> None:
    st, _ = away(heating(OFF_CFG), OFF_CFG)
    st, cmds = away(st, OFF_CFG, is_away=False)
    assert (A, SetHvacMode("heat")) in modes(cmds)
    assert (B, SetHvacMode("heat")) in modes(cmds)
    assert setpoints(cmds) == [(A, SetSetpoint(21.0)), (B, SetSetpoint(21.0))]
    assert not holding(st)


def test_window_closes_during_away_off_room_stays_off() -> None:
    st, _ = away(heating(OFF_CFG), OFF_CFG)
    st, _ = room(OFF_CFG).handle(WindowChanged(open=True), AFTER, st)
    st, cmds = room(OFF_CFG).handle(WindowChanged(open=False), AFTER, st)
    assert modes(cmds) == []
    assert setpoints(cmds) == []
    assert holding(st)


def test_away_ends_while_window_open_room_stays_off() -> None:
    st, _ = room(OFF_CFG).handle(WindowChanged(open=True), AFTER, heating(OFF_CFG))
    st, _ = away(st, OFF_CFG)
    st, cmds = away(st, OFF_CFG, is_away=False)
    assert modes(cmds) == []
    assert holding(st)


def test_ha_setpoint_ends_the_away_hold() -> None:
    st, _ = away(heating(OFF_CFG), OFF_CFG)
    st, cmds = room(OFF_CFG).handle(SetRoomSetpoint(22.0), AFTER, st)
    assert st.away_override is True
    assert not holding(st)
    assert (A, SetHvacMode("heat")) in modes(cmds)
    assert setpoints(cmds) == [(A, SetSetpoint(22.0)), (B, SetSetpoint(22.0))]


def test_knob_turn_ends_the_away_hold() -> None:
    st, _ = away(heating(OFF_CFG), OFF_CFG)
    st, cmds = knob(st, 23.0, OFF_CFG)
    assert (st.setpoint, st.away_override) == (23.0, True)
    assert not holding(st)
    assert (B, SetHvacMode("heat")) in modes(cmds)


def test_a_setpoint_during_a_window_hold_only_changes_the_wish() -> None:
    st, _ = room(OFF_CFG).handle(WindowChanged(open=True), AFTER, heating(OFF_CFG))
    st, _ = away(st, OFF_CFG)
    st, cmds = room(OFF_CFG).handle(SetRoomSetpoint(22.0), AFTER, st)
    assert st.setpoint == 22.0
    assert holding(st)
    assert modes(cmds) == []


def test_heat_selected_during_the_away_hold_stays_off() -> None:
    st, _ = room(OFF_CFG).handle(
        SetRoomOperation(OperatingMode.OFF), T0, heating(OFF_CFG)
    )
    st, _ = away(st, OFF_CFG)
    st, cmds = room(OFF_CFG).handle(SetRoomOperation(OperatingMode.HEAT), AFTER, st)
    assert modes(cmds) == []
    assert holding(st)


def test_start_away_off_starts_held() -> None:
    reported = {A: (21.0, "heat"), B: (21.0, "heat")}
    st, cmds = room(OFF_CFG).handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE, reported, away=True),
        T0,
        RoomState.initial(OFF_CFG),
    )
    assert holding(st)
    assert modes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]


# --- review fixes ------------------------------------------------------------


def mode_knob(st: RoomState, mode: str, cfg: RoomConfig = OFF_CFG):
    """Switch the mode of climate.a at the device, well after the last write."""
    turned = AFTER + timedelta(minutes=5)
    raw = 4 if mode == "heat" else 0
    st, _ = room(cfg).handle(
        TrvObserved(A, "hvac_mode", mode, raw, "report"), turned, st
    )
    return room(cfg).handle(
        KnobSettled(A, "hvac_mode"), turned + timedelta(seconds=3), st
    )


def test_frost_during_the_away_hold_switches_the_thermostats_on() -> None:
    st, _ = away(heating(OFF_CFG), OFF_CFG)
    st, cmds = room(OFF_CFG).handle(SetRoomOperation(OperatingMode.FROST), AFTER, st)
    assert not holding(st)
    assert (A, SetHvacMode("heat")) in modes(cmds)
    assert (B, SetHvacMode("heat")) in modes(cmds)
    assert setpoints(cmds) == [(A, SetSetpoint(7.0)), (B, SetSetpoint(7.0))]


def test_heat_from_frost_while_away_off_switches_the_thermostats_off() -> None:
    st, _ = room(OFF_CFG).handle(
        SetRoomOperation(OperatingMode.FROST), T0, heating(OFF_CFG)
    )
    st, _ = away(st, OFF_CFG)
    assert not holding(st)
    st, cmds = room(OFF_CFG).handle(SetRoomOperation(OperatingMode.HEAT), AFTER, st)
    assert holding(st)
    assert modes(cmds) == [(A, SetHvacMode("off")), (B, SetHvacMode("off"))]
    assert setpoints(cmds) == []


def test_a_knob_mode_change_into_the_away_hold_keeps_the_room_off() -> None:
    st, _ = room(OFF_CFG).handle(
        SetRoomOperation(OperatingMode.OFF), T0, heating(OFF_CFG)
    )
    st, _ = away(st, OFF_CFG)
    st, cmds = mode_knob(st, "heat")
    assert holding(st)
    assert setpoints(cmds) == []
    assert (A, SetHvacMode("off")) in modes(cmds)
    assert (A, SetHvacMode("heat")) not in modes(cmds)


def test_a_knob_turn_that_wakes_a_room_while_away_is_an_override() -> None:
    for cfg in (LOWER_CFG, OFF_CFG):
        st, _ = room(cfg).handle(SetRoomOperation(OperatingMode.OFF), T0, heating(cfg))
        st, _ = away(st, cfg)
        st, cmds = knob(st, 23.0, cfg)
        assert (st.setpoint, st.away_override) == (23.0, True), cfg.away_action
        assert not holding(st)
        assert (B, SetSetpoint(23.0)) in setpoints(cmds)
        assert (B, SetHvacMode("heat")) in modes(cmds)


def test_a_stale_lowered_value_after_coming_home_is_not_adopted() -> None:
    st, _ = away(heating())
    later = AFTER + timedelta(hours=3)
    st, _ = room().handle(PresenceChanged(away=False), later, st)
    reported = later + timedelta(minutes=3)
    st, _ = room().handle(
        TrvObserved(A, "setpoint", 18.0, 1800, "report"), reported, st
    )
    st, cmds = room().handle(
        KnobSettled(A, "setpoint"), reported + timedelta(seconds=3), st
    )
    assert st.setpoint == 21.0
    assert (B, SetSetpoint(18.0)) not in setpoints(cmds)


def test_the_same_ha_setpoint_while_lowered_is_no_override() -> None:
    st, _ = away(heating())
    st, cmds = room().handle(SetRoomSetpoint(21.0), AFTER, st)
    assert st.away_override is False
    assert (A, SetSetpoint(21.0)) not in setpoints(cmds)


def test_the_same_ha_setpoint_during_the_away_hold_keeps_it() -> None:
    st, _ = away(heating(OFF_CFG), OFF_CFG)
    st, cmds = room(OFF_CFG).handle(SetRoomSetpoint(21.0), AFTER, st)
    assert st.away_override is False
    assert holding(st)
    assert modes(cmds) == []
