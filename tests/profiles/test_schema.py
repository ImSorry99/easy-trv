from pathlib import Path
from typing import Any

import pytest
import voluptuous as vol
import yaml

from custom_components.easy_trv.profiles import schema

BUILTIN = Path("custom_components/easy_trv/profiles/builtin/sonoff_trvzb_native.yaml")


def _load_builtin() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(BUILTIN.read_text())
    return data


def test_builtin_profile_validates() -> None:
    data = schema.validate_profile_dict(_load_builtin())
    assert data["id"] == "sonoff_trvzb_native"
    assert data["capabilities"]["external_temperature"]["write"]["cluster"] == 0xFC11
    assert data["capabilities"]["source_select"]["signals"] == {2: "failsafe"}
    assert data["capabilities"]["hysteresis"]["min_firmware"] == "1.3.0"


def test_unknown_encoder_rejected() -> None:
    data = _load_builtin()
    data["capabilities"]["external_temperature"]["write"]["encode"] = "float"
    with pytest.raises(vol.Invalid):
        schema.validate_profile_dict(data)


def test_duplicate_intent_rejected() -> None:
    data = _load_builtin()
    data["capabilities"]["copy"] = dict(data["capabilities"]["external_temperature"])
    with pytest.raises(vol.Invalid, match="duplicate intent"):
        schema.validate_profile_dict(data)


def _mutated(where: str, key: str, value: object) -> dict[str, Any]:
    """The built-in profile with one field of ``where`` replaced."""
    data = _load_builtin()
    target = {
        "root": data,
        "capability": data["capabilities"]["external_temperature"],
        "write": data["capabilities"]["external_temperature"]["write"],
    }[where]
    target[key] = value
    return data


@pytest.mark.parametrize(
    ("where", "key", "value", "message"),
    [
        ("root", "schema", 2, "unsupported schema version"),
        ("root", "schema", True, "unsupported schema version"),
        ("write", "endpoint", "1", "expected an integer"),
        ("write", "endpoint", True, "expected an integer"),
        ("write", "endpoint", 241, "between 0 and 240"),
        ("write", "range", [0], "range must be a list"),
        ("write", "range", "0-99", "range must be a list"),
        ("write", "range", [5, 1], "low must not exceed high"),
        ("write", "range", [0, "x"], "expected a number"),
        ("write", "range", [0, True], "expected a number"),
        ("write", "range", [0, float("inf")], "expected a finite number"),
        ("capability", "verify", "equals", "verify must be a mapping"),
        ("capability", "verify", {"rule": "one_of"}, "needs at least one list"),
        ("capability", "verify", {"rule": "nope"}, "rule must be one of"),
    ],
)
def test_invalid_documents_are_rejected_with_a_reason(
    where: str, key: str, value: object, message: str
) -> None:
    with pytest.raises(vol.Invalid, match=message):
        schema.validate_profile_dict(_mutated(where, key, value))


def test_schema_accepts_the_two_new_intents() -> None:
    data = _load_builtin()
    data["capabilities"] = {
        "adaptive_mode": {
            "intent": "set_adaptive_mode",
            "min_firmware": "1.4.4",
            "write": {
                "endpoint": 1,
                "cluster": 0xFC11,
                "attribute": 0x6017,
                "encode": {"map": {"true": 2, "false": 0}},
            },
            "verify": {"rule": "one_of", "true": [2], "false": [0, 1]},
        },
        "frost_protection": {
            "intent": "set_frost_temperature",
            "write": {
                "endpoint": 1,
                "cluster": 0xFC11,
                "attribute": 0x6002,
                "encode": "temperature_centi",
                "range": [4, 35],
            },
            "verify": {"rule": "equals"},
        },
    }
    validated = schema.validate_profile_dict(data)
    assert validated["capabilities"]["adaptive_mode"]["verify"]["false"] == [0, 1]
    assert validated["capabilities"]["frost_protection"]["write"]["range"] == [
        4.0,
        35.0,
    ]
