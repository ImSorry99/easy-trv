from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.easy_trv.core.model import (
    EVENT_FAILSAFE,
    EVENT_RECOVERED,
    EVENT_WRITE_FAILED,
    INTENT_NAMES,
    SETTING_INTENTS,
    ClearIssue,
    Command,
    DebounceElapsed,
    Execute,
    Failed,
    FireEvent,
    Intent,
    Log,
    Mismatch,
    Ok,
    OperationResult,
    Outcome,
    RaiseIssue,
    RetryDue,
    RoomConfig,
    RoomParams,
    RoomState,
    RoomStatus,
    ScheduleDebounce,
    ScheduleRetry,
    SensorChanged,
    SensorReading,
    SetAdaptiveMode,
    SetFrostTemperature,
    SetHysteresis,
    SetRoomTemperature,
    SetSource,
    SetStatus,
    SetWindowDetection,
    Signal,
    Source,
    Start,
    Tick,
    TrvAvailabilityChanged,
    TrvConfig,
    TrvHealth,
    Unsupported,
)
from custom_components.easy_trv.core.room_logic import RoomLogic

CFG = RoomConfig(
    "room1",
    "Living",
    (
        TrvConfig("climate.a", "00:11", (1, 3, 0)),
        TrvConfig("climate.b", "00:22", (1, 3, 0)),
    ),
    ("sensor.t1", "sensor.t2"),
    "on",
    0.4,
    "leave",
    7.0,
    setpoint_range=(5.0, 30.0, 0.5),
    window_contacts=(),
    window_warn=timedelta(minutes=120),
    window_check=timedelta(minutes=30),
    window_check_drop=0.3,
    outdoor_cutoff=None,
)
T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
BOTH_AVAILABLE = {"climate.a": True, "climate.b": True}


def reading(eid: str, value: float | None, at: datetime = T0) -> SensorReading:
    return SensorReading(eid, value, at)


def capability_of(intent: Intent) -> str:
    return INTENT_NAMES[type(intent)].removeprefix("set_")


def logic() -> RoomLogic:
    return RoomLogic(CFG, RoomParams.defaults(), capability_of=capability_of)


def executes(cmds: list[Command]) -> list[tuple[str, Intent]]:
    return [(c.trv, c.intent) for c in cmds if isinstance(c, Execute)]


def debounces(cmds: list[Command]) -> list[ScheduleDebounce]:
    return [c for c in cmds if isinstance(c, ScheduleDebounce)]


def started() -> tuple[RoomState, list[Command]]:
    return logic().handle(
        Start((reading("sensor.t1", 21.3), reading("sensor.t2", 20.0)), BOTH_AVAILABLE),
        T0,
        RoomState.initial(CFG),
    )


def verified_value_write(st: RoomState, at: datetime) -> RoomState:
    return replace(st, last_value_write=at)


def with_trv(st: RoomState, entity_id: str, **changes: object) -> RoomState:
    trvs = {**st.trvs, entity_id: replace(st.trvs[entity_id], **changes)}
    return replace(st, trvs=trvs)


TEMP = SetRoomTemperature(21.3)
UNSUPPORTED = Unsupported("hysteresis", "1.3.0", "1.2.1")


def result(trv: str, intent: Intent, outcome: Outcome) -> OperationResult:
    return OperationResult(trv, intent, capability_of(intent), outcome)


def of_type[C](cmds: list[Command], kind: type[C]) -> list[C]:
    return [c for c in cmds if isinstance(c, kind)]


def errored(trv: str) -> RoomState:
    """Return a started room whose ``trv`` failed three room-temperature writes."""
    st, _ = started()
    for _ in range(3):
        st, _ = logic().handle(result(trv, TEMP, Failed("timeout")), T0, st)
    return st


HYSTERESIS = SetHysteresis(0.4)
FROST = SetFrostTemperature(7.0)


def hysteresis_errored(trv: str = "climate.a") -> RoomState:
    """Return a started room whose ``trv`` failed three hysteresis writes."""
    st, _ = started()
    for _ in range(3):
        st, _ = logic().handle(result(trv, HYSTERESIS, Failed("busy")), T0, st)
    return st


def event_data(trv: str, ieee: str) -> dict[str, str]:
    return {
        "room": "Living",
        "room_id": "room1",
        "trv_entity_id": trv,
        "ieee": ieee,
    }


# --- Start ---------------------------------------------------------------


def test_start_applies_settings_then_external_and_value() -> None:
    st, cmds = started()
    assert executes(cmds)[:6] == [
        ("climate.a", SetWindowDetection(enabled=True)),
        ("climate.a", SetHysteresis(0.4)),
        ("climate.a", SetFrostTemperature(7.0)),
        ("climate.b", SetWindowDetection(enabled=True)),
        ("climate.b", SetHysteresis(0.4)),
        ("climate.b", SetFrostTemperature(7.0)),
    ]
    assert executes(cmds)[6:] == [
        ("climate.a", SetSource("external")),
        ("climate.b", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.b", SetRoomTemperature(21.3)),
    ]
    assert st.source == Source.EXTERNAL
    assert st.active_sensor == "sensor.t1"
    assert st.last_written == 21.3
    assert all(trv.settings_applied for trv in st.trvs.values())
    assert isinstance(cmds[-1], SetStatus)


def test_start_without_usable_sensor_sets_internal_and_degraded() -> None:
    st, cmds = logic().handle(
        Start((reading("sensor.t1", None), reading("sensor.t2", None)), BOTH_AVAILABLE),
        T0,
        RoomState.initial(CFG),
    )
    assert [i for _, i in executes(cmds) if isinstance(i, SetSource)] == [
        SetSource("internal")
    ] * 2
    assert st.source == Source.INTERNAL
    assert st.active_sensor is None
    assert cmds[-1].status == RoomStatus.DEGRADED


def test_priority_falls_through_to_second_sensor() -> None:
    st, _ = logic().handle(
        Start((reading("sensor.t1", None), reading("sensor.t2", 20.0)), {}),
        T0,
        RoomState.initial(CFG),
    )
    assert st.active_sensor == "sensor.t2"


def test_out_of_range_value_is_unusable() -> None:
    st, _ = logic().handle(
        Start((reading("sensor.t1", 100.0), reading("sensor.t2", None)), {}),
        T0,
        RoomState.initial(CFG),
    )
    assert st.source == Source.INTERNAL


def test_room_without_sensors_never_touches_source() -> None:
    cfg = replace(CFG, temp_sensors=())
    st, cmds = RoomLogic(
        cfg, RoomParams.defaults(), capability_of=capability_of
    ).handle(Start((), {}), T0, RoomState.initial(cfg))
    assert not any(
        isinstance(i, (SetSource, SetRoomTemperature)) for _, i in executes(cmds)
    )
    assert cmds[-1].status == RoomStatus.OK
    assert st.source == Source.NONE


def test_settings_are_only_emitted_when_configured() -> None:
    cfg = replace(CFG, window_detection="leave", adaptive_mode="leave")
    _, cmds = RoomLogic(cfg, RoomParams.defaults(), capability_of=capability_of).handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE), T0, RoomState.initial(cfg)
    )
    sent = {type(i) for _, i in executes(cmds) if isinstance(i, SETTING_INTENTS)}
    # The hysteresis and the frost temperature are always written.
    assert sent == {SetHysteresis, SetFrostTemperature}


def test_settings_are_applied_only_once() -> None:
    st, _ = started()
    _, cmds = logic().handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE), T0, st
    )
    assert not any(isinstance(i, SETTING_INTENTS) for _, i in executes(cmds))


def test_window_detection_off_is_sent_as_disabled() -> None:
    cfg = replace(CFG, window_detection="off", temp_sensors=())
    _, cmds = RoomLogic(cfg, RoomParams.defaults(), capability_of=capability_of).handle(
        Start((), BOTH_AVAILABLE), T0, RoomState.initial(cfg)
    )
    assert executes(cmds) == [
        ("climate.a", SetWindowDetection(enabled=False)),
        ("climate.a", SetHysteresis(0.4)),
        ("climate.a", SetFrostTemperature(7.0)),
        ("climate.b", SetWindowDetection(enabled=False)),
        ("climate.b", SetHysteresis(0.4)),
        ("climate.b", SetFrostTemperature(7.0)),
    ]


def test_start_skips_unavailable_trv_and_degrades() -> None:
    st, cmds = logic().handle(
        Start((reading("sensor.t1", 21.3),), {"climate.a": True, "climate.b": False}),
        T0,
        RoomState.initial(CFG),
    )
    assert executes(cmds)
    assert all(trv == "climate.a" for trv, _ in executes(cmds))
    assert not st.trvs["climate.b"].available
    assert st.trvs["climate.b"].health == TrvHealth.UNAVAILABLE
    assert cmds[-1].status == RoomStatus.DEGRADED


@pytest.mark.parametrize(
    ("raw", "written"), [(21.34, 21.3), (21.35, 21.4), (21.36, 21.4), (20.0, 20.0)]
)
def test_written_value_is_rounded_to_a_tenth(raw: float, written: float) -> None:
    st, cmds = logic().handle(
        Start((reading("sensor.t1", raw),), BOTH_AVAILABLE), T0, RoomState.initial(CFG)
    )
    assert SetRoomTemperature(written) in [i for _, i in executes(cmds)]
    assert st.last_written == written


# --- Usable sensors ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "age", "expected"),
    [
        (21.3, timedelta(0), True),
        (0.0, timedelta(0), True),
        (99.9, timedelta(0), True),
        (-0.1, timedelta(0), False),
        (100.0, timedelta(0), False),
        (float("nan"), timedelta(0), False),
        (None, timedelta(0), False),
        (21.3, timedelta(minutes=120), True),
        (21.3, timedelta(minutes=120, seconds=1), False),
    ],
)
def test_usable_checks_range_and_timeout(
    value: float | None, age: timedelta, *, expected: bool
) -> None:
    assert logic().usable(reading("sensor.t1", value), T0 + age) is expected


def test_reading_without_timestamp_is_unusable_when_timeout_is_set() -> None:
    assert not logic().usable(SensorReading("sensor.t1", 21.3, None), T0)


def test_timeout_can_be_disabled() -> None:
    params = replace(RoomParams.defaults(), sensor_timeout=None)
    room = RoomLogic(CFG, params, capability_of=capability_of)
    assert room.usable(SensorReading("sensor.t1", 21.3, None), T0)
    assert room.usable(reading("sensor.t1", 21.3), T0 + timedelta(days=30))


def test_active_sensor_returns_first_usable_reading() -> None:
    st, _ = started()
    active = logic().active_sensor(st, T0)
    assert active == reading("sensor.t1", 21.3)
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", None)), T0, st)
    assert logic().active_sensor(st, T0) == reading("sensor.t2", 20.0)
    st, _ = logic().handle(SensorChanged(reading("sensor.t2", None)), T0, st)
    assert logic().active_sensor(st, T0) is None


# --- Sensor changes and debounce -----------------------------------------


def test_small_change_below_delta_does_not_write() -> None:
    st, _ = started()
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.35)), T0, st)
    assert not any(isinstance(c, ScheduleDebounce) for c in cmds)
    assert not st.debounce_pending


@pytest.mark.parametrize(
    ("before", "after", "arms"),
    [
        (21.3, 21.4, True),  # exactly min_delta
        (21.1, 21.2, True),  # exactly min_delta, binary float noise below 0.1
        (21.3, 21.2, True),
        (21.3, 21.34, False),
        (21.3, 21.3, False),
    ],
)
def test_delta_threshold_is_inclusive(
    before: float, after: float, *, arms: bool
) -> None:
    st, _ = logic().handle(
        Start((reading("sensor.t1", before),), BOTH_AVAILABLE),
        T0,
        RoomState.initial(CFG),
    )
    _, cmds = logic().handle(SensorChanged(reading("sensor.t1", after)), T0, st)
    assert bool(debounces(cmds)) is arms


def test_change_debounces_then_writes_current_value() -> None:
    st, _ = started()
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.5)), T0, st)
    assert debounces(cmds) == [ScheduleDebounce(5.0)]
    assert executes(cmds) == []
    assert st.debounce_pending
    st, cmds = logic().handle(
        SensorChanged(reading("sensor.t1", 21.7)), T0, st
    )  # re-arm
    assert debounces(cmds) == [ScheduleDebounce(5.0)]
    st, cmds = logic().handle(DebounceElapsed(), T0 + timedelta(seconds=5), st)
    assert executes(cmds) == [
        ("climate.a", SetRoomTemperature(21.7)),
        ("climate.b", SetRoomTemperature(21.7)),
    ]
    assert not st.debounce_pending
    assert st.last_written == 21.7


def test_first_value_is_always_debounced_when_nothing_was_written() -> None:
    st, _ = started()
    st = replace(
        st, last_written=None, readings={}
    )  # sensor.t1 reports for the first time
    _, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.3)), T0, st)
    assert debounces(cmds) == [ScheduleDebounce(5.0)]


def test_unchanged_report_keeps_debounce_and_refreshes_reading() -> None:
    st, _ = started()
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.8)), T0, st)
    assert debounces(cmds) == [ScheduleDebounce(5.0)]
    later = T0 + timedelta(seconds=3)
    st, cmds = logic().handle(
        SensorChanged(reading("sensor.t1", 21.8, at=later)), later, st
    )
    # Same value, newer last_reported: nothing to re-arm, nothing to write.
    assert [type(c) for c in cmds] == [SetStatus]
    assert st.debounce_pending
    assert st.readings["sensor.t1"].last_reported == later


def test_genuine_second_change_after_a_report_still_rearms_the_debounce() -> None:
    st, _ = started()
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 21.8)), T0, st)
    later = T0 + timedelta(seconds=3)
    st, _ = logic().handle(
        SensorChanged(reading("sensor.t1", 21.8, at=later)), later, st
    )
    later = T0 + timedelta(seconds=4)
    _, cmds = logic().handle(
        SensorChanged(reading("sensor.t1", 22.4, at=later)), later, st
    )
    assert debounces(cmds) == [ScheduleDebounce(5.0)]


def test_value_after_a_gap_counts_as_changed() -> None:
    st, _ = started()
    # sensor.t1 drops out (sensor.t2 takes over), then comes back with the
    # value it had before: that is a change relative to the stored reading.
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", None)), T0, st)
    st, _ = logic().handle(DebounceElapsed(), T0, st)
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.3)), T0, st)
    assert debounces(cmds) == [ScheduleDebounce(5.0)]


def test_report_that_hands_the_value_back_to_the_priority_sensor_is_debounced() -> None:
    st, _ = started()
    # sensor.t1 stops reporting; sensor.t2 keeps the room alive.
    at = T0 + timedelta(minutes=100)
    st, _ = logic().handle(SensorChanged(reading("sensor.t2", 20.0, at=at)), at, st)
    # The periodic refresh kept the value fresh all along; this tick is about
    # the sensor timeout only.
    st = verified_value_write(st, at=T0 + timedelta(minutes=125))
    at = T0 + timedelta(minutes=130)
    st, cmds = logic().handle(Tick(), at, st)
    assert st.active_sensor == "sensor.t2"
    assert debounces(cmds) == [ScheduleDebounce(5.0)]
    st, _ = logic().handle(DebounceElapsed(), at, st)
    assert st.last_written == 20.0
    # sensor.t1 reports again with its old value: it is the priority sensor
    # once more, so its value must be written even though it did not change.
    at = T0 + timedelta(minutes=131)
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.3, at=at)), at, st)
    assert st.active_sensor == "sensor.t1"
    assert debounces(cmds) == [ScheduleDebounce(5.0)]


def test_debounce_elapsed_without_usable_sensor_writes_nothing() -> None:
    st, _ = started()
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 22.0)), T0, st)
    st = replace(st, readings={})
    st, cmds = logic().handle(DebounceElapsed(), T0, st)
    assert executes(cmds) == []
    assert not st.debounce_pending


def test_debounce_write_uses_the_priority_sensor_not_the_last_changed_one() -> None:
    st, _ = started()
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 22.0)), T0, st)
    st, _ = logic().handle(SensorChanged(reading("sensor.t2", 25.0)), T0, st)
    _, cmds = logic().handle(DebounceElapsed(), T0, st)
    assert executes(cmds) == [
        ("climate.a", SetRoomTemperature(22.0)),
        ("climate.b", SetRoomTemperature(22.0)),
    ]


def test_a_chatty_secondary_sensor_does_not_starve_the_active_sensors_write() -> None:
    st, _ = started()
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.8)), T0, st)
    armed = debounces(cmds)
    assert armed == [ScheduleDebounce(5.0)]
    # sensor.t2 is not the active sensor, but reports a new value every second.
    for second in range(1, 16):
        at = T0 + timedelta(seconds=second)
        chatty = reading("sensor.t2", 20.0 + second / 10, at=at)
        st, cmds = logic().handle(SensorChanged(chatty), at, st)
        armed += debounces(cmds)
    assert armed == [ScheduleDebounce(5.0)]
    assert st.debounce_pending
    st, cmds = logic().handle(DebounceElapsed(), T0 + timedelta(seconds=15), st)
    assert executes(cmds) == [
        ("climate.a", SetRoomTemperature(21.8)),
        ("climate.b", SetRoomTemperature(21.8)),
    ]


def test_a_secondary_sensor_alone_never_arms_the_debounce() -> None:
    st, _ = started()
    # The active value (21.3) is off the last written one, so only the sensor
    # that reported decides whether the debounce is armed.
    st = replace(st, last_written=20.0)
    st, cmds = logic().handle(SensorChanged(reading("sensor.t2", 25.0)), T0, st)
    assert debounces(cmds) == []
    assert not st.debounce_pending
    assert st.readings["sensor.t2"].value == 25.0


# --- Refresh -------------------------------------------------------------


def test_refresh_resends_after_interval() -> None:
    st, _ = started()
    st = verified_value_write(st, at=T0)
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=19), st)
    assert executes(cmds) == []
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.b", SetRoomTemperature(21.3)),
    ]


def test_refresh_sends_the_current_value_and_remembers_it() -> None:
    st, _ = started()
    st = verified_value_write(st, at=T0)
    at = T0 + timedelta(minutes=19)
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 22.0, at=at)), at, st)
    assert executes(cmds) == []
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [
        ("climate.a", SetRoomTemperature(22.0)),
        ("climate.b", SetRoomTemperature(22.0)),
    ]
    assert st.last_written == 22.0


def test_refresh_fires_after_an_unverified_write() -> None:
    # The first value write never verified: the thermostat is in error and
    # ``last_value_write`` stays unset. The refresh interval still retries it.
    st, _ = started()
    st, _ = logic().handle(result("climate.a", SetSource("external"), Ok()), T0, st)
    for _ in range(3):
        st, _ = logic().handle(result("climate.a", TEMP, Failed("timeout")), T0, st)
    assert st.last_value_write is None
    assert st.trvs["climate.a"].health == TrvHealth.ERROR

    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=19), st)
    assert executes(cmds) == []
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert ("climate.a", SetRoomTemperature(21.3)) in executes(cmds)
    # The refresh interval is the floor: no retry on every tick.
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=21), st)
    assert executes(cmds) == []
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=40), st)
    assert ("climate.a", SetRoomTemperature(21.3)) in executes(cmds)


def test_refresh_waits_for_the_interval_after_the_value_write_was_issued() -> None:
    st, _ = started()
    assert st.last_value_issued == T0
    assert st.last_value_write is None
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=19), st)
    assert executes(cmds) == []


def test_refresh_counts_from_the_newer_of_issued_and_verified_write() -> None:
    st, _ = started()
    st = verified_value_write(st, at=T0 + timedelta(minutes=10))
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=29), st)
    assert executes(cmds) == []
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=30), st)
    assert executes(cmds) != []


def test_refresh_never_starts_without_any_value_write() -> None:
    st, _ = started()
    st = replace(st, last_value_issued=None)
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=60), st)
    assert executes(cmds) == []


def test_refresh_only_applies_to_external_source() -> None:
    st, _ = started()
    st = replace(
        verified_value_write(st, at=T0), source=Source.INTERNAL, active_sensor=None
    )
    st = replace(st, readings={})
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=60), st)
    assert executes(cmds) == []


def test_refresh_skips_unavailable_trv() -> None:
    st, _ = started()
    st = with_trv(verified_value_write(st, at=T0), "climate.a", available=False)
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [("climate.b", SetRoomTemperature(21.3))]


def test_refresh_still_drives_a_trv_with_an_unsupported_capability() -> None:
    st, _ = started()
    st = with_trv(
        verified_value_write(st, at=T0),
        "climate.b",
        health=TrvHealth.UNSUPPORTED,
        unsupported=frozenset({"hysteresis"}),
    )
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.b", SetRoomTemperature(21.3)),
    ]


def test_refresh_skips_a_trv_that_lacks_the_capability_being_written() -> None:
    st, _ = started()
    st = with_trv(
        verified_value_write(st, at=T0),
        "climate.b",
        health=TrvHealth.UNSUPPORTED,
        unsupported=frozenset({"room_temperature"}),
    )
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [("climate.a", SetRoomTemperature(21.3))]


# --- Source lost and regained --------------------------------------------


def test_sensor_timeout_loses_source_then_regains() -> None:
    st, _ = started()
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=121), st)
    assert [i for _, i in executes(cmds)] == [SetSource("internal")] * 2
    assert st.source == Source.INTERNAL
    assert st.active_sensor is None
    later = T0 + timedelta(minutes=121)
    st, cmds = logic().handle(
        SensorChanged(reading("sensor.t1", 21.0, at=later)), later, st
    )
    assert executes(cmds)[:2] == [
        ("climate.a", SetSource("external")),
        ("climate.b", SetSource("external")),
    ]
    assert executes(cmds)[2:] == [
        ("climate.a", SetRoomTemperature(21.0)),
        ("climate.b", SetRoomTemperature(21.0)),
    ]
    assert st.source == Source.EXTERNAL
    assert st.active_sensor == "sensor.t1"
    assert st.last_written == 21.0
    assert debounces(cmds) == []


def test_source_is_lost_once() -> None:
    st, _ = started()
    st, _ = logic().handle(Tick(), T0 + timedelta(minutes=121), st)
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=122), st)
    assert executes(cmds) == []


def test_losing_all_sensors_by_report_sets_internal_and_degrades() -> None:
    st, _ = started()
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", None)), T0, st)
    assert st.source == Source.EXTERNAL
    assert st.active_sensor == "sensor.t2"
    assert [i for _, i in executes(cmds) if isinstance(i, SetSource)] == []
    st, cmds = logic().handle(SensorChanged(reading("sensor.t2", None)), T0, st)
    assert executes(cmds) == [
        ("climate.a", SetSource("internal")),
        ("climate.b", SetSource("internal")),
    ]
    assert st.source == Source.INTERNAL
    assert st.active_sensor is None
    assert cmds[-1].status == RoomStatus.DEGRADED


def test_tick_regains_source_without_a_second_refresh_write() -> None:
    st, _ = started()
    st = replace(
        verified_value_write(st, at=T0 - timedelta(hours=1)),
        source=Source.INTERNAL,
        active_sensor=None,
    )
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=1), st)
    assert executes(cmds) == [
        ("climate.a", SetSource("external")),
        ("climate.b", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.b", SetRoomTemperature(21.3)),
    ]
    assert st.source == Source.EXTERNAL


def test_tick_updates_active_sensor_when_priority_sensor_times_out() -> None:
    st, _ = started()
    at = T0 + timedelta(minutes=100)
    st, _ = logic().handle(SensorChanged(reading("sensor.t2", 20.0, at=at)), at, st)
    # The periodic refresh kept the value fresh all along; this tick is about
    # the sensor timeout only.
    st = verified_value_write(st, at=T0 + timedelta(minutes=115))
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=121), st)
    assert st.source == Source.EXTERNAL
    assert st.active_sensor == "sensor.t2"
    assert executes(cmds) == []
    assert debounces(cmds) == [ScheduleDebounce(5.0)]


def test_tick_does_not_rearm_a_pending_debounce() -> None:
    st, _ = started()
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 22.0)), T0, st)
    _, cmds = logic().handle(Tick(), T0 + timedelta(seconds=30), st)
    assert debounces(cmds) == []


def test_regain_skips_unavailable_trv() -> None:
    st, _ = started()
    st, _ = logic().handle(Tick(), T0 + timedelta(minutes=121), st)
    st = with_trv(st, "climate.a", available=False)
    later = T0 + timedelta(minutes=121)
    _, cmds = logic().handle(
        SensorChanged(reading("sensor.t1", 21.0, at=later)), later, st
    )
    assert executes(cmds) == [
        ("climate.b", SetSource("external")),
        ("climate.b", SetRoomTemperature(21.0)),
    ]


# --- Status --------------------------------------------------------------


def test_status_ok_with_external_source_reports_attributes() -> None:
    st, cmds = started()
    status = cmds[-1]
    assert isinstance(status, SetStatus)
    assert status.status == RoomStatus.OK
    assert status.attributes == {
        "active_sensor": "sensor.t1",
        "source": "external",
        "trv_health": {"climate.a": "ok", "climate.b": "ok"},
        "last_error": None,
    }
    assert st.source == Source.EXTERNAL


def test_status_error_wins_and_reports_first_error_text() -> None:
    st, _ = started()
    st = with_trv(st, "climate.a", health=TrvHealth.UNAVAILABLE)
    st = with_trv(st, "climate.b", health=TrvHealth.ERROR, last_error="boom")
    _, cmds = logic().handle(Tick(), T0, st)
    status = cmds[-1]
    assert isinstance(status, SetStatus)
    assert status.status == RoomStatus.ERROR
    assert status.attributes["trv_health"] == {
        "climate.a": "unavailable",
        "climate.b": "error",
    }
    assert status.attributes["last_error"] == "boom"


@pytest.mark.parametrize("health", [TrvHealth.UNAVAILABLE, TrvHealth.UNSUPPORTED])
def test_status_degraded_for_unavailable_or_unsupported_trv(health: TrvHealth) -> None:
    st, _ = started()
    st = with_trv(st, "climate.a", health=health)
    _, cmds = logic().handle(Tick(), T0, st)
    assert cmds[-1].status == RoomStatus.DEGRADED


def test_status_degraded_while_failsafe_is_recent() -> None:
    st, _ = started()
    st = with_trv(st, "climate.a", failsafe_seen_at=T0)
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=19), st)
    assert cmds[-1].status == RoomStatus.DEGRADED
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=21), st)
    assert cmds[-1].status == RoomStatus.OK


# --- Contract ------------------------------------------------------------


def test_every_call_ends_with_exactly_one_status() -> None:
    st, cmds = started()
    results = [cmds]
    for event, now in (
        (SensorChanged(reading("sensor.t1", 22.5)), T0),
        (SensorChanged(reading("sensor.t1", None)), T0),
        (DebounceElapsed(), T0),
        (Tick(), T0 + timedelta(minutes=200)),
    ):
        st, cmds = logic().handle(event, now, st)
        results.append(cmds)
    for cmds in results:
        assert isinstance(cmds[-1], SetStatus)
        assert sum(isinstance(c, SetStatus) for c in cmds) == 1


def test_state_is_never_mutated() -> None:
    before, _ = started()
    snapshot = replace(before)
    logic().handle(SensorChanged(reading("sensor.t1", 25.0)), T0, before)
    logic().handle(Tick(), T0 + timedelta(minutes=200), before)
    assert before == snapshot
    assert dict(before.readings) == dict(snapshot.readings)


# --- Operation results: retries, issues, recovery ------------------------


def test_failed_write_retries_with_backoff_then_raises_issue() -> None:
    st, _ = started()
    it = SetRoomTemperature(21.3)
    st, cmds = logic().handle(
        OperationResult("climate.a", it, "room_temperature", Failed("timeout")), T0, st
    )
    assert [c for c in cmds if isinstance(c, ScheduleRetry)] == [
        ScheduleRetry("climate.a", "room_temperature", 5.0)
    ]
    st, cmds = logic().handle(RetryDue("climate.a", "room_temperature"), T0, st)
    assert executes(cmds) == [("climate.a", it)]
    st, cmds = logic().handle(
        OperationResult("climate.a", it, "room_temperature", Failed("timeout")), T0, st
    )
    assert [c.seconds for c in cmds if isinstance(c, ScheduleRetry)] == [15.0]
    st, cmds = logic().handle(
        OperationResult(
            "climate.a",
            it,
            "room_temperature",
            Mismatch(frozenset({2130}), 2100),
        ),
        T0,
        st,
    )
    assert any(
        isinstance(c, RaiseIssue)
        and c.kind == "unreachable"
        and c.placeholders["attempts"] == "3"
        for c in cmds
    )
    assert any(isinstance(c, FireEvent) and c.name == EVENT_WRITE_FAILED for c in cmds)
    assert cmds[-1].status == RoomStatus.ERROR
    assert st.trvs["climate.a"].health == TrvHealth.ERROR


def test_recovery_clears_issue_and_fires_event() -> None:
    st = errored("climate.a")  # helper: three failures
    st, cmds = logic().handle(
        OperationResult(
            "climate.a", SetRoomTemperature(21.3), "room_temperature", Ok()
        ),
        T0,
        st,
    )
    assert any(isinstance(c, ClearIssue) for c in cmds)
    assert any(isinstance(c, FireEvent) and c.name == EVENT_RECOVERED for c in cmds)
    assert cmds[-1].status == RoomStatus.OK


def test_failsafe_signal_reenables_without_counting_attempt() -> None:
    st, _ = started()
    st, cmds = logic().handle(
        OperationResult(
            "climate.b",
            SetRoomTemperature(21.3),
            "room_temperature",
            Signal("failsafe"),
        ),
        T0,
        st,
    )
    assert executes(cmds) == [
        ("climate.b", SetSource("external")),
        ("climate.b", SetRoomTemperature(21.3)),
    ]
    assert any(isinstance(c, FireEvent) and c.name == EVENT_FAILSAFE for c in cmds)
    assert st.trvs["climate.b"].attempts == {}
    assert cmds[-1].status == RoomStatus.DEGRADED


def test_unsupported_raises_firmware_issue_no_retry() -> None:
    st, _ = started()
    st, cmds = logic().handle(
        OperationResult(
            "climate.a",
            SetHysteresis(0.4),
            "hysteresis",
            Unsupported("hysteresis", "1.3.0", "1.2.1"),
        ),
        T0,
        st,
    )
    assert not any(isinstance(c, ScheduleRetry) for c in cmds)
    assert any(isinstance(c, RaiseIssue) and c.kind == "firmware" for c in cmds)
    assert st.trvs["climate.a"].health == TrvHealth.UNSUPPORTED


def test_unavailable_trv_is_skipped_then_resynced() -> None:
    st, _ = started()
    st, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=False), T0, st
    )
    assert cmds[-1].status == RoomStatus.DEGRADED
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert all(t != "climate.a" for t, _ in executes(cmds))
    st, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert executes(cmds) == [
        ("climate.a", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
    ]


def test_newer_intent_supersedes_pending_retry() -> None:
    st, _ = started()
    st, _ = logic().handle(
        OperationResult(
            "climate.a", SetRoomTemperature(21.3), "room_temperature", Failed("x")
        ),
        T0,
        st,
    )
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 22.0)), T0, st)
    st, _ = logic().handle(DebounceElapsed(), T0, st)
    st, cmds = logic().handle(
        RetryDue("climate.a", "room_temperature"), T0 + timedelta(seconds=5), st
    )
    assert executes(cmds) in ([], [("climate.a", SetRoomTemperature(22.0))])  # not 21.3


def test_flapping_sensor_single_write() -> None:
    st, _ = started()
    st, c1 = logic().handle(SensorChanged(reading("sensor.t1", None)), T0, st)
    st, c2 = logic().handle(
        SensorChanged(reading("sensor.t1", 21.6)), T0 + timedelta(seconds=1), st
    )
    st, c3 = logic().handle(
        SensorChanged(reading("sensor.t1", None)), T0 + timedelta(seconds=2), st
    )
    st, c4 = logic().handle(
        SensorChanged(reading("sensor.t1", 21.6)), T0 + timedelta(seconds=3), st
    )
    st, c5 = logic().handle(DebounceElapsed(), T0 + timedelta(seconds=8), st)
    sources = [
        i
        for c in (c1, c2, c3, c4, c5)
        for _, i in executes(c)
        if isinstance(i, SetSource)
    ]
    assert sources == []  # t2 (20.0) covers the gaps: no source toggling
    assert executes(c5) == [
        ("climate.a", SetRoomTemperature(21.6)),
        ("climate.b", SetRoomTemperature(21.6)),
    ]


# --- Ok results ----------------------------------------------------------


def test_ok_clears_attempts_and_pending_retry_of_that_capability_only() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", TEMP, Failed("x")), T0, st)
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), Failed("y")), T0, st)
    st, cmds = logic().handle(result("climate.a", TEMP, Ok()), T0, st)
    trv = st.trvs["climate.a"]
    assert trv.attempts == {"hysteresis": 1}
    assert trv.pending_retry == {"hysteresis": SetHysteresis(0.4)}
    assert trv.health == TrvHealth.OK
    assert not any(isinstance(c, (ClearIssue, FireEvent, RaiseIssue)) for c in cmds)


def test_ok_for_a_temperature_write_records_the_verified_value() -> None:
    st, _ = started()
    later = T0 + timedelta(seconds=30)
    st, _ = logic().handle(
        result("climate.a", SetRoomTemperature(21.4), Ok()), later, st
    )
    assert st.last_value_write == later
    assert st.last_written == 21.4


def test_value_bookkeeping_follows_the_intent_not_the_capability_string() -> None:
    st, _ = started()
    later = T0 + timedelta(seconds=30)
    st, _ = logic().handle(
        OperationResult("climate.a", SetRoomTemperature(22.0), "custom", Ok()),
        later,
        st,
    )
    assert (st.last_written, st.last_value_write) == (22.0, later)
    st, _ = logic().handle(
        OperationResult("climate.a", SetHysteresis(0.4), "room_temperature", Ok()),
        later + timedelta(minutes=1),
        st,
    )
    assert (st.last_written, st.last_value_write) == (22.0, later)


def test_recovery_reports_issue_event_and_log_for_that_trv() -> None:
    st = errored("climate.a")
    st, cmds = logic().handle(result("climate.a", TEMP, Ok()), T0, st)
    trv = st.trvs["climate.a"]
    assert of_type(cmds, ClearIssue) == [ClearIssue("unreachable", "climate.a")]
    assert of_type(cmds, FireEvent) == [
        FireEvent(EVENT_RECOVERED, event_data("climate.a", "00:11"))
    ]
    assert [c.level for c in of_type(cmds, Log)] == ["info"]
    assert (trv.health, trv.attempts, trv.last_error) == (TrvHealth.OK, {}, None)
    assert cmds[-1].attributes["last_error"] is None


# --- An error ends with the capability that caused it ----------------------


def test_an_exhausted_settings_write_is_reapplied_on_the_next_resync() -> None:
    st, _ = started()
    for _ in range(2):
        st, _ = logic().handle(result("climate.a", HYSTERESIS, Failed("busy")), T0, st)
    st, cmds = logic().handle(result("climate.a", HYSTERESIS, Failed("busy")), T0, st)
    trv = st.trvs["climate.a"]
    assert trv.health == TrvHealth.ERROR
    assert trv.error_capability == "hysteresis"
    assert not trv.settings_applied
    assert of_type(cmds, RaiseIssue)[0].placeholders["attribute"] == "hysteresis"
    assert st.trvs["climate.b"].settings_applied

    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert [i for t, i in executes(cmds) if t == "climate.a"] == [
        SetWindowDetection(enabled=True),
        HYSTERESIS,
        FROST,
        SetSource("external"),
        SetRoomTemperature(21.3),
    ]
    assert st.trvs["climate.a"].settings_applied


def test_an_exhausted_window_detection_write_is_reapplied_too() -> None:
    st, _ = started()
    for _ in range(3):
        st, _ = logic().handle(
            result("climate.b", SetWindowDetection(enabled=True), Failed("busy")),
            T0,
            st,
        )
    assert not st.trvs["climate.b"].settings_applied
    assert st.trvs["climate.b"].error_capability == "window_detection"
    assert st.trvs["climate.a"].settings_applied


@pytest.mark.parametrize(
    ("failing", "verified"),
    [
        (HYSTERESIS, TEMP),
        (TEMP, HYSTERESIS),
        (TEMP, SetSource("external")),
    ],
)
def test_a_verified_write_of_another_capability_does_not_end_the_error(
    failing: Intent, verified: Intent
) -> None:
    st, _ = started()
    for _ in range(3):
        st, _ = logic().handle(result("climate.a", failing, Failed("busy")), T0, st)
    st, cmds = logic().handle(result("climate.a", verified, Ok()), T0, st)
    trv = st.trvs["climate.a"]
    assert trv.health == TrvHealth.ERROR
    assert trv.last_error == "busy"
    assert trv.error_capability == capability_of(failing)
    assert of_type(cmds, ClearIssue) == []
    assert of_type(cmds, FireEvent) == []
    assert [c.level for c in of_type(cmds, Log)] == ["debug"]
    assert cmds[-1].status == RoomStatus.ERROR


def test_the_capability_that_failed_clears_the_error_when_it_verifies() -> None:
    st = hysteresis_errored()
    st, _ = logic().handle(result("climate.a", TEMP, Ok()), T0, st)
    st, cmds = logic().handle(result("climate.a", HYSTERESIS, Ok()), T0, st)
    trv = st.trvs["climate.a"]
    assert of_type(cmds, ClearIssue) == [ClearIssue("unreachable", "climate.a")]
    assert [c.name for c in of_type(cmds, FireEvent)] == [EVENT_RECOVERED]
    assert (trv.health, trv.last_error, trv.error_capability) == (
        TrvHealth.OK,
        None,
        None,
    )
    assert cmds[-1].status == RoomStatus.OK


def test_an_outage_and_a_return_do_not_forget_a_failed_setting() -> None:
    st = hysteresis_errored()
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert ("climate.a", HYSTERESIS) in executes(cmds)
    # The value write that follows the resync verifies, the setting does not yet.
    st, cmds = logic().handle(result("climate.a", TEMP, Ok()), T0, st)
    assert of_type(cmds, ClearIssue) == []
    assert of_type(cmds, FireEvent) == []
    assert st.trvs["climate.a"].error_capability == "hysteresis"
    st, cmds = logic().handle(result("climate.a", HYSTERESIS, Ok()), T0, st)
    assert of_type(cmds, ClearIssue) == [ClearIssue("unreachable", "climate.a")]
    assert [c.name for c in of_type(cmds, FireEvent)] == [EVENT_RECOVERED]
    assert st.trvs["climate.a"].health == TrvHealth.OK


def test_a_second_failure_keeps_the_capability_that_raised_the_issue() -> None:
    st = errored("climate.a")
    for _ in range(3):
        st, cmds = logic().handle(
            result("climate.a", HYSTERESIS, Failed("busy")), T0, st
        )
    trv = st.trvs["climate.a"]
    assert trv.error_capability == "room_temperature"
    assert not trv.settings_applied
    assert not any(isinstance(c, (RaiseIssue, FireEvent)) for c in cmds)


def test_a_late_failure_after_error_and_outage_is_not_reported_again() -> None:
    st = errored("climate.a")
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    assert st.trvs["climate.a"].health == TrvHealth.UNAVAILABLE
    st, cmds = logic().handle(result("climate.a", TEMP, Failed("late")), T0, st)
    assert not any(isinstance(c, (RaiseIssue, FireEvent, Log)) for c in cmds)
    assert st.trvs["climate.a"].error_capability == "room_temperature"


# --- A failed capability is sent again at the refresh interval --------------

SEND_AGAIN = [
    (SetSource("external"), "source"),
    (SetWindowDetection(enabled=True), "window_detection"),
    (HYSTERESIS, "hysteresis"),
    (FROST, "frost_temperature"),
]


def errored_on(intent: Intent, trv: str = "climate.a") -> RoomState:
    """Return a started room whose ``trv`` failed ``intent`` three times."""
    st, _ = started()
    for _ in range(3):
        st, _ = logic().handle(result(trv, intent, Failed("busy")), T0, st)
    return st


@pytest.mark.parametrize(("intent", "capability"), SEND_AGAIN)
def test_a_failed_capability_is_sent_again_before_the_value_at_the_refresh_interval(
    intent: Intent, capability: str
) -> None:
    st = errored_on(intent)
    assert st.trvs["climate.a"].error_capability == capability
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=19), st)
    assert executes(cmds) == []

    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [
        ("climate.a", intent),
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.b", SetRoomTemperature(21.3)),
    ]
    trv = st.trvs["climate.a"]
    assert capability not in trv.attempts
    assert capability not in trv.pending_retry
    assert trv.health == TrvHealth.ERROR
    # The refresh interval is the floor, not every tick.
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=21), st)
    assert executes(cmds) == []
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=40), st)
    assert ("climate.a", intent) in executes(cmds)


@pytest.mark.parametrize(("intent", "capability"), SEND_AGAIN)
def test_the_sent_again_capability_gets_a_fresh_set_of_attempts(
    intent: Intent, capability: str
) -> None:
    st = errored_on(intent)
    st, _ = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    at = T0 + timedelta(minutes=20)
    st, cmds = logic().handle(result("climate.a", intent, Failed("busy")), at, st)
    # Retried with the backoff instead of giving up at once.
    assert of_type(cmds, ScheduleRetry) == [ScheduleRetry("climate.a", capability, 5.0)]
    assert not any(isinstance(c, (RaiseIssue, FireEvent)) for c in cmds)


@pytest.mark.parametrize("intent", [intent for intent, _ in SEND_AGAIN])
def test_the_sent_again_capability_ends_the_error_when_it_verifies(
    intent: Intent,
) -> None:
    st = errored_on(intent)
    st, _ = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    at = T0 + timedelta(minutes=20)
    st, cmds = logic().handle(result("climate.a", intent, Ok()), at, st)
    trv = st.trvs["climate.a"]
    assert of_type(cmds, ClearIssue) == [ClearIssue("unreachable", "climate.a")]
    assert [c.name for c in of_type(cmds, FireEvent)] == [EVENT_RECOVERED]
    assert (trv.health, trv.error_capability, trv.last_error) == (
        TrvHealth.OK,
        None,
        None,
    )
    assert cmds[-1].status == RoomStatus.OK


def test_an_error_on_the_value_capability_sends_only_the_value_again() -> None:
    st = errored("climate.a")
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.b", SetRoomTemperature(21.3)),
    ]


def test_an_unavailable_thermostat_is_not_sent_the_failed_capability() -> None:
    st = errored_on(SetSource("external"))
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [("climate.b", SetRoomTemperature(21.3))]


def test_an_exhausted_value_write_does_not_postpone_the_failed_capability() -> None:
    st = errored_on(SetSource("external"))
    # The value keeps failing as well and gives up just before the refresh.
    at = T0 + timedelta(minutes=19)
    for _ in range(3):
        st, _ = logic().handle(result("climate.a", TEMP, Failed("busy")), at, st)
    assert st.trvs["climate.a"].error_capability == "source"
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert ("climate.a", SetSource("external")) in executes(cmds)


def test_a_capability_is_not_sent_again_while_its_retry_is_pending() -> None:
    st = errored_on(SetSource("external"))
    st, _ = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    late = T0 + timedelta(minutes=39, seconds=50)
    st, _ = logic().handle(
        result("climate.a", SetSource("external"), Failed("busy")), late, st
    )
    assert "source" in st.trvs["climate.a"].pending_retry
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=40), st)
    assert ("climate.a", SetSource("external")) not in executes(cmds)
    assert st.trvs["climate.a"].attempts["source"] == 1


def test_a_capability_is_not_sent_again_while_its_retries_are_running() -> None:
    st = errored_on(SetSource("external"))
    st, _ = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    late = T0 + timedelta(minutes=39, seconds=50)
    st, _ = logic().handle(
        result("climate.a", SetSource("external"), Failed("busy")), late, st
    )
    st, cmds = logic().handle(RetryDue("climate.a", "source"), late, st)
    assert executes(cmds) == [("climate.a", SetSource("external"))]
    assert "source" not in st.trvs["climate.a"].pending_retry
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=40), st)
    assert ("climate.a", SetSource("external")) not in executes(cmds)
    assert st.trvs["climate.a"].attempts["source"] == 1


def test_a_failed_source_write_is_sent_again_while_the_room_is_on_its_own_sensor() -> (
    None
):
    st, _ = logic().handle(
        Start((reading("sensor.t1", None), reading("sensor.t2", None)), BOTH_AVAILABLE),
        T0,
        RoomState.initial(CFG),
    )
    assert st.source == Source.INTERNAL
    for _ in range(3):
        st, _ = logic().handle(
            result("climate.a", SetSource("internal"), Failed("busy")), T0, st
        )
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    # No value to refresh in this state, but the failed write is still retried.
    assert executes(cmds) == [("climate.a", SetSource("internal"))]


def test_a_failed_setting_is_sent_again_in_a_room_without_sensors() -> None:
    cfg = replace(CFG, temp_sensors=())
    room = RoomLogic(cfg, RoomParams.defaults(), capability_of=capability_of)
    st, _ = room.handle(Start((), BOTH_AVAILABLE), T0, RoomState.initial(cfg))
    for _ in range(3):
        st, _ = room.handle(result("climate.a", HYSTERESIS, Failed("busy")), T0, st)
    _, cmds = room.handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [("climate.a", HYSTERESIS)]


@pytest.mark.parametrize(
    ("cfg", "capability"),
    [
        (replace(CFG, temp_sensors=()), "source"),  # source is not managed
        (replace(CFG, window_detection="leave"), "window_detection"),
        (replace(CFG, adaptive_mode="leave"), "adaptive_mode"),
        # Adaptive mode manages the hysteresis itself while the thermostat has it.
        (replace(CFG, adaptive_mode="on"), "hysteresis"),
    ],
)
def test_nothing_is_sent_again_for_a_capability_the_room_does_not_manage(
    cfg: RoomConfig, capability: str
) -> None:
    room = RoomLogic(cfg, RoomParams.defaults(), capability_of=capability_of)
    st, _ = room.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE), T0, RoomState.initial(cfg)
    )
    st = with_trv(
        st,
        "climate.a",
        health=TrvHealth.ERROR,
        last_error="busy",
        error_capability=capability,
    )
    _, cmds = room.handle(Tick(), T0 + timedelta(minutes=20), st)
    assert [
        i
        for t, i in executes(cmds)
        if t == "climate.a" and not isinstance(i, SetRoomTemperature)
    ] == []


# --- Failed and mismatched results ---------------------------------------


def test_attempts_are_counted_per_capability() -> None:
    st, _ = started()
    st, c1 = logic().handle(result("climate.a", TEMP, Failed("x")), T0, st)
    st, c2 = logic().handle(
        result("climate.a", SetHysteresis(0.4), Failed("y")), T0, st
    )
    trv = st.trvs["climate.a"]
    assert trv.attempts == {"room_temperature": 1, "hysteresis": 1}
    assert trv.pending_retry == {
        "room_temperature": TEMP,
        "hysteresis": SetHysteresis(0.4),
    }
    assert of_type(c1, ScheduleRetry) == [
        ScheduleRetry("climate.a", "room_temperature", 5.0)
    ]
    assert of_type(c2, ScheduleRetry) == [ScheduleRetry("climate.a", "hysteresis", 5.0)]


def test_backoff_clamps_to_its_last_entry() -> None:
    params = replace(RoomParams.defaults(), max_attempts=6, backoff=(5.0, 15.0))
    room = RoomLogic(CFG, params, capability_of=capability_of)
    st, _ = started()
    delays: list[float] = []
    for _ in range(5):
        st, cmds = room.handle(result("climate.a", TEMP, Failed("x")), T0, st)
        delays += [c.seconds for c in of_type(cmds, ScheduleRetry)]
    assert delays == [5.0, 15.0, 15.0, 15.0, 15.0]
    st, cmds = room.handle(result("climate.a", TEMP, Failed("x")), T0, st)
    assert of_type(cmds, ScheduleRetry) == []
    assert st.trvs["climate.a"].health == TrvHealth.ERROR


def test_exhausted_attempts_report_issue_event_and_log() -> None:
    st, _ = started()
    for _ in range(2):
        st, _ = logic().handle(result("climate.a", TEMP, Failed("timeout")), T0, st)
    st, cmds = logic().handle(result("climate.a", TEMP, Failed("timeout")), T0, st)
    assert of_type(cmds, RaiseIssue) == [
        RaiseIssue(
            "unreachable",
            "climate.a",
            {
                "room": "Living",
                "trv": "climate.a",
                "attribute": "room_temperature",
                "attempts": "3",
                "error": "timeout",
            },
        )
    ]
    assert of_type(cmds, FireEvent) == [
        FireEvent(
            EVENT_WRITE_FAILED,
            {
                **event_data("climate.a", "00:11"),
                "attribute": "room_temperature",
                "attempts": 3,
                "error": "timeout",
            },
        )
    ]
    assert [c.level for c in of_type(cmds, Log)] == ["warning"]
    assert of_type(cmds, ScheduleRetry) == []
    trv = st.trvs["climate.a"]
    assert trv.pending_retry == {}
    assert trv.last_error == "timeout"
    assert cmds[-1].attributes["last_error"] == "timeout"
    assert cmds[-1].attributes["trv_health"] == {
        "climate.a": "error",
        "climate.b": "ok",
    }


def test_mismatch_error_text_names_expected_and_actual_values() -> None:
    st, _ = started()
    mismatch = Mismatch(frozenset({2131, 2130}), None)
    for _ in range(2):
        st, _ = logic().handle(result("climate.a", TEMP, mismatch), T0, st)
    st, cmds = logic().handle(result("climate.a", TEMP, mismatch), T0, st)
    text = "mismatch: expected [2130, 2131] got None"
    assert st.trvs["climate.a"].last_error == text
    assert of_type(cmds, RaiseIssue)[0].placeholders["error"] == text


def test_failures_while_in_error_do_not_repeat_issue_event_or_log() -> None:
    st = errored("climate.a")
    st, cmds = logic().handle(result("climate.a", TEMP, Failed("again")), T0, st)
    assert not any(
        isinstance(c, (RaiseIssue, FireEvent, Log, ScheduleRetry)) for c in cmds
    )
    trv = st.trvs["climate.a"]
    assert trv.attempts == {"room_temperature": 4}
    assert trv.health == TrvHealth.ERROR
    assert trv.pending_retry == {}
    assert cmds[-1].status == RoomStatus.ERROR


def test_an_error_on_one_trv_leaves_the_others_alone() -> None:
    st = errored("climate.a")
    assert st.trvs["climate.b"].health == TrvHealth.OK
    assert st.trvs["climate.b"].attempts == {}


# --- Unsupported results -------------------------------------------------


def test_unsupported_issue_carries_the_firmware_details() -> None:
    st, _ = started()
    st, cmds = logic().handle(
        result("climate.a", SetHysteresis(0.4), UNSUPPORTED), T0, st
    )
    assert of_type(cmds, RaiseIssue) == [
        RaiseIssue(
            "firmware",
            "climate.a",
            {
                "trv": "climate.a",
                "capability": "hysteresis",
                "required": "1.3.0",
                "installed": "1.2.1",
            },
        )
    ]
    trv = st.trvs["climate.a"]
    assert trv.unsupported == frozenset({"hysteresis"})
    assert trv.attempts == {}
    assert cmds[-1].status == RoomStatus.DEGRADED


def test_unsupported_does_not_hide_an_error() -> None:
    st = errored("climate.a")
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), UNSUPPORTED), T0, st)
    assert st.trvs["climate.a"].health == TrvHealth.ERROR
    assert st.trvs["climate.a"].unsupported == frozenset({"hysteresis"})


def test_unsupported_drops_the_pending_retry_of_that_capability() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), Failed("x")), T0, st)
    st, cmds = logic().handle(
        result("climate.a", SetHysteresis(0.4), UNSUPPORTED), T0, st
    )
    assert st.trvs["climate.a"].pending_retry == {}
    assert of_type(cmds, ScheduleRetry) == []
    _, cmds = logic().handle(RetryDue("climate.a", "hysteresis"), T0, st)
    assert executes(cmds) == []


def test_a_flagged_trv_keeps_receiving_the_other_capabilities() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), UNSUPPORTED), T0, st)
    st = verified_value_write(st, T0)
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.b", SetRoomTemperature(21.3)),
    ]
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 22.0)), T0, st)
    _, cmds = logic().handle(DebounceElapsed(), T0, st)
    assert executes(cmds) == [
        ("climate.a", SetRoomTemperature(22.0)),
        ("climate.b", SetRoomTemperature(22.0)),
    ]


def test_ok_for_a_flagged_capability_clears_the_firmware_issue() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), UNSUPPORTED), T0, st)
    st, cmds = logic().handle(result("climate.a", SetHysteresis(0.4), Ok()), T0, st)
    assert of_type(cmds, ClearIssue) == [ClearIssue("firmware", "climate.a")]
    assert not of_type(cmds, FireEvent)
    trv = st.trvs["climate.a"]
    assert trv.unsupported == frozenset()
    assert trv.health == TrvHealth.OK
    assert cmds[-1].status == RoomStatus.OK


def test_clearing_one_flagged_capability_keeps_the_other_blocking() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), UNSUPPORTED), T0, st)
    other = Unsupported("window_detection", "1.3.0", "1.2.1")
    st, _ = logic().handle(
        result("climate.a", SetWindowDetection(enabled=True), other), T0, st
    )
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), Ok()), T0, st)
    trv = st.trvs["climate.a"]
    assert trv.unsupported == frozenset({"window_detection"})
    assert trv.health == TrvHealth.UNSUPPORTED


# --- Fail-safe signal ----------------------------------------------------


def test_failsafe_records_event_log_and_keeps_attempts() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.b", TEMP, Failed("x")), T0, st)
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 22.0)), T0, st)
    st, _ = logic().handle(DebounceElapsed(), T0, st)
    at = T0 + timedelta(minutes=1)
    st, cmds = logic().handle(result("climate.b", TEMP, Signal("failsafe")), at, st)
    trv = st.trvs["climate.b"]
    assert executes(cmds) == [
        ("climate.b", SetSource("external")),
        ("climate.b", SetRoomTemperature(22.0)),
    ]
    assert trv.failsafe_seen_at == at
    assert trv.attempts == {"room_temperature": 1}
    assert of_type(cmds, FireEvent) == [
        FireEvent(EVENT_FAILSAFE, event_data("climate.b", "00:22"))
    ]
    assert [c.level for c in of_type(cmds, Log)] == ["info"]
    assert st.trvs["climate.a"].failsafe_seen_at is None


def test_failsafe_is_ignored_when_the_room_is_not_on_the_external_source() -> None:
    st, _ = started()
    st = replace(st, source=Source.INTERNAL, active_sensor=None)
    st, cmds = logic().handle(result("climate.b", TEMP, Signal("failsafe")), T0, st)
    assert executes(cmds) == []
    assert not of_type(cmds, FireEvent)
    assert st.trvs["climate.b"].failsafe_seen_at is None


def test_unknown_signal_is_logged_at_debug_and_ignored() -> None:
    st, _ = started()
    before = st
    st, cmds = logic().handle(result("climate.b", TEMP, Signal("other")), T0, st)
    assert [c.level for c in of_type(cmds, Log)] == ["debug"]
    assert executes(cmds) == []
    assert not of_type(cmds, FireEvent)
    assert st == before
    assert cmds[-1].status == RoomStatus.OK


# --- Retries -------------------------------------------------------------


def test_retry_due_resends_the_pending_intent_once() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), Failed("x")), T0, st)
    st, cmds = logic().handle(RetryDue("climate.a", "hysteresis"), T0, st)
    assert executes(cmds) == [("climate.a", SetHysteresis(0.4))]
    assert st.trvs["climate.a"].pending_retry == {}
    assert st.trvs["climate.a"].attempts == {"hysteresis": 1}
    _, cmds = logic().handle(RetryDue("climate.a", "hysteresis"), T0, st)
    assert executes(cmds) == []


def test_retry_due_without_a_pending_intent_does_nothing() -> None:
    st, _ = started()
    st, cmds = logic().handle(RetryDue("climate.a", "room_temperature"), T0, st)
    assert len(cmds) == 1
    assert isinstance(cmds[0], SetStatus)


def test_retry_due_after_success_does_nothing() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", TEMP, Failed("x")), T0, st)
    st, _ = logic().handle(result("climate.a", TEMP, Ok()), T0, st)
    _, cmds = logic().handle(RetryDue("climate.a", "room_temperature"), T0, st)
    assert executes(cmds) == []


def test_newer_write_replaces_the_pending_retry_intent() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", TEMP, Failed("x")), T0, st)
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), Failed("y")), T0, st)
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 22.0)), T0, st)
    st, _ = logic().handle(DebounceElapsed(), T0, st)
    trv = st.trvs["climate.a"]
    assert trv.pending_retry == {
        "room_temperature": SetRoomTemperature(22.0),
        "hysteresis": SetHysteresis(0.4),
    }
    assert st.trvs["climate.b"].pending_retry == {}
    _, cmds = logic().handle(
        RetryDue("climate.a", "room_temperature"), T0 + timedelta(seconds=5), st
    )
    assert executes(cmds) == [("climate.a", SetRoomTemperature(22.0))]


def test_a_late_failure_of_an_older_write_retries_the_newest_value() -> None:
    st, _ = started()
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 22.0)), T0, st)
    st, cmds = logic().handle(DebounceElapsed(), T0, st)
    assert executes(cmds)[0] == ("climate.a", SetRoomTemperature(22.0))
    st, cmds = logic().handle(result("climate.a", TEMP, Failed("late")), T0, st)
    assert of_type(cmds, ScheduleRetry) == [
        ScheduleRetry("climate.a", "room_temperature", 5.0)
    ]
    assert st.trvs["climate.a"].pending_retry == {
        "room_temperature": SetRoomTemperature(22.0)
    }
    _, cmds = logic().handle(
        RetryDue("climate.a", "room_temperature"), T0 + timedelta(seconds=5), st
    )
    assert executes(cmds) == [("climate.a", SetRoomTemperature(22.0))]


def test_other_intents_are_retried_as_issued() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), Failed("x")), T0, st)
    assert st.trvs["climate.a"].pending_retry == {"hysteresis": SetHysteresis(0.4)}


def test_retry_due_for_an_unavailable_trv_is_dropped_and_resynced_later() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), Failed("x")), T0, st)
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, cmds = logic().handle(RetryDue("climate.a", "hysteresis"), T0, st)
    assert executes(cmds) == []
    assert st.trvs["climate.a"].pending_retry == {}
    assert not st.trvs["climate.a"].settings_applied
    st, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert executes(cmds)[:2] == [
        ("climate.a", SetWindowDetection(enabled=True)),
        ("climate.a", SetHysteresis(0.4)),
    ]


def test_retry_due_for_an_unavailable_trv_keeps_applied_settings() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", TEMP, Failed("x")), T0, st)
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, cmds = logic().handle(RetryDue("climate.a", "room_temperature"), T0, st)
    assert executes(cmds) == []
    assert st.trvs["climate.a"].pending_retry == {}
    assert st.trvs["climate.a"].settings_applied


# --- Availability --------------------------------------------------------


def test_unavailable_trv_gets_neither_refresh_nor_debounced_writes() -> None:
    st, _ = started()
    st = verified_value_write(st, T0)
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [("climate.b", SetRoomTemperature(21.3))]
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 22.0)), T0, st)
    _, cmds = logic().handle(DebounceElapsed(), T0, st)
    assert executes(cmds) == [("climate.b", SetRoomTemperature(22.0))]


def test_going_unavailable_keeps_attempts_pending_retries_and_flags() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", TEMP, Failed("x")), T0, st)
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), UNSUPPORTED), T0, st)
    st, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=False), T0, st
    )
    trv = st.trvs["climate.a"]
    assert not trv.available
    assert trv.health == TrvHealth.UNAVAILABLE
    assert trv.attempts == {"room_temperature": 1}
    assert trv.pending_retry == {"room_temperature": TEMP}
    assert trv.unsupported == frozenset({"hysteresis"})
    assert executes(cmds) == []
    assert cmds[-1].attributes["trv_health"]["climate.a"] == "unavailable"


def test_regaining_availability_restores_health_and_status() -> None:
    st, _ = started()
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert st.trvs["climate.a"].available
    assert st.trvs["climate.a"].health == TrvHealth.OK
    assert cmds[-1].status == RoomStatus.OK


def test_regaining_availability_resyncs_a_trv_with_an_unsupported_capability() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), UNSUPPORTED), T0, st)
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert st.trvs["climate.a"].health == TrvHealth.UNSUPPORTED
    assert executes(cmds) == [
        ("climate.a", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
    ]
    assert cmds[-1].status == RoomStatus.DEGRADED


def test_a_late_firmware_flag_does_not_hide_the_outage() -> None:
    st, _ = started()
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, _ = logic().handle(result("climate.a", SetHysteresis(0.4), UNSUPPORTED), T0, st)
    assert st.trvs["climate.a"].unsupported == frozenset({"hysteresis"})
    assert st.trvs["climate.a"].health == TrvHealth.UNAVAILABLE
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=True), T0, st)
    assert st.trvs["climate.a"].health == TrvHealth.UNSUPPORTED


def test_regaining_availability_applies_settings_that_were_missed() -> None:
    st, cmds = logic().handle(
        Start(
            (reading("sensor.t1", 21.3), reading("sensor.t2", 20.0)),
            {"climate.a": False, "climate.b": True},
        ),
        T0,
        RoomState.initial(CFG),
    )
    assert not st.trvs["climate.a"].settings_applied
    assert st.trvs["climate.b"].settings_applied
    st, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert executes(cmds) == [
        ("climate.a", SetWindowDetection(enabled=True)),
        ("climate.a", SetHysteresis(0.4)),
        ("climate.a", SetFrostTemperature(7.0)),
        ("climate.a", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
    ]
    assert st.trvs["climate.a"].settings_applied
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    _, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert executes(cmds) == [
        ("climate.a", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
    ]


def test_regaining_availability_follows_the_current_internal_source() -> None:
    st, _ = logic().handle(
        Start(
            (reading("sensor.t1", None), reading("sensor.t2", None)),
            {"climate.a": False, "climate.b": True},
        ),
        T0,
        RoomState.initial(CFG),
    )
    _, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert executes(cmds)[3:] == [("climate.a", SetSource("internal"))]


def test_regaining_availability_in_a_room_without_sensors_only_applies_settings() -> (
    None
):
    cfg = replace(CFG, temp_sensors=(), window_detection="off")
    room = RoomLogic(cfg, RoomParams.defaults(), capability_of=capability_of)
    st, _ = room.handle(
        Start((), {"climate.a": False, "climate.b": True}), T0, RoomState.initial(cfg)
    )
    _, cmds = room.handle(TrvAvailabilityChanged("climate.a", available=True), T0, st)
    assert executes(cmds) == [
        ("climate.a", SetWindowDetection(enabled=False)),
        ("climate.a", SetHysteresis(0.4)),
        ("climate.a", SetFrostTemperature(7.0)),
    ]


def test_repeated_availability_events_change_nothing() -> None:
    st = errored("climate.a")
    same, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert same == st
    assert executes(cmds) == []
    assert cmds[-1].status == RoomStatus.ERROR
    st, _ = logic().handle(TrvAvailabilityChanged("climate.b", available=False), T0, st)
    same, cmds = logic().handle(
        TrvAvailabilityChanged("climate.b", available=False), T0, st
    )
    assert same == st


def test_recovery_after_an_outage_still_clears_the_unreachable_issue() -> None:
    st = errored("climate.a")
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=True), T0, st)
    assert st.trvs["climate.a"].health == TrvHealth.OK
    st, cmds = logic().handle(result("climate.a", TEMP, Ok()), T0, st)
    assert of_type(cmds, ClearIssue) == [ClearIssue("unreachable", "climate.a")]
    assert [c.name for c in of_type(cmds, FireEvent)] == [EVENT_RECOVERED]
    assert st.trvs["climate.a"].last_error is None


# --- Unsupported capabilities never stop a thermostat ---------------------


def flagged_start(*, a_available: bool = True) -> tuple[RoomState, list[Command]]:
    """Start a room whose climate.a is already known to lack hysteresis."""
    initial = with_trv(
        RoomState.initial(CFG),
        "climate.a",
        health=TrvHealth.UNSUPPORTED,
        unsupported=frozenset({"hysteresis"}),
    )
    return logic().handle(
        Start(
            (reading("sensor.t1", 21.3), reading("sensor.t2", 20.0)),
            {"climate.a": a_available, "climate.b": True},
        ),
        T0,
        initial,
    )


def test_start_drives_a_flagged_trv_but_skips_the_flagged_capability() -> None:
    st, cmds = flagged_start()
    assert [i for t, i in executes(cmds) if t == "climate.a"] == [
        SetWindowDetection(enabled=True),
        FROST,
        SetSource("external"),
        SetRoomTemperature(21.3),
    ]
    assert [i for t, i in executes(cmds) if t == "climate.b"] == [
        SetWindowDetection(enabled=True),
        SetHysteresis(0.4),
        FROST,
        SetSource("external"),
        SetRoomTemperature(21.3),
    ]
    assert st.trvs["climate.a"].health == TrvHealth.UNSUPPORTED
    assert st.trvs["climate.a"].settings_applied
    assert cmds[-1].status == RoomStatus.DEGRADED


def test_regain_resyncs_a_flagged_trv_without_the_flagged_capability() -> None:
    st, _ = flagged_start(a_available=False)
    assert not st.trvs["climate.a"].settings_applied
    st, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert executes(cmds) == [
        ("climate.a", SetWindowDetection(enabled=True)),
        ("climate.a", SetFrostTemperature(7.0)),
        ("climate.a", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
    ]
    assert st.trvs["climate.a"].settings_applied
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    _, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert executes(cmds) == [
        ("climate.a", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
    ]


def test_source_switching_still_reaches_a_flagged_trv() -> None:
    st, _ = flagged_start()
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=121), st)
    assert executes(cmds) == [
        ("climate.a", SetSource("internal")),
        ("climate.b", SetSource("internal")),
    ]
    later = T0 + timedelta(minutes=121)
    _, cmds = logic().handle(
        SensorChanged(reading("sensor.t1", 21.0, at=later)), later, st
    )
    assert executes(cmds) == [
        ("climate.a", SetSource("external")),
        ("climate.b", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.0)),
        ("climate.b", SetRoomTemperature(21.0)),
    ]


def test_a_flagged_source_capability_is_skipped_on_every_path() -> None:
    st, _ = started()
    st = with_trv(
        st,
        "climate.a",
        health=TrvHealth.UNSUPPORTED,
        unsupported=frozenset({"source"}),
    )
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=121), st)
    assert executes(cmds) == [("climate.b", SetSource("internal"))]


# --- Fail-safe on the re-enable write itself -----------------------------


def test_failsafe_on_the_source_write_is_a_failed_attempt_not_a_reenable() -> None:
    st, _ = started()
    st, cmds = logic().handle(
        result("climate.a", SetSource("external"), Signal("failsafe")), T0, st
    )
    assert of_type(cmds, ScheduleRetry) == [ScheduleRetry("climate.a", "source", 5.0)]
    assert executes(cmds) == []
    assert not of_type(cmds, FireEvent)
    trv = st.trvs["climate.a"]
    assert trv.attempts == {"source": 1}
    assert trv.pending_retry == {"source": SetSource("external")}
    assert trv.failsafe_seen_at is None
    st, cmds = logic().handle(RetryDue("climate.a", "source"), T0, st)
    assert executes(cmds) == [("climate.a", SetSource("external"))]


def test_a_persistent_failsafe_is_bounded_with_the_shipped_profile_sequence() -> None:
    """The profile reads the fail-safe flag back on every value write."""
    room = logic()
    reenable = SetSource("external")
    seen: list[Command] = []
    st, _ = started()
    st, cmds = room.handle(result("climate.a", TEMP, Signal("failsafe")), T0, st)
    seen += cmds
    assert executes(cmds) == [("climate.a", reenable), ("climate.a", TEMP)]
    for delay in (5.0, 15.0):
        # The read-back of the re-enable's SetSource is a failed attempt ...
        st, cmds = room.handle(
            result("climate.a", reenable, Signal("failsafe")), T0, st
        )
        seen += cmds
        assert [c.seconds for c in of_type(cmds, ScheduleRetry)] == [delay]
        assert executes(cmds) == []
        # ... and so is the one of the re-enable's value write: nothing new,
        # and the pending source retry (the backoff) stays untouched.
        st, cmds = room.handle(result("climate.a", TEMP, Signal("failsafe")), T0, st)
        seen += cmds
        assert executes(cmds) == []
        assert not of_type(cmds, (FireEvent, ScheduleRetry, RaiseIssue))
        assert st.trvs["climate.a"].pending_retry == {"source": reenable}
        st, cmds = room.handle(RetryDue("climate.a", "source"), T0, st)
        seen += cmds
        assert executes(cmds) == [("climate.a", reenable)]
        # A value write while the source write is retried does not re-enable.
        st, cmds = room.handle(result("climate.a", TEMP, Signal("failsafe")), T0, st)
        seen += cmds
        assert executes(cmds) == []
    st, cmds = room.handle(result("climate.a", reenable, Signal("failsafe")), T0, st)
    seen += cmds
    assert of_type(cmds, ScheduleRetry) == []
    assert of_type(cmds, RaiseIssue) == [
        RaiseIssue(
            "unreachable",
            "climate.a",
            {
                "room": "Living",
                "trv": "climate.a",
                "attribute": "source",
                "attempts": "3",
                "error": "signal: failsafe",
            },
        )
    ]
    assert st.trvs["climate.a"].health == TrvHealth.ERROR
    assert cmds[-1].status == RoomStatus.ERROR
    # Once in error a fail-safe on a value write only leaves a trace.
    later = T0 + timedelta(minutes=1)
    st, cmds = room.handle(result("climate.a", TEMP, Signal("failsafe")), later, st)
    seen += cmds
    assert executes(cmds) == []
    assert not of_type(cmds, (FireEvent, RaiseIssue, ScheduleRetry))
    assert st.trvs["climate.a"].failsafe_seen_at == later
    assert [c.name for c in of_type(seen, FireEvent)] == [
        EVENT_FAILSAFE,
        EVENT_WRITE_FAILED,
    ]
    assert len(of_type(seen, RaiseIssue)) == 1


def test_a_failsafe_that_is_not_re_enabled_is_still_recorded() -> None:
    st, _ = started()
    st, _ = logic().handle(
        result("climate.a", SetSource("external"), Failed("x")), T0, st
    )
    at = T0 + timedelta(minutes=2)
    st, cmds = logic().handle(result("climate.a", TEMP, Signal("failsafe")), at, st)
    assert st.trvs["climate.a"].failsafe_seen_at == at
    assert [c.level for c in of_type(cmds, Log)] == ["debug"]
    assert executes(cmds) == []
    assert not of_type(cmds, FireEvent)
    assert cmds[-1].status == RoomStatus.DEGRADED


def test_failsafe_with_an_unsupported_source_only_records_it() -> None:
    st, _ = started()
    st = with_trv(
        st,
        "climate.a",
        health=TrvHealth.UNSUPPORTED,
        unsupported=frozenset({"source"}),
    )
    st, cmds = logic().handle(result("climate.a", TEMP, Signal("failsafe")), T0, st)
    assert executes(cmds) == []
    assert not of_type(cmds, FireEvent)
    assert st.trvs["climate.a"].failsafe_seen_at == T0


def test_a_late_failsafe_for_an_unavailable_trv_emits_no_writes() -> None:
    st, _ = started()
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, cmds = logic().handle(result("climate.a", TEMP, Signal("failsafe")), T0, st)
    assert executes(cmds) == []
    assert not of_type(cmds, FireEvent)
    assert st.trvs["climate.a"].failsafe_seen_at == T0
    assert st.trvs["climate.a"].health == TrvHealth.UNAVAILABLE


def test_failsafe_is_re_enabled_again_once_the_source_write_recovered() -> None:
    st, _ = started()
    reenable = SetSource("external")
    st, _ = logic().handle(result("climate.a", reenable, Signal("failsafe")), T0, st)
    st, _ = logic().handle(result("climate.a", reenable, Ok()), T0, st)
    st, cmds = logic().handle(result("climate.a", TEMP, Signal("failsafe")), T0, st)
    assert executes(cmds) == [("climate.a", reenable), ("climate.a", TEMP)]


def test_failsafe_on_a_settings_write_is_ignored() -> None:
    st, _ = started()
    before = st
    st, cmds = logic().handle(
        result("climate.a", SetHysteresis(0.4), Signal("failsafe")), T0, st
    )
    assert [c.level for c in of_type(cmds, Log)] == ["debug"]
    assert executes(cmds) == []
    assert st == before


# --- Per-thermostat settings ---------------------------------------------


def test_start_marks_settings_applied_for_every_writable_trv() -> None:
    st, _ = started()
    assert all(trv.settings_applied for trv in st.trvs.values())


def test_a_trv_with_only_the_always_written_settings_counts_as_applied() -> None:
    # Window detection and adaptive mode are left alone; the hysteresis and the
    # frost temperature are always written, so there is never "nothing" to apply.
    cfg = replace(CFG, window_detection="leave", adaptive_mode="leave")
    room = RoomLogic(cfg, RoomParams.defaults(), capability_of=capability_of)
    st, cmds = room.handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE), T0, RoomState.initial(cfg)
    )
    assert intents_for(cmds)[:2] == [
        SetHysteresis(0.4),
        SetFrostTemperature(7.0),
    ]
    assert all(trv.settings_applied for trv in st.trvs.values())


# --- Pending debounce ----------------------------------------------------


def test_debounce_elapsed_without_a_pending_debounce_is_ignored() -> None:
    st, _ = started()
    after, cmds = logic().handle(DebounceElapsed(), T0, st)
    assert after == st
    assert len(cmds) == 1
    assert isinstance(cmds[0], SetStatus)


def test_refresh_clears_the_pending_debounce() -> None:
    st, _ = started()
    st = replace(verified_value_write(st, T0), debounce_pending=True)
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds)
    assert not st.debounce_pending
    _, cmds = logic().handle(DebounceElapsed(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == []


def test_regaining_the_source_clears_the_pending_debounce() -> None:
    st, _ = started()
    st = replace(st, source=Source.INTERNAL, active_sensor=None, debounce_pending=True)
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=1), st)
    assert executes(cmds)
    assert not st.debounce_pending


# --- Contract for the remaining events -----------------------------------


@pytest.mark.parametrize(
    "event",
    [
        TrvAvailabilityChanged("climate.a", available=False),
        TrvAvailabilityChanged("climate.a", available=True),
        RetryDue("climate.a", "room_temperature"),
        OperationResult("climate.a", TEMP, "room_temperature", Ok()),
        OperationResult("climate.a", TEMP, "room_temperature", Failed("x")),
        OperationResult(
            "climate.a", TEMP, "room_temperature", Mismatch(frozenset({2130}), 1)
        ),
        OperationResult("climate.a", SetHysteresis(0.4), "hysteresis", UNSUPPORTED),
        OperationResult("climate.a", TEMP, "room_temperature", Signal("failsafe")),
        OperationResult("climate.a", TEMP, "room_temperature", Signal("other")),
    ],
)
def test_result_and_availability_events_end_with_exactly_one_status(
    event: object,
) -> None:
    st, _ = started()
    _, cmds = logic().handle(event, T0, st)  # type: ignore[arg-type]
    assert isinstance(cmds[-1], SetStatus)
    assert sum(isinstance(c, SetStatus) for c in cmds) == 1


def test_result_handling_never_mutates_the_previous_state() -> None:
    before, _ = started()
    before, _ = logic().handle(result("climate.a", TEMP, Failed("x")), T0, before)
    attempts = dict(before.trvs["climate.a"].attempts)
    pending = dict(before.trvs["climate.a"].pending_retry)
    snapshot = replace(before)
    for event in (
        result("climate.a", TEMP, Failed("y")),
        result("climate.a", TEMP, Ok()),
        result("climate.a", TEMP, UNSUPPORTED),
        RetryDue("climate.a", "room_temperature"),
        TrvAvailabilityChanged("climate.a", available=False),
    ):
        logic().handle(event, T0, before)
    assert before == snapshot
    assert dict(before.trvs["climate.a"].attempts) == attempts
    assert dict(before.trvs["climate.a"].pending_retry) == pending


# --- Adaptive mode, hysteresis and frost temperature ----------------------

ADAPTIVE_UNSUPPORTED = Unsupported("adaptive_mode", "1.4.0", "1.3.0")
READINGS = (reading("sensor.t1", 21.3), reading("sensor.t2", 20.0))


def adaptive_logic(cfg: RoomConfig) -> RoomLogic:
    return RoomLogic(cfg, RoomParams.defaults(), capability_of=capability_of)


def start_room(cfg: RoomConfig) -> tuple[RoomLogic, RoomState, list[Command]]:
    room = adaptive_logic(cfg)
    st, cmds = room.handle(Start(READINGS, BOTH_AVAILABLE), T0, RoomState.initial(cfg))
    return room, st, cmds


def intents_for(cmds: list[Command], trv: str = "climate.a") -> list[Intent]:
    return [i for t, i in executes(cmds) if t == trv]


def test_start_applies_settings_in_order_with_adaptive_on() -> None:
    cfg = replace(CFG, adaptive_mode="on", hysteresis=0.4, frost_temperature=7.0)
    _, _, cmds = start_room(cfg)
    assert intents_for(cmds)[:4] == [
        SetWindowDetection(enabled=True),
        SetAdaptiveMode(enabled=True),
        SetFrostTemperature(7.0),
        SetSource("external"),
    ]


@pytest.mark.parametrize("mode", ["off", "leave"])
def test_hysteresis_is_written_when_adaptive_is_off_or_leave(mode: str) -> None:
    cfg = replace(CFG, adaptive_mode=mode, hysteresis=0.4)
    _, _, cmds = start_room(cfg)
    expected: list[Intent] = [SetWindowDetection(enabled=True)]
    if mode == "off":
        expected.append(SetAdaptiveMode(enabled=False))
    expected += [SetHysteresis(0.4), SetFrostTemperature(7.0), SetSource("external")]
    assert intents_for(cmds)[: len(expected)] == expected


def test_adaptive_leave_writes_nothing_for_adaptive() -> None:
    _, _, cmds = start_room(replace(CFG, adaptive_mode="leave"))
    assert not any(isinstance(i, SetAdaptiveMode) for _, i in executes(cmds))


def test_frost_temperature_is_written_with_its_configured_value() -> None:
    _, _, cmds = start_room(replace(CFG, frost_temperature=9.5))
    assert SetFrostTemperature(9.5) in intents_for(cmds)
    assert SetFrostTemperature(9.5) in intents_for(cmds, "climate.b")


def test_unsupported_adaptive_mode_does_not_block_other_settings() -> None:
    cfg = replace(CFG, adaptive_mode="on")
    room, st, _ = start_room(cfg)
    st, cmds = room.handle(
        result("climate.a", SetAdaptiveMode(enabled=True), ADAPTIVE_UNSUPPORTED),
        T0,
        st,
    )
    assert st.trvs["climate.a"].health == TrvHealth.UNSUPPORTED
    assert [i.kind for i in of_type(cmds, RaiseIssue)] == ["firmware"]
    # The other settings of that thermostat keep working and verify.
    st, cmds = room.handle(result("climate.a", FROST, Ok()), T0, st)
    assert st.trvs["climate.a"].health == TrvHealth.UNSUPPORTED
    assert of_type(cmds, RaiseIssue) == []
    st = with_trv(st, "climate.a", settings_applied=False)
    _, cmds = room.handle(Start(READINGS, BOTH_AVAILABLE), T0, st)
    sent = intents_for(cmds)
    assert FROST in sent
    assert SetWindowDetection(enabled=True) in sent
    assert not any(isinstance(i, SetAdaptiveMode) for i in sent)


def test_unsupported_adaptive_mode_triggers_the_hysteresis_write() -> None:
    cfg = replace(CFG, adaptive_mode="on", hysteresis=0.4)
    room, st, start_cmds = start_room(cfg)
    assert SetHysteresis(0.4) not in intents_for(start_cmds)
    _, cmds = room.handle(
        result("climate.a", SetAdaptiveMode(enabled=True), ADAPTIVE_UNSUPPORTED),
        T0,
        st,
    )
    assert executes(cmds) == [("climate.a", SetHysteresis(0.4))]


def test_unsupported_adaptive_mode_without_adaptive_on_writes_no_extra_hysteresis() -> (
    None
):
    cfg = replace(CFG, adaptive_mode="off", hysteresis=0.4)
    room, st, _ = start_room(cfg)
    _, cmds = room.handle(
        result("climate.a", SetAdaptiveMode(enabled=False), ADAPTIVE_UNSUPPORTED),
        T0,
        st,
    )
    assert executes(cmds) == []


def test_unsupported_hysteresis_does_not_trigger_a_hysteresis_write() -> None:
    cfg = replace(CFG, adaptive_mode="on", hysteresis=0.4)
    room, st, _ = start_room(cfg)
    _, cmds = room.handle(result("climate.a", HYSTERESIS, UNSUPPORTED), T0, st)
    assert executes(cmds) == []


def test_unsupported_adaptive_hysteresis_write_waits_for_an_absent_thermostat() -> None:
    cfg = replace(CFG, adaptive_mode="on", hysteresis=0.4)
    room, st, _ = start_room(cfg)
    st, _ = room.handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, cmds = room.handle(
        result("climate.a", SetAdaptiveMode(enabled=True), ADAPTIVE_UNSUPPORTED),
        T0,
        st,
    )
    assert executes(cmds) == []
    assert not st.trvs["climate.a"].settings_applied
    _, cmds = room.handle(TrvAvailabilityChanged("climate.a", available=True), T0, st)
    assert HYSTERESIS in intents_for(cmds)


def test_a_thermostat_without_adaptive_mode_gets_the_hysteresis_on_later_applies() -> (
    None
):
    cfg = replace(CFG, adaptive_mode="on", hysteresis=0.4)
    room = adaptive_logic(cfg)
    initial = with_trv(
        RoomState.initial(cfg),
        "climate.a",
        health=TrvHealth.UNSUPPORTED,
        unsupported=frozenset({"adaptive_mode"}),
    )
    _, cmds = room.handle(Start(READINGS, BOTH_AVAILABLE), T0, initial)
    assert intents_for(cmds)[:3] == [
        SetWindowDetection(enabled=True),
        SetHysteresis(0.4),
        SetFrostTemperature(7.0),
    ]
    assert intents_for(cmds, "climate.b")[:3] == [
        SetWindowDetection(enabled=True),
        SetAdaptiveMode(enabled=True),
        SetFrostTemperature(7.0),
    ]


def test_a_failed_hysteresis_is_sent_again_for_a_thermostat_without_adaptive_mode() -> (
    None
):
    cfg = replace(CFG, adaptive_mode="on", hysteresis=0.4)
    room = adaptive_logic(cfg)
    initial = with_trv(
        RoomState.initial(cfg),
        "climate.a",
        health=TrvHealth.UNSUPPORTED,
        unsupported=frozenset({"adaptive_mode"}),
    )
    st, _ = room.handle(Start(READINGS, BOTH_AVAILABLE), T0, initial)
    for _ in range(3):
        st, _ = room.handle(result("climate.a", HYSTERESIS, Failed("busy")), T0, st)
    _, cmds = room.handle(Tick(), T0 + timedelta(minutes=20), st)
    assert ("climate.a", HYSTERESIS) in executes(cmds)


def test_a_failed_adaptive_mode_is_sent_again_at_the_refresh_interval() -> None:
    cfg = replace(CFG, adaptive_mode="on")
    room, st, _ = start_room(cfg)
    for _ in range(3):
        st, _ = room.handle(
            result("climate.a", SetAdaptiveMode(enabled=True), Failed("busy")), T0, st
        )
    assert st.trvs["climate.a"].error_capability == "adaptive_mode"
    assert not st.trvs["climate.a"].settings_applied
    _, cmds = room.handle(Tick(), T0 + timedelta(minutes=20), st)
    assert ("climate.a", SetAdaptiveMode(enabled=True)) in executes(cmds)


def test_exhausted_frost_write_is_reapplied_on_resync() -> None:
    st = errored_on(FROST)
    assert not st.trvs["climate.a"].settings_applied
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    _, cmds = logic().handle(
        TrvAvailabilityChanged("climate.a", available=True), T0, st
    )
    assert FROST in intents_for(cmds)


def test_a_pending_frost_retry_is_reapplied_when_the_thermostat_is_away() -> None:
    st, _ = started()
    st, _ = logic().handle(result("climate.a", FROST, Failed("busy")), T0, st)
    st, _ = logic().handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, _ = logic().handle(RetryDue("climate.a", "frost_temperature"), T0, st)
    assert not st.trvs["climate.a"].settings_applied


# --- A capability the profile does not declare ----------------------------

NOT_IN_PROFILE_ADAPTIVE = Unsupported(
    "adaptive_mode", "unknown", "1.4.4", reason="profile"
)


def test_unsupported_reason_defaults_to_firmware() -> None:
    assert Unsupported("hysteresis", "1.3.0", "1.2.1").reason == "firmware"


def test_capability_missing_from_the_profile_is_not_a_firmware_problem() -> None:
    cfg = replace(CFG, adaptive_mode="on", hysteresis=0.4)
    room, st, _ = start_room(cfg)
    st, cmds = room.handle(
        result("climate.a", SetAdaptiveMode(enabled=True), NOT_IN_PROFILE_ADAPTIVE),
        T0,
        st,
    )
    assert of_type(cmds, RaiseIssue) == []
    assert of_type(cmds, ClearIssue) == []
    trv = st.trvs["climate.a"]
    assert trv.health == TrvHealth.OK
    assert trv.not_in_profile == frozenset(
        {capability_of(SetAdaptiveMode(enabled=True))}
    )
    assert trv.unsupported == trv.not_in_profile
    assert cmds[-1].status == RoomStatus.OK
    logs = of_type(cmds, Log)
    assert [log.level for log in logs] == ["debug"]
    assert "Living" in logs[0].message
    assert "climate.a" in logs[0].message
    assert capability_of(SetAdaptiveMode(enabled=True)) in logs[0].message
    # The adaptive -> hysteresis rule still applies.
    assert executes(cmds) == [("climate.a", SetHysteresis(0.4))]


def test_a_profile_gap_stays_healthy_when_the_thermostat_comes_back() -> None:
    cfg = replace(CFG, adaptive_mode="on")
    room, st, _ = start_room(cfg)
    st, _ = room.handle(
        result("climate.a", SetAdaptiveMode(enabled=True), NOT_IN_PROFILE_ADAPTIVE),
        T0,
        st,
    )
    st, _ = room.handle(TrvAvailabilityChanged("climate.a", available=False), T0, st)
    st, cmds = room.handle(TrvAvailabilityChanged("climate.a", available=True), T0, st)
    assert st.trvs["climate.a"].health == TrvHealth.OK
    assert cmds[-1].status == RoomStatus.OK


def test_a_profile_gap_is_logged_once_and_not_written_again() -> None:
    cfg = replace(CFG, adaptive_mode="on", hysteresis=0.4)
    room, st, _ = start_room(cfg)
    st, _ = room.handle(
        result("climate.a", SetAdaptiveMode(enabled=True), NOT_IN_PROFILE_ADAPTIVE),
        T0,
        st,
    )
    st = with_trv(st, "climate.a", settings_applied=False)
    _, cmds = room.handle(Start(READINGS, BOTH_AVAILABLE), T0, st)
    sent = intents_for(cmds)
    assert SetAdaptiveMode(enabled=True) not in sent
    assert sent.count(SetHysteresis(0.4)) == 1
    assert of_type(cmds, Log) == []
    assert of_type(cmds, RaiseIssue) == []


def test_a_device_unsupported_outcome_still_raises_the_firmware_issue() -> None:
    cfg = replace(CFG, adaptive_mode="on")
    room, st, _ = start_room(cfg)
    outcome = Unsupported("adaptive_mode", "unknown", "1.3.0", reason="device")
    st, cmds = room.handle(
        result("climate.a", SetAdaptiveMode(enabled=True), outcome), T0, st
    )
    assert [i.kind for i in of_type(cmds, RaiseIssue)] == ["firmware"]
    assert st.trvs["climate.a"].health == TrvHealth.UNSUPPORTED
    assert st.trvs["climate.a"].not_in_profile == frozenset()
