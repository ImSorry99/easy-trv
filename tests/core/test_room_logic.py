from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.easy_trv.core.model import (
    INTENT_NAMES,
    Command,
    DebounceElapsed,
    Execute,
    Failed,
    GlobalParams,
    Intent,
    Ok,
    OperationResult,
    RetryDue,
    RoomConfig,
    RoomState,
    RoomStatus,
    ScheduleDebounce,
    SensorChanged,
    SensorReading,
    SetHysteresis,
    SetRoomTemperature,
    SetSource,
    SetStatus,
    SetWindowDetection,
    Source,
    Start,
    Tick,
    TrvAvailabilityChanged,
    TrvConfig,
    TrvHealth,
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
)
T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
BOTH_AVAILABLE = {"climate.a": True, "climate.b": True}


def reading(eid: str, value: float | None, at: datetime = T0) -> SensorReading:
    return SensorReading(eid, value, at)


def capability_of(intent: Intent) -> str:
    return INTENT_NAMES[type(intent)].removeprefix("set_")


def logic() -> RoomLogic:
    return RoomLogic(CFG, GlobalParams.defaults(), capability_of=capability_of)


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


# --- Start ---------------------------------------------------------------


def test_start_applies_settings_then_external_and_value() -> None:
    st, cmds = started()
    assert executes(cmds)[:4] == [
        ("climate.a", SetWindowDetection(enabled=True)),
        ("climate.a", SetHysteresis(0.4)),
        ("climate.b", SetWindowDetection(enabled=True)),
        ("climate.b", SetHysteresis(0.4)),
    ]
    assert executes(cmds)[4:] == [
        ("climate.a", SetSource("external")),
        ("climate.b", SetSource("external")),
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.b", SetRoomTemperature(21.3)),
    ]
    assert st.source == Source.EXTERNAL
    assert st.active_sensor == "sensor.t1"
    assert st.last_written == 21.3
    assert st.settings_applied
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
        cfg, GlobalParams.defaults(), capability_of=capability_of
    ).handle(Start((), {}), T0, RoomState.initial(cfg))
    assert not any(
        isinstance(i, (SetSource, SetRoomTemperature)) for _, i in executes(cmds)
    )
    assert cmds[-1].status == RoomStatus.OK
    assert st.source == Source.NONE


def test_settings_are_only_emitted_when_configured() -> None:
    cfg = replace(CFG, window_detection="leave", hysteresis=None)
    _, cmds = RoomLogic(
        cfg, GlobalParams.defaults(), capability_of=capability_of
    ).handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE), T0, RoomState.initial(cfg)
    )
    assert not any(
        isinstance(i, (SetWindowDetection, SetHysteresis)) for _, i in executes(cmds)
    )


def test_settings_are_applied_only_once() -> None:
    st, _ = started()
    _, cmds = logic().handle(
        Start((reading("sensor.t1", 21.3),), BOTH_AVAILABLE), T0, st
    )
    assert not any(
        isinstance(i, (SetWindowDetection, SetHysteresis)) for _, i in executes(cmds)
    )


def test_window_detection_off_is_sent_as_disabled() -> None:
    cfg = replace(CFG, window_detection="off", hysteresis=None, temp_sensors=())
    _, cmds = RoomLogic(
        cfg, GlobalParams.defaults(), capability_of=capability_of
    ).handle(Start((), BOTH_AVAILABLE), T0, RoomState.initial(cfg))
    assert executes(cmds) == [
        ("climate.a", SetWindowDetection(enabled=False)),
        ("climate.b", SetWindowDetection(enabled=False)),
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
    params = replace(GlobalParams.defaults(), sensor_timeout=None)
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
    st = replace(st, last_written=None)
    _, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.3)), T0, st)
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
    st, _ = logic().handle(SensorChanged(reading("sensor.t2", 25.0)), T0, st)
    _, cmds = logic().handle(DebounceElapsed(), T0, st)
    assert executes(cmds) == [
        ("climate.a", SetRoomTemperature(21.3)),
        ("climate.b", SetRoomTemperature(21.3)),
    ]


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


def test_refresh_needs_a_verified_write() -> None:
    st, _ = started()
    assert st.last_value_write is None
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


def test_refresh_skips_unsupported_trv() -> None:
    st, _ = started()
    st = with_trv(
        verified_value_write(st, at=T0), "climate.b", health=TrvHealth.UNSUPPORTED
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


@pytest.mark.parametrize(
    "event",
    [
        TrvAvailabilityChanged("climate.a", available=False),
        RetryDue("climate.a", "room_temperature"),
        OperationResult(
            "climate.a", SetRoomTemperature(21.3), "room_temperature", Ok()
        ),
        OperationResult(
            "climate.a", SetRoomTemperature(21.3), "room_temperature", Failed("x")
        ),
    ],
)
def test_result_and_availability_events_are_not_handled_yet(event: object) -> None:
    st, _ = started()
    with pytest.raises(NotImplementedError):
        logic().handle(event, T0, st)  # type: ignore[arg-type]


def test_state_is_never_mutated() -> None:
    before, _ = started()
    snapshot = replace(before)
    logic().handle(SensorChanged(reading("sensor.t1", 25.0)), T0, before)
    logic().handle(Tick(), T0 + timedelta(minutes=200), before)
    assert before == snapshot
    assert dict(before.readings) == dict(snapshot.readings)
