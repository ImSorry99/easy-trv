from typing import Any

import pytest
import yaml

from custom_components.easy_trv.core.model import (
    SetAdaptiveMode,
    SetFrostTemperature,
    SetHvacMode,
    SetHysteresis,
    SetRoomTemperature,
    SetSetpoint,
    SetSource,
    SetWindowDetection,
)
from custom_components.easy_trv.profiles import engine, loader, schema


@pytest.fixture
def trvzb() -> engine.Profile:
    return loader.load_profiles_sync(None).profiles["sonoff_trvzb_native"]


def _builtin_dict() -> dict[str, Any]:
    text = (loader.BUILTIN_DIR / "sonoff_trvzb_native.yaml").read_text()
    return schema.validate_profile_dict(yaml.safe_load(text))


def test_matches_case_insensitive(trvzb: engine.Profile) -> None:
    assert trvzb.matches("sonoff", "TRVZB")
    assert not trvzb.matches("SONOFF", "TRV-ZBT")


def test_plan_room_temperature(trvzb: engine.Profile) -> None:
    op = trvzb.plan(SetRoomTemperature(21.35), (1, 3, 0))
    assert (op.cluster, op.endpoint, op.attribute, op.raw_value) == (
        0xFC11,
        1,
        0x600D,
        2140,
    )
    assert op.accept == frozenset({2140})
    assert op.also_read == (0x600E,)
    assert op.extra_signals == {0x600E: {2: "failsafe"}}


def test_plan_set_source_accepts_3(trvzb: engine.Profile) -> None:
    op = trvzb.plan(SetSource("external"), None)
    assert op.raw_value == 1
    assert op.accept == frozenset({1, 3})
    assert op.signals == {2: "failsafe"}
    assert trvzb.plan(SetSource("internal"), None).accept == frozenset({0})


def test_plan_hysteresis_and_window(trvzb: engine.Profile) -> None:
    assert trvzb.plan(SetHysteresis(0.4), (1, 3, 0)).raw_value == -40
    assert trvzb.plan(SetWindowDetection(enabled=True), (1, 2, 1)).raw_value == 1


def test_firmware_gating(trvzb: engine.Profile) -> None:
    with pytest.raises(engine.CapabilityUnsupported) as exc:
        trvzb.plan(SetHysteresis(0.4), (1, 2, 1))
    assert exc.value.required == (1, 3, 0)
    assert not trvzb.device_supported((1, 1, 6))
    assert trvzb.device_supported(None)


def test_range_enforced(trvzb: engine.Profile) -> None:
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetRoomTemperature(-0.5), None)
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetHysteresis(1.2), (1, 3, 0))


def test_capability_for_and_supports(trvzb: engine.Profile) -> None:
    assert trvzb.capability_for(SetHysteresis(0.4)) == "hysteresis"
    assert trvzb.capability_for(SetSource("internal")) == "source_select"
    assert trvzb.supports("hysteresis", None)
    assert trvzb.supports("hysteresis", (1, 3, 0))
    assert not trvzb.supports("hysteresis", (1, 2, 1))
    assert trvzb.supports("external_temperature", (1, 2, 1))
    assert not trvzb.supports("unknown", None)


def test_profile_minimum_firmware_gates_every_capability(trvzb: engine.Profile) -> None:
    with pytest.raises(engine.CapabilityUnsupported) as exc:
        trvzb.plan(SetRoomTemperature(21.0), (1, 1, 6))
    assert exc.value.capability == "external_temperature"
    assert exc.value.required == (1, 2, 1)
    assert exc.value.installed == (1, 1, 6)
    assert not trvzb.supports("external_temperature", (1, 1, 6))


def test_profile_without_capability_for_intent_is_unsupported() -> None:
    data = _builtin_dict()
    del data["capabilities"]["window_detection_flag"]
    profile = engine.Profile.from_dict(data)
    assert profile.capability_for(SetWindowDetection(enabled=True)) is None
    with pytest.raises(engine.CapabilityUnsupported) as exc:
        profile.plan(SetWindowDetection(enabled=True), (1, 3, 0))
    assert exc.value.required is None
    assert exc.value.installed == (1, 3, 0)


def test_plan_without_range_does_no_range_check() -> None:
    data = _builtin_dict()
    del data["capabilities"]["external_temperature"]["write"]["range"]
    profile = engine.Profile.from_dict(data)
    assert profile.plan(SetRoomTemperature(120.0), None).raw_value == 12000


def test_from_dict_requires_one_of_keys_to_match_map() -> None:
    data = _builtin_dict()
    del data["capabilities"]["source_select"]["verify"]["internal"]
    with pytest.raises(ValueError, match="source_select"):
        engine.Profile.from_dict(data)


def test_encoder_that_cannot_encode_the_value_raises() -> None:
    data = _builtin_dict()
    data["capabilities"]["external_temperature"]["write"]["encode"] = "bool"
    profile = engine.Profile.from_dict(data)
    with pytest.raises(ValueError, match="external_temperature"):
        profile.plan(SetRoomTemperature(21.0), None)


def test_const_encoder_ignores_the_value() -> None:
    data = _builtin_dict()
    data["capabilities"]["window_detection_flag"]["write"]["encode"] = {"const": 7}
    profile = engine.Profile.from_dict(data)
    assert profile.plan(SetWindowDetection(enabled=False), None).raw_value == 7
    assert profile.plan(SetWindowDetection(enabled=True), None).raw_value == 7


def test_one_of_without_key_for_value_raises() -> None:
    data = _builtin_dict()
    window = data["capabilities"]["window_detection_flag"]
    window["verify"] = {"rule": "one_of", "true": [1]}
    profile = engine.Profile.from_dict(data)
    assert profile.plan(SetWindowDetection(enabled=True), None).accept == frozenset({1})
    with pytest.raises(ValueError, match="window_detection_flag"):
        profile.plan(SetWindowDetection(enabled=False), None)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_value_is_out_of_range(trvzb: engine.Profile, bad: float) -> None:
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetRoomTemperature(bad), None)
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetHysteresis(bad), (1, 3, 0))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_value_is_out_of_range_without_declared_range(bad: float) -> None:
    data = _builtin_dict()
    del data["capabilities"]["external_temperature"]["write"]["range"]
    profile = engine.Profile.from_dict(data)
    with pytest.raises(engine.ValueOutOfRange):
        profile.plan(SetRoomTemperature(bad), None)


def test_map_encoder_without_entry_for_value_raises() -> None:
    data = _builtin_dict()
    source = data["capabilities"]["source_select"]
    source["write"]["encode"] = {"map": {"external": 1}}
    source["verify"] = {"rule": "equals"}
    profile = engine.Profile.from_dict(data)
    assert profile.plan(SetSource("external"), None).raw_value == 1
    with pytest.raises(ValueError, match="source_select"):
        profile.plan(SetSource("internal"), None)


def test_capability_unsupported_message(trvzb: engine.Profile) -> None:
    with pytest.raises(engine.CapabilityUnsupported) as too_old:
        trvzb.plan(SetHysteresis(0.4), (1, 2, 1))
    assert "1.3.0" in str(too_old.value)
    assert "1.2.1" in str(too_old.value)
    data = _builtin_dict()
    del data["capabilities"]["window_detection_flag"]
    with pytest.raises(engine.CapabilityUnsupported) as absent:
        engine.Profile.from_dict(data).plan(SetWindowDetection(enabled=True), None)
    assert "set_window_detection" in str(absent.value)


def test_numeric_encoder_on_non_numeric_value_is_a_plain_value_error() -> None:
    data = _builtin_dict()
    window = data["capabilities"]["window_detection_flag"]
    window["write"]["encode"] = "temperature_centi"
    window["write"]["range"] = [0.0, 1.0]
    profile = engine.Profile.from_dict(data)
    with pytest.raises(ValueError, match="window_detection_flag") as exc:
        profile.plan(SetWindowDetection(enabled=True), None)
    assert not isinstance(exc.value, engine.ValueOutOfRange)


def test_builtin_profile_plans_adaptive_mode_on_and_off(
    trvzb: engine.Profile,
) -> None:
    on = trvzb.plan(SetAdaptiveMode(enabled=True), (1, 4, 4))
    off = trvzb.plan(SetAdaptiveMode(enabled=False), (1, 4, 4))
    assert (on.cluster, on.endpoint, on.attribute) == (0xFC11, 1, 0x6017)
    assert (on.raw_value, on.accept) == (2, frozenset({2}))
    assert (off.raw_value, off.accept) == (0, frozenset({0, 1}))


def test_adaptive_mode_needs_firmware_1_4_4(trvzb: engine.Profile) -> None:
    with pytest.raises(engine.CapabilityUnsupported) as exc:
        trvzb.plan(SetAdaptiveMode(enabled=True), (1, 4, 1))
    assert exc.value.required == (1, 4, 4)


def test_frost_temperature_encodes_centi_and_rejects_out_of_range(
    trvzb: engine.Profile,
) -> None:
    op = trvzb.plan(SetFrostTemperature(7.0), (1, 3, 0))
    assert (op.attribute, op.raw_value, op.accept) == (0x6002, 700, frozenset({700}))
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetFrostTemperature(3.0), (1, 3, 0))
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetFrostTemperature(35.5), (1, 3, 0))


def test_setpoint_plans_centi_rounded_to_half_degree(trvzb: engine.Profile) -> None:
    op = trvzb.plan(SetSetpoint(21.3), (1, 4, 4))
    assert (op.cluster, op.attribute, op.raw_value) == (0x0201, 0x0012, 2150)
    assert op.accept == frozenset({2150})


@pytest.mark.parametrize(
    ("celsius", "raw"),
    [(21.25, 2150), (21.24, 2100), (21.0, 2100), (21.75, 2200), (4.2, 400)],
)
def test_setpoint_step_rounds_half_up(
    trvzb: engine.Profile, celsius: float, raw: int
) -> None:
    assert trvzb.plan(SetSetpoint(celsius), None).raw_value == raw


def test_setpoint_range_is_checked_after_rounding(trvzb: engine.Profile) -> None:
    assert trvzb.plan(SetSetpoint(3.75), None).raw_value == 400
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetSetpoint(3.7), None)
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetSetpoint(35.3), None)
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetSetpoint(float("nan")), None)


def test_hvac_mode_plans_heat_and_off(trvzb: engine.Profile) -> None:
    heat = trvzb.plan(SetHvacMode("heat"), (1, 3, 0))
    off = trvzb.plan(SetHvacMode("off"), (1, 3, 0))
    assert (heat.cluster, heat.attribute, heat.raw_value) == (0x0201, 0x001C, 4)
    assert off.raw_value == 0
    assert heat.accept == frozenset({4})
    assert off.accept == frozenset({0})
    assert heat.signals == {1: "schedule"}


def test_decode_inverts_the_encoders(trvzb: engine.Profile) -> None:
    assert trvzb.decode("setpoint", 2150) == 21.5
    assert trvzb.decode("setpoint", 2133) == 21.33
    assert trvzb.decode("hvac_mode", 0) == "off"
    assert trvzb.decode("hvac_mode", 4) == "heat"
    assert trvzb.decode("hvac_mode", 1) is None
    assert trvzb.decode("hysteresis", -40) is None
    with pytest.raises(KeyError):
        trvzb.decode("nope", 0)


def test_limits_and_observed(trvzb: engine.Profile) -> None:
    assert trvzb.limits("set_setpoint") == (4.0, 35.0, 0.5)
    assert trvzb.limits("set_hysteresis") == (0.2, 1.0, 0.5)  # default step
    assert trvzb.limits("set_hvac_mode") is None  # no range
    assert trvzb.limits("set_window_detection") is None
    assert set(trvzb.observed()) == {"setpoint", "hvac_mode"}


def test_limits_use_the_declared_step_and_none_without_a_capability() -> None:
    data = _builtin_dict()
    data["capabilities"]["setpoint"]["write"]["step"] = 0.1
    data["capabilities"].pop("hvac_mode")
    profile = engine.Profile.from_dict(data)
    assert profile.limits("set_setpoint") == (4.0, 35.0, 0.1)
    assert profile.limits("set_hvac_mode") is None
    assert profile.observed() == ("setpoint",)


def test_a_capability_without_step_and_observe_keeps_working() -> None:
    data = _builtin_dict()
    del data["capabilities"]["setpoint"]["write"]["step"]
    del data["capabilities"]["setpoint"]["observe"]
    profile = engine.Profile.from_dict(data)
    assert profile.capabilities["setpoint"].step is None
    assert profile.capabilities["setpoint"].observe is False
    assert profile.plan(SetSetpoint(21.3), None).raw_value == 2130


def test_the_builtin_profile_names_the_six_entities_easy_trv_controls(
    trvzb: engine.Profile,
) -> None:
    named = {
        name: spec.entity
        for name, spec in trvzb.capabilities.items()
        if spec.entity is not None
    }
    assert named == {
        "external_temperature": engine.EntityRef(
            "number", "external_temperature_sensor_value"
        ),
        "source_select": engine.EntityRef("switch", "external_temperature_sensor"),
        "window_detection_flag": engine.EntityRef("switch", "open_window"),
        "hysteresis": engine.EntityRef("number", "temperature_control_accuracy"),
        "adaptive_mode": engine.EntityRef("switch", "adaptive_mode"),
        "frost_protection": engine.EntityRef("number", "frost_protection_temperature"),
    }
    # The setpoint and the mode belong to the climate entity.
    assert trvzb.capabilities["setpoint"].entity is None
    assert trvzb.capabilities["hvac_mode"].entity is None


def test_a_capability_without_an_entity_has_none() -> None:
    data = _builtin_dict()
    for capability in data["capabilities"].values():
        capability.pop("entity", None)
    profile = engine.Profile.from_dict(data)
    assert {spec.entity for spec in profile.capabilities.values()} == {None}


def test_an_entity_reference_is_hashable_and_compared_by_value() -> None:
    assert engine.EntityRef("number", "a") == engine.EntityRef("number", "a")
    assert {engine.EntityRef("number", "a"), engine.EntityRef("number", "a")} == {
        engine.EntityRef("number", "a")
    }
