"""Settings resolution: built-in defaults, global options and room overrides.

Pure Python. ``const.py`` re-exports the names defined here.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

GLOBAL: Final = "global"
_LEAVE: Final = "leave"

DEFAULT_REFRESH_MINUTES: Final = 20
DEFAULT_VERIFY_DELAY: Final = 10
DEFAULT_MAX_ATTEMPTS: Final = 3
DEFAULT_MIN_DELTA: Final = 0.1
DEFAULT_SENSOR_TIMEOUT: Final = 120
DEFAULT_WINDOW_DETECTION: Final = "on"
DEFAULT_HYSTERESIS: Final = "0_4"
DEFAULT_ADAPTIVE_MODE: Final = "on"
DEFAULT_FROST_TEMPERATURE: Final = 5.0
DEFAULT_PROFILE: Final = "auto"

_KEY_HYSTERESIS: Final = "hysteresis"

# Built-in default of every setting. The keys are the ``CONF_*`` values.
DEFAULTS: Final[Mapping[str, Any]] = {
    "refresh_minutes": DEFAULT_REFRESH_MINUTES,
    "verify_delay_seconds": DEFAULT_VERIFY_DELAY,
    "max_attempts": DEFAULT_MAX_ATTEMPTS,
    "min_delta_kelvin": DEFAULT_MIN_DELTA,
    "sensor_timeout_minutes": DEFAULT_SENSOR_TIMEOUT,
    "window_detection": DEFAULT_WINDOW_DETECTION,
    _KEY_HYSTERESIS: DEFAULT_HYSTERESIS,
    "adaptive_mode": DEFAULT_ADAPTIVE_MODE,
    "frost_temperature": DEFAULT_FROST_TEMPERATURE,
    "profile": DEFAULT_PROFILE,
}


@dataclass(frozen=True)
class RoomSettings:
    """The resolved settings of one room."""

    refresh_minutes: int
    verify_delay_seconds: int
    max_attempts: int
    min_delta_kelvin: float
    sensor_timeout_minutes: int
    window_detection: Literal["on", "off", "leave"]
    hysteresis: float  # kelvin, 0.2..1.0
    adaptive_mode: Literal["on", "off", "leave"]
    frost_temperature: float  # celsius
    profile: str  # "auto" or a profile id


def hysteresis_kelvin(stored: str | float) -> float:
    """Return a stored hysteresis choice in kelvin.

    The choice is a translation-safe key (``0_4``), not a number. The dotted
    form (``0.4``) and plain numbers are accepted as well. Python's own
    ``float("0_4")`` would read this as 4.0, so the separator is replaced
    explicitly.
    """
    return float(str(stored).replace("_", "."))


def _is_set(key: str, value: object) -> bool:
    """Return whether ``value`` is a real choice for ``key``.

    ``None`` and ``"global"`` mean "use the next layer". ``"leave"`` is the
    retired hysteresis choice and also means "use the next layer".
    """
    if value is None or value == GLOBAL:
        return False
    return not (key == _KEY_HYSTERESIS and value == _LEAVE)


def overrides(room: Mapping[str, Any]) -> dict[str, Any]:
    """Return the settings a room sets itself, i.e. the keys that are not global."""
    return {
        key: room[key] for key in DEFAULTS if key in room and _is_set(key, room[key])
    }


def _coerce(key: str, value: Any) -> Any:
    """Convert a stored value to the type of the setting ``key``."""
    if key == _KEY_HYSTERESIS:
        return hysteresis_kelvin(value)
    default = DEFAULTS[key]
    if isinstance(default, int):
        return round(value)
    if isinstance(default, float):
        return float(value)
    return value


def resolve_settings(
    options: Mapping[str, Any], room: Mapping[str, Any]
) -> RoomSettings:
    """Resolve every setting: room value, else global value, else default.

    Keys in ``room`` and ``options`` that are not settings are ignored.
    """
    room_values = overrides(room)
    global_values = overrides(options)
    resolved = {
        key: _coerce(key, room_values.get(key, global_values.get(key, default)))
        for key, default in DEFAULTS.items()
    }
    return RoomSettings(**resolved)
