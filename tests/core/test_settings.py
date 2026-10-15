from dataclasses import fields

import pytest

from custom_components.easy_trv.core.settings import (
    DEFAULTS,
    RoomSettings,
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


def test_new_defaults():
    s = resolve_settings({}, {})
    assert (s.setpoint_min, s.setpoint_max) == (5.0, 30.0)
    assert (s.window_warn_minutes, s.window_check_minutes) == (120, 30)
    assert s.window_check_drop_kelvin == 0.3
    assert s.outdoor_cutoff_celsius is None
    assert s.window_open_delay_seconds == 30


def test_window_open_delay_follows_room_then_global_and_zero_is_a_value():
    assert (
        resolve_settings(
            {"window_open_delay_seconds": 60}, {}
        ).window_open_delay_seconds
        == 60
    )
    room = {"window_open_delay_seconds": 0}
    assert (
        resolve_settings(
            {"window_open_delay_seconds": 60}, room
        ).window_open_delay_seconds
        == 0
    )
    room = {"window_open_delay_seconds": 45.0}
    delay = resolve_settings({}, room).window_open_delay_seconds
    assert delay == 45
    assert isinstance(delay, int)


def test_outdoor_cutoff_global_room_number_and_room_off():
    assert (
        resolve_settings({"outdoor_cutoff_celsius": 16.0}, {}).outdoor_cutoff_celsius
        == 16.0
    )
    assert (
        resolve_settings(
            {"outdoor_cutoff_celsius": 16.0}, {"outdoor_cutoff_celsius": 18.0}
        ).outdoor_cutoff_celsius
        == 18.0
    )
    assert (
        resolve_settings(
            {"outdoor_cutoff_celsius": 16.0}, {"outdoor_cutoff": "off"}
        ).outdoor_cutoff_celsius
        is None
    )


def test_room_off_wins_over_a_room_number():
    room = {"outdoor_cutoff": "off", "outdoor_cutoff_celsius": 18.0}
    assert resolve_settings({}, room).outdoor_cutoff_celsius is None


def test_outdoor_cutoff_global_choice_and_blank_number_follow_global():
    options = {"outdoor_cutoff_celsius": 16.0}
    room = {"outdoor_cutoff": "global", "outdoor_cutoff_celsius": None}
    assert resolve_settings(options, room).outdoor_cutoff_celsius == 16.0


def test_outdoor_cutoff_is_a_room_only_key():
    # The switch is read from the room, never from the global options.
    off = resolve_settings({"outdoor_cutoff": "off"}, {})
    assert off.outdoor_cutoff_celsius is None
    options = {"outdoor_cutoff": "off", "outdoor_cutoff_celsius": 16.0}
    assert resolve_settings(options, {}).outdoor_cutoff_celsius == 16.0
    assert "outdoor_cutoff" not in DEFAULTS
    assert "outdoor_cutoff" not in {f.name for f in fields(RoomSettings)}


def test_overrides_keeps_outdoor_cutoff_off_and_drops_global():
    assert overrides({"outdoor_cutoff": "off"}) == {"outdoor_cutoff": "off"}
    assert overrides({"outdoor_cutoff": "global"}) == {}
    assert overrides({"outdoor_cutoff": None}) == {}


def test_new_numbers_are_coerced():
    s = resolve_settings(
        {"setpoint_min": 6, "window_warn_minutes": 60.0, "outdoor_cutoff_celsius": 16},
        {"window_check_drop_kelvin": 1, "window_check_minutes": 10.4},
    )
    assert s.setpoint_min == 6.0
    assert isinstance(s.setpoint_min, float)
    assert s.window_warn_minutes == 60
    assert isinstance(s.window_warn_minutes, int)
    assert s.window_check_drop_kelvin == 1.0
    assert isinstance(s.window_check_drop_kelvin, float)
    assert s.window_check_minutes == 10
    assert s.outdoor_cutoff_celsius == 16.0
    assert isinstance(s.outdoor_cutoff_celsius, float)


def test_setpoint_min_must_be_below_max():
    with pytest.raises(ValueError, match="setpoint_min"):
        resolve_settings({"setpoint_max": 18.0}, {"setpoint_min": 18.0})
    with pytest.raises(ValueError, match="setpoint_min"):
        resolve_settings({}, {"setpoint_min": 31.0})
    with pytest.raises(ValueError, match="setpoint_min"):
        resolve_settings({"setpoint_max": 4.0}, {})
    assert resolve_settings({}, {"setpoint_min": 29.5}).setpoint_min == 29.5


def test_the_setpoint_range_is_auto_by_default():
    assert resolve_settings({}, {}).setpoint_range == "auto"
    assert resolve_settings({"setpoint_range": "manual"}, {}).setpoint_range == "manual"
    room = {"setpoint_range": "auto"}
    assert resolve_settings({"setpoint_range": "manual"}, room).setpoint_range == "auto"


@pytest.mark.parametrize(
    ("options", "room", "expected"),
    [
        # Saved before the choice existed: own limits mean manual.
        ({"setpoint_max": 24.0}, {}, "manual"),
        ({}, {"setpoint_min": 16.0}, "manual"),
        # The room's limits make only that room manual.
        ({"setpoint_range": "auto"}, {"setpoint_min": 16.0}, "manual"),
        # A saved choice wins over the limits, also "global" in a room.
        ({"setpoint_range": "auto", "setpoint_max": 24.0}, {}, "auto"),
        ({}, {"setpoint_range": "global", "setpoint_min": 16.0}, "auto"),
        ({"setpoint_max": 24.0}, {"setpoint_range": "auto"}, "auto"),
    ],
)
def test_limits_saved_before_the_range_choice_count_as_manual(options, room, expected):
    assert resolve_settings(options, room).setpoint_range == expected
