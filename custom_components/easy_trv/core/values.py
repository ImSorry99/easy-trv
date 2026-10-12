"""Temperature and firmware-version value helpers (pure Python)."""

import math
import re
from decimal import ROUND_HALF_UP, Decimal

type Version = tuple[int, int, int]

_TENTH = Decimal("0.1")
_CENTI_FACTOR = 100
_CELSIUS_UNITS = frozenset({"°C", "C"})
_FAHRENHEIT_UNITS = frozenset({"°F", "F"})
_DOTTED_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)", re.ASCII)
_HEX_VERSION = re.compile(r"(?:0[xX])?[0-9a-fA-F]+", re.ASCII)


def parse_version_string(s: str) -> Version:
    """Parse ``"major.minor.patch"`` into a version tuple.

    Raises:
        ValueError: If ``s`` is not exactly three dot-separated
            non-negative integers.

    """
    match = _DOTTED_VERSION.fullmatch(s)
    if match is None:
        msg = f"invalid version string: {s!r}"
        raise ValueError(msg)
    return int(match[1]), int(match[2]), int(match[3])


def parse_zha_sw_version(s: str | None) -> Version | None:
    """Parse a ZHA ``sw_version`` into a version tuple.

    Accepts a dotted ``"a.b.c"`` string or a hex string (with or without
    ``0x`` prefix) whose nibbles encode ``major`` (bits 15-12), ``minor``
    (bits 11-8) and ``patch`` (bits 7-0). Anything else yields ``None``.
    """
    if s is None:
        return None
    try:
        return parse_version_string(s)
    except ValueError:
        pass
    if _HEX_VERSION.fullmatch(s) is None:
        return None
    raw = int(s, 16)
    return (raw >> 12) & 0xF, (raw >> 8) & 0xF, raw & 0xFF


def round_tenth(celsius: float) -> float:
    """Round to one decimal place, ties away from zero (half-up)."""
    return float(Decimal(str(celsius)).quantize(_TENTH, rounding=ROUND_HALF_UP))


def to_centi(celsius: float) -> int:
    """Convert °C to centi-degrees after rounding to one decimal."""
    return int(Decimal(str(round_tenth(celsius))) * _CENTI_FACTOR)


def from_centi(raw: int) -> float:
    """Convert centi-degrees to °C."""
    return raw / _CENTI_FACTOR


def parse_temperature(state: str, unit: str | None) -> float | None:
    """Parse an entity state into °C, or ``None`` if it is not a temperature.

    Fahrenheit values are converted; unknown units, non-numeric states and
    non-finite numbers yield ``None``.
    """
    if unit is None or unit not in _CELSIUS_UNITS | _FAHRENHEIT_UNITS:
        return None
    try:
        value = float(state)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    if unit in _FAHRENHEIT_UNITS:
        return (value - 32) * 5 / 9
    return value


def is_in_range(celsius: float, low: float, high: float) -> bool:
    """Return whether ``celsius`` lies within ``[low, high]`` (inclusive)."""
    return low <= celsius <= high
