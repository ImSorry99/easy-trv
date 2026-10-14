from pathlib import Path

from custom_components.easy_trv import const
from custom_components.easy_trv.core import model


def test_defaults_match_requirements() -> None:
    assert const.DOMAIN == "easy_trv"
    assert const.DEFAULT_REFRESH_MINUTES == 20
    assert const.DEFAULT_VERIFY_DELAY == 10
    assert const.DEFAULT_MAX_ATTEMPTS == 3
    assert const.DEFAULT_MIN_DELTA == 0.1
    assert const.DEFAULT_SENSOR_TIMEOUT == 120
    assert const.DEFAULT_HYSTERESIS == "0_4"
    assert const.DEFAULT_WINDOW_DETECTION == "on"
    assert const.DEFAULT_ADAPTIVE_MODE == "on"
    assert const.DEFAULT_FROST_TEMPERATURE == 5.0
    assert const.SETTING_GLOBAL == "global"
    assert const.CONF_ADAPTIVE_MODE == "adaptive_mode"
    assert const.CONF_FROST_TEMPERATURE == "frost_temperature"
    assert const.BACKOFF_SECONDS == (5.0, 15.0, 45.0)


def test_event_names_are_defined_once_in_core() -> None:
    assert const.EVENT_WRITE_FAILED == model.EVENT_WRITE_FAILED
    assert const.EVENT_RECOVERED == model.EVENT_RECOVERED
    assert const.EVENT_FAILSAFE == model.EVENT_FAILSAFE
    # const only re-exports them; the literals live in core.model.
    source = Path(const.__file__).read_text()
    for name in (model.EVENT_WRITE_FAILED, model.EVENT_RECOVERED, model.EVENT_FAILSAFE):
        assert name not in source
