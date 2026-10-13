"""Room-logic data model (pure Python)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.easy_trv.core.values import Version

# --- Intents -------------------------------------------------------------


@dataclass(frozen=True)
class SetRoomTemperature:
    """Tell the thermostat the room temperature measured by an external sensor."""

    celsius: float


@dataclass(frozen=True)
class SetSource:
    """Select which temperature source the thermostat regulates against."""

    source: Literal["external", "internal"]


@dataclass(frozen=True)
class SetWindowDetection:
    """Enable or disable the thermostat's open-window detection."""

    enabled: bool


@dataclass(frozen=True)
class SetHysteresis:
    """Set the thermostat's temperature hysteresis."""

    kelvin: float


Intent = SetRoomTemperature | SetSource | SetWindowDetection | SetHysteresis

INTENT_NAMES: dict[type, str] = {
    SetRoomTemperature: "set_room_temperature",
    SetSource: "set_source",
    SetWindowDetection: "set_window_detection",
    SetHysteresis: "set_hysteresis",
}

# --- Enums ---------------------------------------------------------------


class Source(StrEnum):
    """Temperature source the room's thermostats currently regulate against."""

    EXTERNAL = "external"
    INTERNAL = "internal"
    NONE = "none"


class TrvHealth(StrEnum):
    """Health of a single thermostat as seen by the room logic."""

    OK = "ok"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


class RoomStatus(StrEnum):
    """Overall status of a room."""

    OK = "ok"
    DEGRADED = "degraded"
    ERROR = "error"


# --- Configuration -------------------------------------------------------


@dataclass(frozen=True)
class GlobalParams:
    """Integration-wide tunables shared by all rooms."""

    refresh: timedelta
    verify_delay: float
    max_attempts: int
    min_delta: float
    sensor_timeout: timedelta | None
    debounce: float = 5.0
    backoff: tuple[float, ...] = (5.0, 15.0, 45.0)

    @classmethod
    def defaults(cls) -> GlobalParams:
        """Return the default tunables (mirrors the ``DEFAULT_*`` constants)."""
        return cls(
            refresh=timedelta(minutes=20),
            verify_delay=10.0,
            max_attempts=3,
            min_delta=0.1,
            sensor_timeout=timedelta(minutes=120),
        )

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> GlobalParams:
        """Build the tunables from the config entry options.

        A missing option falls back to its default and a ``sensor_timeout_minutes``
        of ``0`` switches the timeout off. The literals mirror the ``DEFAULT_*``
        constants, which core cannot import.
        """
        sensor_timeout = options.get("sensor_timeout_minutes", 120)
        return cls(
            refresh=timedelta(minutes=options.get("refresh_minutes", 20)),
            verify_delay=float(options.get("verify_delay_seconds", 10)),
            max_attempts=int(options.get("max_attempts", 3)),
            min_delta=float(options.get("min_delta_kelvin", 0.1)),
            sensor_timeout=(
                timedelta(minutes=sensor_timeout) if sensor_timeout else None
            ),
        )


@dataclass(frozen=True)
class TrvConfig:
    """One thermostat of a room."""

    entity_id: str
    ieee: str
    firmware: Version | None


@dataclass(frozen=True)
class RoomConfig:
    """Static configuration of a room."""

    room_id: str
    name: str
    trvs: tuple[TrvConfig, ...]
    temp_sensors: tuple[str, ...]
    window_detection: Literal["on", "off", "leave"]
    hysteresis: float | None


# --- State ---------------------------------------------------------------


@dataclass(frozen=True)
class SensorReading:
    """Latest known value of a temperature sensor."""

    entity_id: str
    value: float | None
    last_reported: datetime | None


@dataclass(frozen=True)
class TrvState:
    """Mutable-by-replacement state of one thermostat."""

    health: TrvHealth = TrvHealth.OK
    available: bool = True
    attempts: Mapping[str, int] = field(default_factory=dict)
    pending_retry: Mapping[str, Intent] = field(default_factory=dict)
    last_error: str | None = None
    error_capability: str | None = None
    last_error_retry: datetime | None = None
    failsafe_seen_at: datetime | None = None
    unsupported: frozenset[str] = frozenset()
    settings_applied: bool = False


@dataclass(frozen=True)
class RoomState:
    """State of a room, replaced (never mutated) on every event."""

    readings: Mapping[str, SensorReading]
    trvs: Mapping[str, TrvState]
    source: Source = Source.NONE
    active_sensor: str | None = None
    last_written: float | None = None
    last_value_write: datetime | None = None
    last_value_issued: datetime | None = None
    debounce_pending: bool = False

    @classmethod
    def initial(cls, cfg: RoomConfig) -> RoomState:
        """Return the state of a room that has not started yet."""
        return cls(readings={}, trvs={trv.entity_id: TrvState() for trv in cfg.trvs})


# --- Outcomes ------------------------------------------------------------


@dataclass(frozen=True)
class Ok:
    """The operation was written and read back correctly."""


@dataclass(frozen=True)
class Mismatch:
    """The read-back value differs from what was written."""

    expected: frozenset[int]
    actual: int | None


@dataclass(frozen=True)
class Failed:
    """The operation could not be carried out."""

    error: str


@dataclass(frozen=True)
class Unsupported:
    """The installed firmware lacks the capability."""

    capability: str
    required: str
    installed: str


@dataclass(frozen=True)
class Signal:
    """The read-back carries a named signal rather than a plain value."""

    name: str


Outcome = Ok | Mismatch | Failed | Unsupported | Signal

# --- Events --------------------------------------------------------------


@dataclass(frozen=True)
class Start:
    """The room is set up; initial sensor readings and availability are known."""

    sensors: tuple[SensorReading, ...]
    trv_available: Mapping[str, bool]


@dataclass(frozen=True)
class SensorChanged:
    """A temperature sensor reported a new reading."""

    reading: SensorReading


@dataclass(frozen=True)
class TrvAvailabilityChanged:
    """A thermostat became available or unavailable."""

    entity_id: str
    available: bool


@dataclass(frozen=True)
class Tick:
    """Periodic wake-up for timeouts and refresh."""


@dataclass(frozen=True)
class DebounceElapsed:
    """The debounce timer armed by a sensor change fired."""


@dataclass(frozen=True)
class RetryDue:
    """A retry timer for a pending operation fired."""

    trv: str
    capability: str


@dataclass(frozen=True)
class OperationResult:
    """A previously issued operation finished."""

    trv: str
    intent: Intent
    capability: str
    outcome: Outcome


Event = (
    Start
    | SensorChanged
    | TrvAvailabilityChanged
    | Tick
    | DebounceElapsed
    | RetryDue
    | OperationResult
)

# --- Commands ------------------------------------------------------------

# Bus event names; they mirror ``const.EVENT_*``, which core cannot import
# because ``const`` needs Home Assistant.
EVENT_WRITE_FAILED: Final = "easy_trv_write_failed"
EVENT_RECOVERED: Final = "easy_trv_recovered"
EVENT_FAILSAFE: Final = "easy_trv_failsafe_detected"

IssueKind = Literal["unreachable", "firmware"]


@dataclass(frozen=True)
class Execute:
    """Carry out an intent on one thermostat."""

    trv: str
    intent: Intent


@dataclass(frozen=True)
class ScheduleDebounce:
    """Arm (or re-arm) the debounce timer."""

    seconds: float


@dataclass(frozen=True)
class ScheduleRetry:
    """Arm a retry timer for one thermostat capability."""

    trv: str
    capability: str
    seconds: float


@dataclass(frozen=True)
class SetStatus:
    """Publish the room status."""

    status: RoomStatus
    attributes: Mapping[str, Any]


@dataclass(frozen=True)
class RaiseIssue:
    """Create or update a repair issue."""

    kind: IssueKind
    trv: str
    placeholders: Mapping[str, str]


@dataclass(frozen=True)
class ClearIssue:
    """Delete a repair issue."""

    kind: IssueKind
    trv: str


@dataclass(frozen=True)
class FireEvent:
    """Fire a Home Assistant bus event."""

    name: str
    data: Mapping[str, Any]


@dataclass(frozen=True)
class Log:
    """Write a log line."""

    level: Literal["debug", "info", "warning"]
    message: str


Command = (
    Execute
    | ScheduleDebounce
    | ScheduleRetry
    | SetStatus
    | RaiseIssue
    | ClearIssue
    | FireEvent
    | Log
)
