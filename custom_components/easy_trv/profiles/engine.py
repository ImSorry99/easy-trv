"""Profile engine: turns room intents into verified device operations.

This module is deliberately free of Home Assistant imports so the room logic
can be tested without a running instance.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from custom_components.easy_trv.core.model import (
    INTENT_NAMES,
    Intent,
    SetAdaptiveMode,
    SetFrostTemperature,
    SetHysteresis,
    SetRoomTemperature,
    SetSource,
    SetWindowDetection,
)
from custom_components.easy_trv.core.values import (
    Version,
    is_in_range,
    parse_version_string,
    to_centi,
)

_CENTI_FACTOR = 100

type _Value = float | bool | str


def _version_text(version: Version | None) -> str:
    """Render a version tuple as ``"major.minor.patch"``."""
    return "unknown" if version is None else ".".join(map(str, version))


class CapabilityUnsupported(Exception):  # noqa: N818
    """The profile cannot carry out an intent on the installed firmware.

    ``required`` is ``None`` when the profile has no capability for the
    intent at all; in that case ``capability`` names the intent.
    """

    def __init__(
        self,
        capability: str,
        required: Version | None,
        installed: Version | None,
    ) -> None:
        """Store the capability name and the required and installed firmware."""
        super().__init__(capability, required, installed)
        self.capability = capability
        self.required = required
        self.installed = installed

    def __str__(self) -> str:
        """Describe what is missing."""
        if self.required is None:
            return f"profile has no capability for {self.capability!r}"
        return (
            f"capability {self.capability!r} needs firmware "
            f"{_version_text(self.required)} or newer, "
            f"installed {_version_text(self.installed)}"
        )


class ValueOutOfRange(ValueError):  # noqa: N818
    """An intent value lies outside the range declared by the capability."""


@dataclass(frozen=True)
class Operation:
    """One verified attribute write, fully resolved for the adapter."""

    capability: str
    endpoint: int
    cluster: int
    attribute: int
    raw_value: int
    accept: frozenset[int]
    signals: Mapping[int, str]
    also_read: tuple[int, ...]
    extra_signals: Mapping[int, Mapping[int, str]]


@dataclass(frozen=True)
class CapabilitySpec:
    """A capability as declared in a validated profile document.

    ``encode`` is the name of a built-in encoder, a constant raw value
    (``int``) or a lookup table from intent value to raw value (``Mapping``).
    ``verify_values`` holds the accepted raw values per intent value for the
    ``one_of`` rule and is empty for ``equals``.
    """

    name: str
    intent: str
    min_firmware: Version | None
    endpoint: int
    cluster: int
    attribute: int
    encode: str | int | Mapping[str, int]
    value_range: tuple[float, float] | None
    verify_rule: str
    verify_values: Mapping[str, tuple[int, ...]]
    signals: Mapping[int, str]
    also_read: tuple[int, ...]


def _intent_value(intent: Intent) -> _Value:
    """Return the value carried by an intent."""
    match intent:
        case SetRoomTemperature(celsius=value):
            return value
        case SetHysteresis(kelvin=value):
            return value
        case SetFrostTemperature(celsius=value):
            return value
        case SetAdaptiveMode(enabled=value):
            return value
        case SetWindowDetection(enabled=value):
            return value
        case SetSource(source=value):
            return value


def _value_key(value: _Value) -> str:
    """Return the lookup key for ``map`` encoders and ``one_of`` rules."""
    return value if isinstance(value, str) else str(value).lower()


def _numeric(spec: CapabilitySpec, value: _Value) -> float:
    """Return ``value`` as a finite number.

    Raises:
        ValueError: If the value is not a number at all (a profile problem).
        ValueOutOfRange: If the value is NaN or infinite (a bad reading).

    """
    if isinstance(value, bool | str):
        msg = f"capability {spec.name!r} needs a number, got {value!r}"
        raise ValueError(msg)  # noqa: TRY004 - the contract is ValueError
    if not math.isfinite(value):
        msg = f"capability {spec.name!r}: {value} is not a finite number"
        raise ValueOutOfRange(msg)
    return value


def _check_range(spec: CapabilitySpec, value: _Value) -> None:
    """Raise ``ValueOutOfRange`` if the numeric value violates the range.

    Non-finite numbers never lie within a range, so they are out of range too.
    """
    if spec.value_range is None:
        return
    number = _numeric(spec, value)
    low, high = spec.value_range
    if not is_in_range(number, low, high):
        msg = f"capability {spec.name!r}: {number} is outside [{low}, {high}]"
        raise ValueOutOfRange(msg)


def _encode(spec: CapabilitySpec, value: _Value) -> int:
    """Encode the intent value into the raw attribute value."""
    encode = spec.encode
    if isinstance(encode, int):
        return encode
    if isinstance(encode, Mapping):
        try:
            return encode[_value_key(value)]
        except KeyError:
            msg = f"capability {spec.name!r} has no mapping for {value!r}"
            raise ValueError(msg) from None
    match encode:
        case "temperature_centi":
            return to_centi(_numeric(spec, value))
        case "kelvin_centi_negative":
            return -round(_numeric(spec, value) * _CENTI_FACTOR)
        case "bool" if isinstance(value, bool):
            return int(value)
    msg = f"capability {spec.name!r}: encoder {encode!r} cannot encode {value!r}"
    raise ValueError(msg)


def _accepted(spec: CapabilitySpec, raw_value: int, value: _Value) -> frozenset[int]:
    """Return the raw read-back values that confirm the write."""
    if spec.verify_rule == "equals":
        return frozenset({raw_value})
    accepted = spec.verify_values.get(_value_key(value))
    if accepted is None:
        msg = f"capability {spec.name!r} verifies no value for {value!r}"
        raise ValueError(msg)
    return frozenset(accepted)


def _build_capability(name: str, raw: dict[str, Any]) -> CapabilitySpec:
    """Build a capability from its validated document."""
    write = raw["write"]
    encode = write["encode"]
    if isinstance(encode, dict):
        encode = encode["const"] if "const" in encode else dict(encode["map"])
    value_range = write.get("range")
    verify = raw["verify"]
    verify_values = {
        key: tuple(values) for key, values in verify.items() if key != "rule"
    }
    if (
        verify["rule"] == "one_of"
        and isinstance(encode, dict)
        and set(verify_values) != set(encode)
    ):
        msg = (
            f"capability {name!r}: verify keys {sorted(verify_values)} "
            f"must match encode map keys {sorted(encode)}"
        )
        raise ValueError(msg)
    min_firmware = raw.get("min_firmware")
    return CapabilitySpec(
        name=name,
        intent=raw["intent"],
        min_firmware=(
            None if min_firmware is None else parse_version_string(min_firmware)
        ),
        endpoint=write["endpoint"],
        cluster=write["cluster"],
        attribute=write["attribute"],
        encode=MappingProxyType(encode) if isinstance(encode, dict) else encode,
        value_range=None if value_range is None else (value_range[0], value_range[1]),
        verify_rule=verify["rule"],
        verify_values=MappingProxyType(verify_values),
        signals=MappingProxyType(dict(raw.get("signals", {}))),
        also_read=tuple(raw.get("also_read", ())),
    )


@dataclass(frozen=True)
class Profile:
    """A device profile: which thermostat it drives and how."""

    id: str
    name: str
    manufacturer: str
    model: str
    min_firmware: Version
    tested_firmware: tuple[Version, ...]
    capabilities: Mapping[str, CapabilitySpec]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Profile:
        """Build a profile from a dict already checked by the schema.

        Raises:
            ValueError: If a ``one_of`` verify rule does not list exactly the
                keys of its capability's ``map`` encoder.

        """
        return cls(
            id=data["id"],
            name=data["name"],
            manufacturer=data["match"]["manufacturer"],
            model=data["match"]["model"],
            min_firmware=parse_version_string(data["min_firmware"]),
            tested_firmware=tuple(
                parse_version_string(version)
                for version in data.get("tested_firmware", ())
            ),
            capabilities=MappingProxyType(
                {
                    name: _build_capability(name, raw)
                    for name, raw in data["capabilities"].items()
                }
            ),
        )

    def matches(self, manufacturer: str, model: str) -> bool:
        """Return whether this profile fits the device (case-insensitive)."""
        return (
            manufacturer.strip().casefold() == self.manufacturer.strip().casefold()
            and model.strip().casefold() == self.model.strip().casefold()
        )

    def device_supported(self, firmware: Version | None) -> bool:
        """Return whether the profile may drive this firmware (unknown: yes)."""
        return firmware is None or firmware >= self.min_firmware

    def capability_for(self, intent: Intent) -> str | None:
        """Return the name of the capability that handles the intent, if any."""
        wanted = INTENT_NAMES[type(intent)]
        return next(
            (name for name, spec in self.capabilities.items() if spec.intent == wanted),
            None,
        )

    def supports(self, capability: str, firmware: Version | None) -> bool:
        """Return whether the capability exists and works on this firmware."""
        spec = self.capabilities.get(capability)
        if spec is None:
            return False
        return firmware is None or firmware >= self._required_firmware(spec)

    def plan(self, intent: Intent, firmware: Version | None) -> Operation:
        """Turn an intent into the operation that carries it out.

        Raises:
            CapabilityUnsupported: If the profile has no capability for the
                intent or the firmware is below the capability or profile
                minimum.
            ValueOutOfRange: If the intent value violates the capability range.
            ValueError: If the value cannot be encoded or verified.

        """
        name = self.capability_for(intent)
        if name is None:
            raise CapabilityUnsupported(INTENT_NAMES[type(intent)], None, firmware)
        spec = self.capabilities[name]
        required = self._required_firmware(spec)
        if firmware is not None and firmware < required:
            raise CapabilityUnsupported(name, required, firmware)
        value = _intent_value(intent)
        _check_range(spec, value)
        raw_value = _encode(spec, value)
        return Operation(
            capability=name,
            endpoint=spec.endpoint,
            cluster=spec.cluster,
            attribute=spec.attribute,
            raw_value=raw_value,
            accept=_accepted(spec, raw_value, value),
            signals=spec.signals,
            also_read=spec.also_read,
            extra_signals=self._extra_signals(spec),
        )

    def _required_firmware(self, spec: CapabilitySpec) -> Version:
        """Return the lowest firmware on which the capability may be used."""
        if spec.min_firmware is None:
            return self.min_firmware
        return max(self.min_firmware, spec.min_firmware)

    def _extra_signals(self, spec: CapabilitySpec) -> Mapping[int, Mapping[int, str]]:
        """Collect the signals of the capabilities owning each extra read."""
        extra: dict[int, Mapping[int, str]] = {}
        for attribute in spec.also_read:
            owner = next(
                (
                    other
                    for other in self.capabilities.values()
                    if (other.endpoint, other.cluster, other.attribute)
                    == (spec.endpoint, spec.cluster, attribute)
                ),
                None,
            )
            extra[attribute] = MappingProxyType({}) if owner is None else owner.signals
        return MappingProxyType(extra)
