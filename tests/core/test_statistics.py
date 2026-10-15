from dataclasses import replace
from datetime import UTC, datetime, timedelta

from custom_components.easy_trv.core.model import (
    Preset,
    RoomConfig,
    RoomMode,
    RoomState,
    SensorReading,
    TrvConfig,
)
from custom_components.easy_trv.core.statistics import (
    Duration,
    deviation,
    restored,
    seconds,
    start,
    stop,
)

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)

CFG = RoomConfig(
    "room1",
    "Living",
    (TrvConfig("climate.a", "00:11", (1, 3, 0)),),
    ("sensor.t1",),
    "on",
    0.4,
    "leave",
    7.0,
)


def after(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


# --- Duration ------------------------------------------------------------


def test_a_new_duration_is_empty_and_stopped() -> None:
    assert Duration(0.0, None) == Duration(0.0, None)
    assert seconds(Duration(0.0, None), T0) == 0.0


def test_two_periods_add_up() -> None:
    d = start(Duration(0.0, None), T0)
    assert seconds(d, after(10)) == 600.0
    d = stop(d, after(10))
    assert d == Duration(600.0, None)
    assert seconds(d, after(30)) == 600.0
    d = start(d, after(30))
    assert seconds(d, after(35)) == 900.0
    d = stop(d, after(40))
    assert d == Duration(1200.0, None)
    assert seconds(d, after(60)) == 1200.0


def test_start_twice_keeps_the_first_start() -> None:
    d = start(Duration(0.0, None), T0)
    d = start(d, after(5))
    assert d.since == T0
    assert seconds(d, after(10)) == 600.0


def test_stop_while_stopped_changes_nothing() -> None:
    d = Duration(300.0, None)
    assert stop(d, after(5)) == d


def test_start_and_stop_do_not_mutate() -> None:
    d = Duration(0.0, None)
    running = start(d, T0)
    stop(running, after(1))
    assert d == Duration(0.0, None)
    assert running == Duration(0.0, T0)


def test_restored_raises_the_total() -> None:
    assert restored(Duration(100.0, None), 500.0) == Duration(500.0, None)


def test_restored_never_lowers_the_total() -> None:
    assert restored(Duration(500.0, None), 100.0) == Duration(500.0, None)


def test_restored_keeps_a_running_period() -> None:
    d = restored(Duration(0.0, T0), 500.0)
    assert d == Duration(500.0, T0)
    assert seconds(d, after(1)) == 560.0


def test_restored_ignores_negative_values() -> None:
    assert restored(Duration(0.0, None), -5.0) == Duration(0.0, None)


# --- Deviation -----------------------------------------------------------


def room(
    value: float | None = 20.4,
    setpoint: float | None = 21.0,
    **changes: object,
) -> RoomState:
    base = RoomState(
        readings={"sensor.t1": SensorReading("sensor.t1", value, T0)}
        if value is not None
        else {"sensor.t1": SensorReading("sensor.t1", None, None)},
        trvs={},
        active_sensor="sensor.t1",
        setpoint=setpoint,
    )
    return replace(base, **changes)


def test_deviation_is_sensor_minus_setpoint_while_heating() -> None:
    assert deviation(room(20.4, 21.0), CFG, T0) == -0.6
    assert deviation(room(22.0, 21.0), CFG, T0) == 1.0


def test_deviation_is_rounded_to_a_tenth() -> None:
    assert deviation(room(20.44, 21.0), CFG, T0) == -0.6
    assert deviation(room(21.26, 21.0), CFG, T0) == 0.3


def test_deviation_none_when_off() -> None:
    assert deviation(room(mode=RoomMode.OFF), CFG, T0) is None


def test_deviation_none_on_frost_protection() -> None:
    assert deviation(room(preset=Preset.FROST), CFG, T0) is None


def test_deviation_none_during_window_hold() -> None:
    assert deviation(room(window_open=True), CFG, T0) is None


def test_deviation_none_during_outdoor_hold() -> None:
    assert deviation(room(outdoor_hold=True), CFG, T0) is None


def test_deviation_none_without_active_sensor() -> None:
    assert deviation(room(active_sensor=None), CFG, T0) is None


def test_deviation_none_when_the_reading_is_missing() -> None:
    assert deviation(room(active_sensor="sensor.other"), CFG, T0) is None


def test_deviation_none_when_the_sensor_has_no_value() -> None:
    assert deviation(room(value=None), CFG, T0) is None


def test_deviation_none_without_setpoint() -> None:
    assert deviation(room(setpoint=None), CFG, T0) is None
