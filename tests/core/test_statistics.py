import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from custom_components.easy_trv.core.model import (
    Preset,
    RoomMode,
    RoomState,
    SensorReading,
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


def after(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


# --- Duration ------------------------------------------------------------


def test_a_new_duration_is_empty_and_stopped() -> None:
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


def test_restored_ignores_values_that_are_not_finite() -> None:
    assert restored(Duration(100.0, None), float("inf")) == Duration(100.0, None)
    assert restored(Duration(100.0, None), float("nan")) == Duration(100.0, None)
    assert restored(Duration(0.0, T0), float("-inf")) == Duration(0.0, T0)


def test_stop_with_a_clock_before_the_start_adds_nothing() -> None:
    running = start(Duration(600.0, None), T0)
    assert stop(running, T0 - timedelta(seconds=5)) == Duration(600.0, None)


def test_seconds_with_a_clock_before_the_start_does_not_go_below_the_total() -> None:
    running = start(Duration(600.0, None), T0)
    assert seconds(running, T0 - timedelta(seconds=5)) == 600.0


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
    assert deviation(room(20.4, 21.0)) == -0.6
    assert deviation(room(22.0, 21.0)) == 1.0


def test_deviation_is_rounded_to_a_tenth() -> None:
    assert deviation(room(20.44, 21.0)) == -0.6
    assert deviation(room(21.26, 21.0)) == 0.3


def test_deviation_rounds_the_reading_like_the_room() -> None:
    # Half-up on the reading, then on the difference: round() would give 0.2 / -8.5.
    assert deviation(room(21.25, 21.0)) == 0.3
    assert deviation(room(15.05, 23.5)) == -8.4


def test_deviation_is_never_negative_zero() -> None:
    for value, setpoint in ((20.96, 21.0), (21.0, 21.04)):
        result = deviation(room(value, setpoint))
        assert result == 0.0
        assert result is not None
        assert math.copysign(1, result) == 1


def test_deviation_none_when_off() -> None:
    assert deviation(room(mode=RoomMode.OFF)) is None


def test_deviation_none_on_frost_protection() -> None:
    assert deviation(room(preset=Preset.FROST)) is None


def test_deviation_none_during_window_hold() -> None:
    assert deviation(room(window_open=True)) is None


def test_deviation_none_during_outdoor_hold() -> None:
    assert deviation(room(outdoor_hold=True)) is None


def test_deviation_none_without_active_sensor() -> None:
    assert deviation(room(active_sensor=None)) is None


def test_deviation_none_when_the_reading_is_missing() -> None:
    assert deviation(room(active_sensor="sensor.other")) is None


def test_deviation_none_when_the_sensor_has_no_value() -> None:
    assert deviation(room(value=None)) is None


def test_deviation_none_without_setpoint() -> None:
    assert deviation(room(setpoint=None)) is None


def test_deviation_measures_against_the_lowered_target():
    # Wish 21 °C, lowered to 18 °C while away: the room is 1 K above the target.
    assert deviation(room(19.0, 21.0), goal=18.0) == 1.0
    assert deviation(room(19.0, 21.0)) == -2.0
