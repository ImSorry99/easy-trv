from datetime import timedelta

import pytest
from homeassistant.exceptions import ConfigEntryError

from custom_components.easy_trv import room_config_from_subentry, select_profile
from custom_components.easy_trv.core.model import RoomConfig, TrvConfig
from custom_components.easy_trv.core.settings import resolve_settings
from custom_components.easy_trv.profiles.loader import (
    BUILTIN_DIR,
    load_profiles_sync,
)
from tests.fake_gateway import FakeGateway
from tests.helpers_entry import IEEE_A, IEEE_B, room_data

BUILTIN_TEXT = (BUILTIN_DIR / "sonoff_trvzb_native.yaml").read_text()


def test_room_config_from_subentry_maps_the_stored_data() -> None:
    gateway = FakeGateway()
    refs = {
        "climate.a": gateway.add_device("climate.a", IEEE_A, firmware=(1, 3, 0)),
        "climate.b": gateway.add_device("climate.b", IEEE_B, firmware=None),
    }
    data = room_data(trvs=["climate.b", "climate.a"], hysteresis="0_6")

    config = room_config_from_subentry(
        "sub1", "Living", data, refs, resolve_settings({}, data)
    )

    assert config == RoomConfig(
        room_id="sub1",
        name="Living",
        trvs=(
            TrvConfig("climate.b", IEEE_B, None),
            TrvConfig("climate.a", IEEE_A, (1, 3, 0)),
        ),
        temp_sensors=("sensor.t1", "sensor.t2"),
        window_detection="on",
        hysteresis=0.6,
        adaptive_mode="on",
        frost_temperature=5.0,
        setpoint_range=None,
        window_contacts=(),
        window_warn=timedelta(minutes=120),
        window_check=timedelta(minutes=30),
        window_check_drop=0.3,
        outdoor_cutoff=None,
    )


@pytest.mark.parametrize(
    ("stored", "kelvin"),
    [
        ("0_2", 0.2),
        ("0_4", 0.4),
        ("0_6", 0.6),
        ("0_8", 0.8),
        ("1_0", 1.0),
        # Dotted form, accepted in case it was stored earlier.
        ("0.4", 0.4),
        ("1.0", 1.0),
    ],
)
def test_room_config_hysteresis_key_maps_to_kelvin(stored, kelvin) -> None:
    gateway = FakeGateway()
    refs = {"climate.a": gateway.add_device("climate.a", IEEE_A)}
    data = room_data(trvs=["climate.a"], hysteresis=stored)

    config = room_config_from_subentry(
        "sub1", "Living", data, refs, resolve_settings({}, data)
    )

    assert config.hysteresis == kelvin


def test_room_config_takes_the_resolved_settings() -> None:
    gateway = FakeGateway()
    refs = {"climate.a": gateway.add_device("climate.a", IEEE_A)}
    data = room_data(trvs=["climate.a"], hysteresis="global", window_detection="leave")
    options = {"hysteresis": "0_8", "adaptive_mode": "off", "frost_temperature": 9.0}

    config = room_config_from_subentry(
        "sub1", "Living", data, refs, resolve_settings(options, data)
    )

    assert config.hysteresis == 0.8
    assert config.window_detection == "leave"
    assert config.adaptive_mode == "off"
    assert config.frost_temperature == 9.0


def test_room_config_hysteresis_leave_resolves_to_the_global_value() -> None:
    gateway = FakeGateway()
    refs = {"climate.a": gateway.add_device("climate.a", IEEE_A)}
    data = room_data(trvs=["climate.a"], hysteresis="leave")

    config = room_config_from_subentry(
        "sub1", "Living", data, refs, resolve_settings({}, data)
    )

    assert config.hysteresis == 0.4


def test_select_profile_auto_prefers_builtin_over_user_profiles(tmp_path) -> None:
    (tmp_path / "a.yaml").write_text(
        BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: a_user_clone", 1)
    )
    profiles = load_profiles_sync(tmp_path).profiles
    gateway = FakeGateway()
    refs = {"climate.a": gateway.add_device("climate.a", IEEE_A)}

    assert select_profile(profiles, "auto", refs).id == "sonoff_trvzb_native"


def test_select_profile_auto_needs_one_profile_matching_every_thermostat(
    tmp_path,
) -> None:
    (tmp_path / "zbt.yaml").write_text(
        BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_zbt", 1).replace(
            "model: TRVZB", "model: TRV-ZBT", 1
        )
    )
    profiles = load_profiles_sync(tmp_path).profiles
    gateway = FakeGateway()
    mixed = {
        "climate.a": gateway.add_device("climate.a", IEEE_A),
        "climate.x": gateway.add_device("climate.x", "00:33", model="TRV-ZBT"),
    }
    only_x = {"climate.x": mixed["climate.x"]}

    assert select_profile(profiles, "auto", only_x).id == "user_zbt"
    with pytest.raises(ConfigEntryError):
        select_profile(profiles, "auto", mixed)


def test_select_profile_explicit_must_exist_and_match() -> None:
    profiles = load_profiles_sync(None).profiles
    gateway = FakeGateway()
    foreign = {"climate.x": gateway.add_device("climate.x", "00:33", model="TRV-ZBT")}
    native = {"climate.a": gateway.add_device("climate.a", IEEE_A)}

    assert select_profile(profiles, "sonoff_trvzb_native", native).id == (
        "sonoff_trvzb_native"
    )
    with pytest.raises(ConfigEntryError, match="no_such_profile"):
        select_profile(profiles, "no_such_profile", native)
    with pytest.raises(ConfigEntryError, match=r"climate\.x"):
        select_profile(profiles, "sonoff_trvzb_native", foreign)
