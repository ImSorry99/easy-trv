from custom_components.easy_trv import const


def test_defaults_match_requirements() -> None:
    assert const.DOMAIN == "easy_trv"
    assert const.DEFAULT_REFRESH_MINUTES == 20
    assert const.DEFAULT_VERIFY_DELAY == 10
    assert const.DEFAULT_MAX_ATTEMPTS == 3
    assert const.DEFAULT_MIN_DELTA == 0.1
    assert const.DEFAULT_SENSOR_TIMEOUT == 120
    assert const.BACKOFF_SECONDS == (5.0, 15.0, 45.0)
