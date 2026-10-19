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
DEFAULT_HIDE_THERMOSTAT_ENTITIES: Final = "off"
DEFAULT_PROFILE: Final = "auto"
DEFAULT_SETPOINT_RANGE: Final = "auto"
DEFAULT_SETPOINT_MIN: Final = 5.0
DEFAULT_SETPOINT_MAX: Final = 30.0
DEFAULT_WINDOW_WARN_MINUTES: Final = 120
DEFAULT_WINDOW_WARN_COLDER: Final = "on"
DEFAULT_WINDOW_CHECK_MINUTES: Final = 30
DEFAULT_WINDOW_CHECK_DROP: Final = 0.3
DEFAULT_OUTDOOR_CUTOFF_CELSIUS: Final[float | None] = None  # None = off
DEFAULT_WINDOW_OPEN_DELAY_SECONDS: Final = 30
DEFAULT_STATISTICS: Final = "off"
DEFAULT_PRESENCE_UNKNOWN: Final = "away"
DEFAULT_AWAY_ACTION: Final = "setback"
DEFAULT_AWAY_SETBACK: Final = 3.0

_KEY_HYSTERESIS: Final = "hysteresis"
_KEY_SETPOINT_RANGE: Final = "setpoint_range"
_KEY_SETPOINT_MIN: Final = "setpoint_min"
_KEY_SETPOINT_MAX: Final = "setpoint_max"
_KEY_OUTDOOR_CUTOFF: Final = "outdoor_cutoff"  # room-only switch: global / off
_KEY_OUTDOOR_CUTOFF_CELSIUS: Final = "outdoor_cutoff_celsius"
_KEY_PRESENCE: Final = "presence"  # room-only: global / none / own
_KEY_PRESENCE_ENTITY: Final = "presence_entity"
_OFF: Final = "off"
# Settings whose default is None but that are numbers when set.
_FLOAT_KEYS: Final = frozenset({_KEY_OUTDOOR_CUTOFF_CELSIUS})

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
    "hide_thermostat_entities": DEFAULT_HIDE_THERMOSTAT_ENTITIES,
    "profile": DEFAULT_PROFILE,
    _KEY_SETPOINT_RANGE: DEFAULT_SETPOINT_RANGE,
    _KEY_SETPOINT_MIN: DEFAULT_SETPOINT_MIN,
    _KEY_SETPOINT_MAX: DEFAULT_SETPOINT_MAX,
    "window_warn_minutes": DEFAULT_WINDOW_WARN_MINUTES,
    "window_warn_colder": DEFAULT_WINDOW_WARN_COLDER,
    "window_check_minutes": DEFAULT_WINDOW_CHECK_MINUTES,
    "window_check_drop_kelvin": DEFAULT_WINDOW_CHECK_DROP,
    _KEY_OUTDOOR_CUTOFF_CELSIUS: DEFAULT_OUTDOOR_CUTOFF_CELSIUS,
    "window_open_delay_seconds": DEFAULT_WINDOW_OPEN_DELAY_SECONDS,
    "statistics": DEFAULT_STATISTICS,
    "presence_unknown": DEFAULT_PRESENCE_UNKNOWN,
    "away_action": DEFAULT_AWAY_ACTION,
    "away_setback_kelvin": DEFAULT_AWAY_SETBACK,
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
    # on: the thermostat entities Easy TRV controls are hidden in Home Assistant
    hide_thermostat_entities: Literal["on", "off"]
    profile: str  # "auto" or a profile id
    # auto: the limits the thermostats show; manual: setpoint_min..setpoint_max
    setpoint_range: Literal["auto", "manual"]
    setpoint_min: float  # celsius
    setpoint_max: float  # celsius
    window_warn_minutes: int  # 0 = off
    # on: the warning waits while it is not colder outside than inside
    window_warn_colder: Literal["on", "off"]
    window_check_minutes: int  # 0 = off
    window_check_drop_kelvin: float
    outdoor_cutoff_celsius: float | None  # None = off
    window_open_delay_seconds: int  # 0 = an open window counts at once
    # off: no statistics sensors; hidden: they collect but are hidden in Home
    # Assistant; visible: they collect and are shown
    statistics: Literal["off", "hidden", "visible"]
    # How an unknown presence counts: away (default) or home
    presence_unknown: Literal["away", "home"]
    # What away does: lower the target or switch the room off
    away_action: Literal["setback", "off"]
    away_setback_kelvin: float


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
    """Return the settings a room sets itself, i.e. the keys that are not global.

    ``outdoor_cutoff`` and ``presence`` are room-only switches, not settings
    with a default. They are kept when they are not ``global``.
    """
    return {
        key: room[key]
        for key in (*DEFAULTS, _KEY_OUTDOOR_CUTOFF, _KEY_PRESENCE)
        if key in room and _is_set(key, room[key])
    }


def resolve_presence_entity(
    options: Mapping[str, Any], room: Mapping[str, Any]
) -> str | None:
    """Return the presence entity of a room, ``None`` if it has no presence.

    The room's ``presence`` decides: ``global`` (or nothing) takes the entity of
    the options, ``none`` takes none, ``own`` takes the room's own entity.
    """
    choice = room.get(_KEY_PRESENCE) or GLOBAL
    if choice == "none":
        return None
    layer = room if choice == "own" else options
    return layer.get(_KEY_PRESENCE_ENTITY) or None


def _coerce(key: str, value: Any) -> Any:
    """Convert a stored value to the type of the setting ``key``."""
    if key == _KEY_HYSTERESIS:
        return hysteresis_kelvin(value)
    default = DEFAULTS[key]
    if key in _FLOAT_KEYS:
        return None if value is None else float(value)
    if isinstance(default, int):
        return round(value)
    if isinstance(default, float):
        return float(value)
    return value


def _with_range_choice(layer: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return ``layer`` with ``setpoint_range: manual`` if it predates that choice.

    Options and rooms saved before 0.4.0 have no range choice; their own
    minimum or maximum was meant as the range, so they count as manual.
    """
    if _KEY_SETPOINT_RANGE in layer or not any(
        _is_set(key, layer.get(key)) for key in (_KEY_SETPOINT_MIN, _KEY_SETPOINT_MAX)
    ):
        return layer
    return {**layer, _KEY_SETPOINT_RANGE: "manual"}


def resolve_settings(
    options: Mapping[str, Any], room: Mapping[str, Any]
) -> RoomSettings:
    """Resolve every setting: room value, else global value, else default.

    The outdoor cut-off threshold is also ``None`` (off) when the room's
    ``outdoor_cutoff`` switch is ``off``, which wins over any number. Keys in
    ``room`` and ``options`` that are not settings are ignored.

    Raises:
        ValueError: ``setpoint_min`` is not below ``setpoint_max``.

    """
    room_values = overrides(_with_range_choice(room))
    global_values = overrides(_with_range_choice(options))
    resolved = {
        key: _coerce(key, room_values.get(key, global_values.get(key, default)))
        for key, default in DEFAULTS.items()
    }
    if room_values.get(_KEY_OUTDOOR_CUTOFF) == _OFF:
        resolved[_KEY_OUTDOOR_CUTOFF_CELSIUS] = None
    if resolved[_KEY_SETPOINT_MIN] >= resolved[_KEY_SETPOINT_MAX]:
        msg = "setpoint_min must be below setpoint_max"
        raise ValueError(msg)
    return RoomSettings(**resolved)
