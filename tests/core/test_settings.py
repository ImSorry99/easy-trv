from custom_components.easy_trv.core.settings import (
    hysteresis_kelvin,
    overrides,
    resolve_settings,
)


def test_defaults_when_nothing_is_set():
    s = resolve_settings({}, {})
    assert (s.refresh_minutes, s.verify_delay_seconds, s.max_attempts) == (20, 10, 3)
    assert (s.min_delta_kelvin, s.sensor_timeout_minutes) == (0.1, 120)
    assert (s.window_detection, s.hysteresis, s.adaptive_mode) == ("on", 0.4, "on")
    assert (s.frost_temperature, s.profile) == (5.0, "auto")


def test_global_overrides_default_and_room_overrides_global():
    options = {"hysteresis": "0_6", "refresh_minutes": 5, "adaptive_mode": "off"}
    room = {"hysteresis": "0_2", "refresh_minutes": None, "adaptive_mode": "global"}
    s = resolve_settings(options, room)
    assert (s.hysteresis, s.refresh_minutes, s.adaptive_mode) == (0.2, 5, "off")


def test_legacy_values_are_accepted():
    assert resolve_settings({}, {"hysteresis": "0.4"}).hysteresis == 0.4
    # "leave" no longer exists: the room follows the global value.
    assert (
        resolve_settings({"hysteresis": "0_6"}, {"hysteresis": "leave"}).hysteresis
        == 0.6
    )


def test_overrides_lists_only_room_specific_keys():
    room = {
        "hysteresis": "0_2",
        "adaptive_mode": "global",
        "refresh_minutes": None,
        "trvs": [],
    }
    assert overrides(room) == {"hysteresis": "0_2"}


def test_sensor_timeout_zero_means_off_is_kept_as_zero():
    assert (
        resolve_settings({"sensor_timeout_minutes": 0}, {}).sensor_timeout_minutes == 0
    )


def test_hysteresis_kelvin_accepts_key_dotted_and_number():
    assert hysteresis_kelvin("0_4") == 0.4
    assert hysteresis_kelvin("0.4") == 0.4
    assert hysteresis_kelvin(0.4) == 0.4


def test_room_profile_global_falls_back_to_global_profile_then_auto():
    assert resolve_settings({"profile": "x"}, {"profile": "global"}).profile == "x"
    assert resolve_settings({}, {"profile": "global"}).profile == "auto"
    assert resolve_settings({"profile": "x"}, {"profile": "y"}).profile == "y"


def test_non_setting_room_keys_are_ignored():
    room = {
        "trvs": ["a"],
        "temp_sensors": [],
        "humidity_sensors": [],
        "name": "n",
        "area_id": "a",
    }
    assert overrides(room) == {}
    assert resolve_settings({}, room) == resolve_settings({}, {})


def test_numbers_are_coerced():
    s = resolve_settings(
        {"refresh_minutes": 5.0, "frost_temperature": 8}, {"max_attempts": 4.4}
    )
    assert s.refresh_minutes == 5
    assert isinstance(s.refresh_minutes, int)
    assert s.frost_temperature == 8.0
    assert isinstance(s.frost_temperature, float)
    assert s.max_attempts == 4
