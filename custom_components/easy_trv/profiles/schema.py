"""Profile vocabularies and the voluptuous schema for profile documents.

This module is deliberately free of Home Assistant imports so the profile
engine can stay pure Python.
"""

import math
import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol

if TYPE_CHECKING:
    from collections.abc import Callable

INTENTS = (
    "set_room_temperature",
    "set_source",
    "set_window_detection",
    "set_hysteresis",
)
ENCODERS = ("temperature_centi", "kelvin_centi_negative", "bool")
VERIFY_RULES = ("equals", "one_of")

_SCHEMA_VERSION = 1
_MAX_ENDPOINT = 240
_MAX_ZCL_ID = 0xFFFF
_RANGE_LENGTH = 2

_SLUG = vol.Match(re.compile(r"^[a-z][a-z0-9_]*\Z", re.ASCII))
_VERSION = vol.Match(re.compile(r"^\d+\.\d+\.\d+\Z", re.ASCII))
_NON_EMPTY_STR = vol.All(str, vol.Length(min=1))


def _integer(
    low: int | None = None, high: int | None = None
) -> Callable[[object], int]:
    """Build a validator for integers (booleans excluded) within optional bounds."""

    def validate(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            msg = "expected an integer"
            raise vol.Invalid(msg)
        if (low is not None and value < low) or (high is not None and value > high):
            msg = f"value must be between {low} and {high}"
            raise vol.Invalid(msg)
        return value

    return validate


def _number(value: object) -> float:
    """Validate a finite int or float (booleans excluded) and return it as float."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = "expected a number"
        raise vol.Invalid(msg)
    if not math.isfinite(value):
        msg = "expected a finite number"
        raise vol.Invalid(msg)
    return float(value)


def _schema_version(value: object) -> int:
    """Accept exactly the integer 1 (not ``True`` or ``1.0``)."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value != _SCHEMA_VERSION
    ):
        msg = f"unsupported schema version, expected {_SCHEMA_VERSION}"
        raise vol.Invalid(msg)
    return _SCHEMA_VERSION


def _value_range(value: object) -> list[float]:
    """Validate ``[low, high]`` with ``low <= high`` and return it as floats."""
    if not isinstance(value, list) or len(value) != _RANGE_LENGTH:
        msg = "range must be a list of [low, high]"
        raise vol.Invalid(msg)
    low, high = (_number(item) for item in value)
    if low > high:
        msg = "range low must not exceed high"
        raise vol.Invalid(msg)
    return [low, high]


_ENCODE = vol.Any(
    vol.In(ENCODERS),
    {vol.Required("map"): {_NON_EMPTY_STR: _integer()}},
    {vol.Required("const"): _integer()},
)

_WRITE = vol.Schema(
    {
        vol.Required("endpoint"): _integer(0, _MAX_ENDPOINT),
        vol.Required("cluster"): _integer(0, _MAX_ZCL_ID),
        vol.Required("attribute"): _integer(0, _MAX_ZCL_ID),
        vol.Required("encode"): _ENCODE,
        vol.Optional("range"): _value_range,
    },
)

_VERIFY_EQUALS = vol.Schema({vol.Required("rule"): "equals"})
_VERIFY_ONE_OF_VALUES = vol.Schema(
    {_NON_EMPTY_STR: vol.All([_integer()], vol.Length(min=1))},
)


def _verify(value: object) -> dict[str, Any]:
    """Validate a verify rule: ``equals`` or ``one_of`` with accepted-value lists."""
    if not isinstance(value, dict):
        msg = "verify must be a mapping"
        raise vol.Invalid(msg)
    rule = value.get("rule")
    if rule == "equals":
        equals: dict[str, Any] = _VERIFY_EQUALS(value)
        return equals
    if rule == "one_of":
        accepted = {key: items for key, items in value.items() if key != "rule"}
        if not accepted:
            msg = "rule 'one_of' needs at least one list of accepted values"
            raise vol.Invalid(msg)
        validated: dict[str, Any] = _VERIFY_ONE_OF_VALUES(accepted)
        return {"rule": "one_of", **validated}
    msg = f"rule must be one of {', '.join(VERIFY_RULES)}"
    raise vol.Invalid(msg)


_CAPABILITY = vol.Schema(
    {
        vol.Required("intent"): vol.In(INTENTS),
        vol.Optional("min_firmware"): _VERSION,
        vol.Required("write"): _WRITE,
        vol.Required("verify"): _verify,
        vol.Optional("signals", default=dict): {_integer(): _SLUG},
        vol.Optional("also_read", default=list): [_integer(0, _MAX_ZCL_ID)],
    },
)

PROFILE_SCHEMA = vol.Schema(
    {
        vol.Required("schema"): _schema_version,
        vol.Required("id"): _SLUG,
        vol.Required("name"): _NON_EMPTY_STR,
        vol.Required("match"): {
            vol.Required("manufacturer"): _NON_EMPTY_STR,
            vol.Required("model"): _NON_EMPTY_STR,
        },
        vol.Required("min_firmware"): _VERSION,
        vol.Optional("tested_firmware", default=list): [_VERSION],
        vol.Required("capabilities"): {_SLUG: _CAPABILITY},
    },
)


def validate_profile_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Validate a parsed profile document and return it with defaults applied.

    Raises:
        vol.Invalid: If the document violates the schema or two capabilities
            declare the same intent.

    """
    validated: dict[str, Any] = PROFILE_SCHEMA(data)
    seen: dict[str, str] = {}
    for name, capability in validated["capabilities"].items():
        intent = capability["intent"]
        if intent in seen:
            msg = (
                f"duplicate intent {intent!r} in capabilities "
                f"{seen[intent]!r} and {name!r}"
            )
            raise vol.Invalid(msg)
        seen[intent] = name
    return validated
