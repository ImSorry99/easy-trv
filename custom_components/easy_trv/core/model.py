"""Room-logic data model (pure Python)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Literal

from custom_components.easy_trv.core.settings import resolve_settings

if TYPE_CHECKING:
    from collections.abc import Mapping

    from custom_components.easy_trv.core.settings import RoomSettings
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


@dataclass(frozen=True)
class SetAdaptiveMode:
    """Enable or disable the thermostat's adaptive (learning) regulation."""

    enabled: bool


@dataclass(frozen=True)
class SetFrostTemperature:
    """Set the temperature the thermostat holds as frost protection."""

    celsius: float


@dataclass(frozen=True)
class SetSetpoint:
    """Set the target temperature the thermostat heats to."""

    celsius: float


@dataclass(frozen=True)
class SetHvacMode:
    """Switch the thermostat's heating on (``heat``) or off."""

    mode: Literal["heat", "off"]


Intent = (
    SetRoomTemperature
    | SetSource
    | SetWindowDetection
    | SetAdaptiveMode
    | SetHysteresis
    | SetFrostTemperature
    | SetSetpoint
    | SetHvacMode
)

INTENT_NAMES: dict[type, str] = {
    SetRoomTemperature: "set_room_temperature",
    SetSource: "set_source",
    SetWindowDetection: "set_window_detection",
    SetAdaptiveMode: "set_adaptive_mode",
    SetHysteresis: "set_hysteresis",
    SetFrostTemperature: "set_frost_temperature",
    SetSetpoint: "set_setpoint",
    SetHvacMode: "set_hvac_mode",
}

# The settings a room applies to a thermostat, in the order they are written.
# SetSetpoint and SetHvacMode are room state, not settings, and stay out.
SETTING_INTENTS: Final = (
    SetWindowDetection,
    SetAdaptiveMode,
    SetHysteresis,
    SetFrostTemperature,
)

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


class RoomMode(StrEnum):
    """Whether the room heats or is switched off."""

    HEAT = "heat"
    OFF = "off"


class Preset(StrEnum):
    """Whether the room holds its frost protection temperature (internal)."""

    NONE = "none"
    FROST = "frost"


class OperatingMode(StrEnum):
    """Operating mode of a room: off, frost protection only, or heating."""

    OFF = "off"
    FROST = "frost"
    HEAT = "heat"


class Note(StrEnum):
    """Why a thermostat's reported value is not to be taken as a user change."""

    WINDOW_PHASE = "window_phase"
    SCHEDULE = "schedule"


# --- Configuration -------------------------------------------------------


@dataclass(frozen=True)
class RoomParams:
    """Tunables of one room: timing, retries and sensor handling."""

    refresh: timedelta
    verify_delay: float
    max_attempts: int
    min_delta: float
    sensor_timeout: timedelta | None
    debounce: float = 5.0
    backoff: tuple[float, ...] = (5.0, 15.0, 45.0)

    @classmethod
    def from_settings(cls, settings: RoomSettings) -> RoomParams:
        """Build the tunables from the resolved settings of a room.

        A ``sensor_timeout_minutes`` of ``0`` switches the timeout off.
        """
        timeout = settings.sensor_timeout_minutes
        return cls(
            refresh=timedelta(minutes=settings.refresh_minutes),
            verify_delay=float(settings.verify_delay_seconds),
            max_attempts=settings.max_attempts,
            min_delta=settings.min_delta_kelvin,
            sensor_timeout=timedelta(minutes=timeout) if timeout else None,
        )

    @classmethod
    def defaults(cls) -> RoomParams:
        """Return the tunables of a room that sets nothing itself."""
        return cls.from_settings(resolve_settings({}, {}))


@dataclass(frozen=True)
class TrvConfig:
    """One thermostat of a room."""

    entity_id: str
    ieee: str
    firmware: Version | None


@dataclass(frozen=True)
class RoomConfig:
    """Static configuration of a room.

    The thermostat settings come from the resolved settings of the room.
    ``"leave"`` keeps the thermostat's own window detection or adaptive mode.
    """

    room_id: str
    name: str
    trvs: tuple[TrvConfig, ...]
    temp_sensors: tuple[str, ...]
    window_detection: Literal["on", "off", "leave"]
    hysteresis: float  # kelvin
    adaptive_mode: Literal["on", "off", "leave"]
    frost_temperature: float  # celsius
    # Effective (min, max, step); None = no climate (profile lacks the capabilities).
    setpoint_range: tuple[float, float, float] | None = None
    window_contacts: tuple[str, ...] = ()
    window_warn: timedelta | None = None
    # Raise the long-open warning only while it is colder outside than inside.
    window_warn_only_colder: bool = False
    window_check: timedelta | None = None
    window_check_drop: float = 0.3
    outdoor_cutoff: float | None = None
    # How long a contact must stay open before the window counts as open;
    # ``None``: at once.
    window_open_delay: timedelta | None = None
    # What away does; ``None``: the room has no presence entity.
    away_action: Literal["setback", "off"] | None = None
    # How far a room is lowered while away (kelvin).
    away_setback: float = 3.0


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
    # Capabilities whose attempts were used up and that have not arrived since,
    # each with the last error text of its own. Only a verified write, a
    # read-back that showed another value, or a missing function ends it. The
    # Repairs notice stays as long as the room still writes one of them.
    given_up: frozenset[str] = frozenset()
    errors: Mapping[str, str] = field(default_factory=dict)
    # The part of ``given_up`` that is being written again and whose result is
    # still outstanding: it is not known yet whether it fails once more.
    resent: frozenset[str] = frozenset()
    last_error_retry: datetime | None = None
    failsafe_seen_at: datetime | None = None
    unsupported: frozenset[str] = frozenset()
    # The part of ``unsupported`` the profile does not declare at all: those
    # are no firmware problem and do not affect the health.
    not_in_profile: frozenset[str] = frozenset()
    settings_applied: bool = False
    reported_setpoint: float | None = None
    reported_mode: str | None = None
    note: Note | None = None
    note_since: datetime | None = None
    # capability -> recent writes as (value, at), to tell echoes from user changes
    written_recently: Mapping[str, tuple[tuple[float | str, datetime], ...]] = field(
        default_factory=dict
    )
    # capability -> last observed value awaiting the knob debounce
    knob_pending: Mapping[str, float | str] = field(default_factory=dict)
    # capability -> value written to bring the thermostat in line (it came back,
    # or left its schedule or window phase) whose result is still outstanding:
    # reports of it are not adopted meanwhile, they may still show the old value
    resync_pending: Mapping[str, float | str] = field(default_factory=dict)
    # capability -> when reported_setpoint / reported_mode was last reported
    reported_at: Mapping[str, datetime] = field(default_factory=dict)
    # capability -> when a value that was no echo of our writes was last reported:
    # writes from before it no longer vouch for the values the thermostat reports
    foreign_at: Mapping[str, datetime] = field(default_factory=dict)
    # Switched off by a hold while it ran its own schedule: the room leaves it
    # off after the hold (it never writes ``auto``) until it reports auto or heat.
    left_off: bool = False


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
    setpoint: float | None = None
    mode: RoomMode = RoomMode.HEAT
    preset: Preset = Preset.NONE
    frost_return: float | None = None
    # Origin of the setpoint: ha, device:<entity_id>, restore, frost or initial.
    setpoint_source: str | None = None
    window_open: bool = False
    window_opened_at: datetime | None = None
    window_open_value: float | None = None
    # The sensor ``window_open_value`` came from (the stuck check needs the same).
    window_open_sensor: str | None = None
    outdoor_hold: bool = False
    # The latest outdoor temperature; None = unknown or no outdoor sensor.
    outdoor: float | None = None
    # Presence reads away (after the unknown policy); only matters with an
    # ``away_action``.
    away: bool = False
    # A target set while away: the room heats normally until presence is home.
    away_override: bool = False
    window_warned: bool = False
    window_suspect: bool = False
    # Whether the stuck-contact check already ran for the current opening.
    window_checked: bool = False

    @classmethod
    def initial(cls, cfg: RoomConfig) -> RoomState:
        """Return the state of a room that has not started yet."""
        return cls(
            readings={},
            trvs={trv.entity_id: TrvState() for trv in cfg.trvs},
            mode=RoomMode.HEAT,
            preset=Preset.NONE,
        )


# --- Outcomes ------------------------------------------------------------


@dataclass(frozen=True)
class Ok:
    """The operation was written and read back correctly."""


@dataclass(frozen=True)
class Mismatch:
    """The read-back value differs from what was written."""

    expected: frozenset[int]
    actual: int | None
    # Decoded actual value, set by the controller for observed capabilities.
    observed: float | str | None = None


@dataclass(frozen=True)
class Failed:
    """The operation could not be carried out."""

    error: str


@dataclass(frozen=True)
class Unsupported:
    """The thermostat cannot do the capability.

    ``reason`` tells why: ``"firmware"`` (below the minimum the profile names),
    ``"device"`` (the device answers ``UNSUPPORTED_ATTRIBUTE``) or ``"profile"``
    (the profile declares no such capability, which is no firmware problem).
    """

    capability: str
    required: str
    installed: str
    reason: Literal["firmware", "device", "profile"] = "firmware"


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
    # trv -> (setpoint, mode) from the Home Assistant states
    reported: Mapping[str, tuple[float | None, str | None]] = field(
        default_factory=dict
    )
    # The hold inputs at start: a window contact already open, the outdoor
    # temperature (``None``: unknown or not watched). They hold the room before
    # it compares the thermostats with its values.
    window_open: bool = False
    outdoor: float | None = None
    # Presence reads away at start (after the unknown policy).
    away: bool = False


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


@dataclass(frozen=True)
class SetRoomSetpoint:
    """The user asked the room for a target temperature."""

    celsius: float


@dataclass(frozen=True)
class SetRoomMode:
    """The user switched the room on or off."""

    mode: RoomMode


@dataclass(frozen=True)
class SetRoomOperation:
    """The user selected the operating mode of the room."""

    operation: OperatingMode


@dataclass(frozen=True)
class RestoreRoom:
    """Restore the room state remembered across a restart."""

    setpoint: float | None
    mode: RoomMode | None
    preset: Preset | None
    # The setpoint to return to when frost protection ends.
    frost_return: float | None = None


@dataclass(frozen=True)
class TrvObserved:
    """A thermostat reported a value of one capability."""

    trv: str
    capability: str
    value: float | str | None
    raw: int
    kind: str = "report"


@dataclass(frozen=True)
class KnobSettled:
    """The knob debounce of one thermostat capability elapsed."""

    trv: str
    capability: str


@dataclass(frozen=True)
class WindowChanged:
    """The window contacts changed between open and closed."""

    open: bool


@dataclass(frozen=True)
class PresenceChanged:
    """Presence changed between home and away (after the unknown policy)."""

    away: bool


@dataclass(frozen=True)
class OutdoorChanged:
    """The outdoor temperature changed; ``None`` means unknown."""

    celsius: float | None


Event = (
    Start
    | SensorChanged
    | TrvAvailabilityChanged
    | Tick
    | DebounceElapsed
    | RetryDue
    | OperationResult
    | SetRoomSetpoint
    | SetRoomMode
    | SetRoomOperation
    | RestoreRoom
    | TrvObserved
    | KnobSettled
    | WindowChanged
    | OutdoorChanged
    | PresenceChanged
)

# --- Commands ------------------------------------------------------------

# Bus event names; they mirror ``const.EVENT_*``, which core cannot import
# because ``const`` needs Home Assistant.
EVENT_WRITE_FAILED: Final = "easy_trv_write_failed"
EVENT_RECOVERED: Final = "easy_trv_recovered"
EVENT_FAILSAFE: Final = "easy_trv_failsafe_detected"
EVENT_SETPOINT_ADOPTED: Final = "easy_trv_setpoint_adopted"
EVENT_AWAY: Final = "easy_trv_away"

# ``unreachable``, ``firmware`` and ``schedule_thermostat_off`` belong to a
# thermostat (``trv`` is its entity); the window kinds belong to the room
# (``trv`` is the room id).
IssueKind = Literal[
    "unreachable",
    "firmware",
    "window_open_long",
    "window_contact_suspect",
    "schedule_thermostat_off",
]


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
class ScheduleKnobSettle:
    """Arm the knob debounce timer of one thermostat capability."""

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
    | ScheduleKnobSettle
    | SetStatus
    | RaiseIssue
    | ClearIssue
    | FireEvent
    | Log
)
