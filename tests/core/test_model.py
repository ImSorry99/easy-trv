from datetime import timedelta

from custom_components.easy_trv.core.model import GlobalParams


def test_empty_options_give_the_defaults() -> None:
    assert GlobalParams.from_options({}) == GlobalParams.defaults()


def test_options_are_read_and_converted() -> None:
    # A NumberSelector delivers floats, also for whole numbers.
    params = GlobalParams.from_options(
        {
            "refresh_minutes": 30.0,
            "verify_delay_seconds": 5.0,
            "max_attempts": 4.0,
            "min_delta_kelvin": 0.3,
            "sensor_timeout_minutes": 60.0,
        }
    )
    assert params.refresh == timedelta(minutes=30)
    assert params.verify_delay == 5.0
    assert params.max_attempts == 4
    assert isinstance(params.max_attempts, int)
    assert params.min_delta == 0.3
    assert params.sensor_timeout == timedelta(minutes=60)


def test_sensor_timeout_zero_switches_the_timeout_off() -> None:
    assert (
        GlobalParams.from_options({"sensor_timeout_minutes": 0}).sensor_timeout is None
    )
    assert (
        GlobalParams.from_options({"sensor_timeout_minutes": 0.0}).sensor_timeout
        is None
    )


def test_missing_and_unknown_options_fall_back_to_defaults() -> None:
    params = GlobalParams.from_options({"refresh_minutes": 45, "unrelated": "x"})
    assert params.refresh == timedelta(minutes=45)
    assert params.verify_delay == GlobalParams.defaults().verify_delay
    assert params.sensor_timeout == timedelta(minutes=120)


def test_timing_constants_stay_at_their_defaults() -> None:
    params = GlobalParams.from_options({})
    assert params.debounce == 5.0
    assert params.backoff == (5.0, 15.0, 45.0)
