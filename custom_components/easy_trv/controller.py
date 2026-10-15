"""Room controller: runs one room's pure logic inside Home Assistant.

The controller is the only place where the state machine in
``core.room_logic`` meets Home Assistant and the Zigbee gateway. It feeds the
logic with events (state changes, state reports, a periodic tick, timers and
operation results), runs the commands it returns, and executes the thermostat
operations one at a time on a single worker so that a room never has two
Zigbee operations in flight.
"""

import asyncio
import contextlib
import logging
from collections import Counter
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, assert_never

from homeassistant.const import (
    ATTR_TEMPERATURE,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from custom_components.easy_trv import issues
from custom_components.easy_trv.adapters.zha_gateway import (
    GatewayError,
    UnsupportedAttribute,
)
from custom_components.easy_trv.const import OPERATION_TIMEOUT, TICK_SECONDS
from custom_components.easy_trv.core.model import (
    INTENT_NAMES,
    ClearIssue,
    Command,
    DebounceElapsed,
    Execute,
    Failed,
    FireEvent,
    Log,
    Mismatch,
    Ok,
    OperationResult,
    RaiseIssue,
    RestoreRoom,
    RetryDue,
    RoomMode,
    RoomState,
    RoomStatus,
    ScheduleDebounce,
    ScheduleKnobSettle,
    ScheduleRetry,
    SensorChanged,
    SensorReading,
    SetHvacMode,
    SetRoomMode,
    SetRoomPreset,
    SetRoomSetpoint,
    SetSetpoint,
    SetStatus,
    Signal,
    Start,
    Tick,
    TrvAvailabilityChanged,
    Unsupported,
)
from custom_components.easy_trv.core.room_logic import RoomLogic
from custom_components.easy_trv.core.setpoint import device_driven
from custom_components.easy_trv.core.values import parse_temperature
from custom_components.easy_trv.labels import thermostat_name
from custom_components.easy_trv.observation import (
    HoldInputs,
    KnobTimers,
    ThermostatSubscriptions,
)
from custom_components.easy_trv.profiles.engine import CapabilityUnsupported

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        EventStateReportedData,
        HomeAssistant,
        State,
    )

    from custom_components.easy_trv.adapters.zha_gateway import DeviceRef, TrvGateway
    from custom_components.easy_trv.core.model import Event as LogicEvent
    from custom_components.easy_trv.core.model import (
        Intent,
        Note,
        Outcome,
        Preset,
        RoomConfig,
        RoomParams,
        TrvState,
    )
    from custom_components.easy_trv.core.values import Version
    from custom_components.easy_trv.profiles.engine import Operation, Profile

_LOGGER = logging.getLogger(__name__)

_LOG_LEVELS: Final = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
}
# The units ``values.parse_temperature`` understands.
_SUPPORTED_UNITS: Final = frozenset({"°C", "C", "°F", "F"})
# States that carry no reading, so a missing unit there is not worth a warning.
_NO_READING_STATES: Final = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE, ""})


# HA climate states the room understands as a thermostat mode. Both ``auto`` and
# ``heat_cool`` mean the thermostat's own schedule, which the room leaves alone.
_REPORTED_MODES: Final = {
    "heat": "heat",
    "off": "off",
    "auto": "auto",
    "heat_cool": "auto",
}


def _is_available(state: State | None) -> bool:
    """Return whether a thermostat state exists and is not ``unavailable``."""
    return state is not None and state.state != STATE_UNAVAILABLE


def _fmt_version(version: Version | None) -> str:
    """Render a firmware version as ``"1.3.0"``, or ``"unknown"`` if there is none."""
    return "unknown" if version is None else ".".join(map(str, version))


def _judge(op: Operation, values: Mapping[int, int]) -> Outcome:
    """Classify a read-back: signals first, then accepted values, else a mismatch."""
    actual = values.get(op.attribute)
    if actual is not None:
        name = op.signals.get(actual)
        if name is not None:
            return Signal(name)
    for attribute in op.also_read:
        extra = values.get(attribute)
        if extra is None:
            continue
        name = op.extra_signals.get(attribute, {}).get(extra)
        if name is not None:
            return Signal(name)
    if actual is not None and actual in op.accept:
        return Ok()
    return Mismatch(op.accept, actual)


def _outcome_text(outcome: Outcome) -> str:
    """Describe an outcome in a few words for the debug log."""
    match outcome:
        case Ok():
            return "ok"
        case Mismatch(expected=expected, actual=actual):
            return f"mismatch expected {sorted(expected)} got {actual}"
        case Failed(error=error):
            return f"failed: {error}"
        case Unsupported(capability=capability, required=required, installed=installed):
            return f"unsupported {capability} needs {required}, installed {installed}"
        case Signal(name=name):
            return f"signal {name}"
        case _:
            assert_never(outcome)


def _scalar(value: Any) -> Any:
    """Convert enums and time values; leave everything else as it is."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    return value


def _jsonable(value: Any) -> Any:
    """Convert configuration and state objects into JSON-serialisable data."""
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(item) for item in value), key=str)
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        data = {f.name: _jsonable(getattr(value, f.name)) for f in fields(value)}
        if type(value) in INTENT_NAMES:
            data = {"intent": INTENT_NAMES[type(value)], **data}
        return data
    return _scalar(value)


class RoomController:
    """Drive the thermostats of one room from Home Assistant states.

    A controller is single-use: build it, ``async_start`` it once and
    ``async_stop`` it when the room is unloaded.
    """

    def __init__(  # noqa: PLR0913 - the interface names every collaborator
        self,
        hass: HomeAssistant,
        config: RoomConfig,
        params: RoomParams,
        profile: Profile,
        gateway: TrvGateway,
        device_refs: Mapping[str, DeviceRef],
        *,
        labels: Mapping[str, str] = MappingProxyType({}),
        outdoor_sensor: str | None = None,
    ) -> None:
        """Wire the room logic to Home Assistant, the profile and the gateway.

        ``labels`` maps an intent name to the plain-language name of the setting
        for Repairs texts; a setting without a label is shown by its key.
        ``outdoor_sensor`` is the global outdoor temperature sensor, which only
        matters for a room with an outdoor cut-off.
        """
        self._hass = hass
        self._config = config
        self._params = params
        self._profile = profile
        self._gateway = gateway
        self._device_refs = device_refs
        self._labels = labels
        self._firmware = {trv.entity_id: trv.firmware for trv in config.trvs}
        self._trv_ids = frozenset(self._firmware)
        self._logic = RoomLogic(config, params, self._capability_of)
        self._state = RoomState.initial(config)
        self._status = RoomStatus.OK
        self._attributes: dict[str, Any] = {}
        self._listeners: list[Callable[[], None]] = []
        self._update_listeners: list[Callable[[], None]] = []
        self._queue: asyncio.Queue[tuple[str, Intent]] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        # (thermostat, capability) -> operations queued or running for it
        self._busy: Counter[tuple[str, str]] = Counter()
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._cancel_debounce: CALLBACK_TYPE | None = None
        self._cancel_retries: list[CALLBACK_TYPE] = []
        self._subscriptions = ThermostatSubscriptions(
            gateway,
            profile,
            {trv.entity_id: trv.ieee for trv in config.trvs},
            self._dispatch,
            config.name,
        )
        self._knobs = KnobTimers(hass, self._dispatch)
        self._holds = HoldInputs(
            hass, config, outdoor_sensor, self._dispatch, self._notify_update
        )
        self._warned_units: set[str] = set()
        self._stopped = False

    # --- Public surface --------------------------------------------------

    @property
    def ieees(self) -> frozenset[str]:
        """Return the IEEE addresses of the room's thermostats."""
        return frozenset(trv.ieee for trv in self._config.trvs)

    @property
    def status(self) -> RoomStatus:
        """Return the current room status."""
        return self._status

    @property
    def attributes(self) -> dict[str, Any]:
        """Return the current status attributes (read-only by convention)."""
        return self._attributes

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Call ``cb`` whenever status or attributes change; return a remover."""
        self._listeners.append(cb)

        def remove() -> None:
            if cb in self._listeners:
                self._listeners.remove(cb)

        return remove

    def add_update_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Call ``cb`` after every handled event; return a remover.

        Unlike ``add_listener`` this fires whether or not the status changed, so
        an entity can show the room's setpoint, mode and holds.
        """
        self._update_listeners.append(cb)

        def remove() -> None:
            if cb in self._update_listeners:
                self._update_listeners.remove(cb)

        return remove

    @property
    def setpoint(self) -> float | None:
        """Return the room's target temperature, ``None`` while it has none."""
        return self._state.setpoint

    @property
    def mode(self) -> RoomMode:
        """Return whether the room heats or is switched off."""
        return self._state.mode

    @property
    def preset(self) -> Preset:
        """Return the room's preset."""
        return self._state.preset

    @property
    def frost_return(self) -> float | None:
        """Return the setpoint the room returns to when the frost preset ends."""
        return self._state.frost_return

    @property
    def thermostats(self) -> tuple[str, ...]:
        """Return the climate entities of the room's thermostats."""
        return tuple(self._firmware)

    @property
    def effective_range(self) -> tuple[float, float, float] | None:
        """Return ``(min, max, step)`` of the setpoint, ``None`` without a climate.

        The configuration carries the narrowest range of settings and devices.
        """
        return self._config.setpoint_range

    @property
    def setpoint_source(self) -> str | None:
        """Return where the setpoint came from (``ha``, ``device:<trv>``, ...)."""
        return self._state.setpoint_source

    @property
    def window_open(self) -> bool:
        """Return whether a window hold is active."""
        return self._state.window_open

    @property
    def outdoor_hold(self) -> bool:
        """Return whether the outdoor cut-off hold is active."""
        return self._state.outdoor_hold

    @property
    def notes(self) -> dict[str, Note]:
        """Return why a thermostat's own value is shown but not adopted."""
        return {
            trv: state.note
            for trv, state in self._state.trvs.items()
            if state.note is not None
        }

    @property
    def current_temperature(self) -> float | None:
        """Return the value last sent to the thermostats, else the sensor's."""
        state = self._state
        if state.last_written is not None:
            return state.last_written
        reading = state.readings.get(state.active_sensor or "")
        return None if reading is None else reading.value

    @property
    def in_sync(self) -> bool:
        """Return whether every thermostat is verified at the room's values.

        False while a thermostat is away, on its schedule or window phase, still
        waiting for a setpoint or mode write, or while a knob turn is pending,
        and while a window contact says neither open nor closed.
        """
        wanted = {
            self._capability_of(SetSetpoint(0.0)),
            self._capability_of(SetHvacMode("heat")),
        }
        return self._holds.contacts_reliable and all(
            self._trv_in_sync(entity_id, trv, wanted)
            for entity_id, trv in self._state.trvs.items()
        )

    def _trv_in_sync(self, entity_id: str, trv: TrvState, wanted: set[str]) -> bool:
        """Return whether a thermostat is verified at the room's setpoint and mode.

        ``wanted`` names the setpoint and mode capabilities. Not in sync: it is
        away or device-driven, a write of either is queued, running or awaiting
        its retry, the attempts for either failed, or a knob turn is pending.
        """
        return (
            trv.available
            and not device_driven(trv)
            and not any(self._busy[entity_id, capability] for capability in wanted)
            and not wanted & trv.pending_retry.keys()
            and not wanted & trv.attempts.keys()
            and trv.error_capability not in wanted
            and not trv.knob_pending
            and not trv.resync_pending
        )

    async def async_set_setpoint(self, celsius: float) -> None:
        """Set the room's target temperature and write it to the thermostats."""
        self._dispatch(SetRoomSetpoint(celsius))

    async def async_set_mode(self, mode: RoomMode) -> None:
        """Switch the room on (``heat``) or off."""
        self._dispatch(SetRoomMode(mode))

    async def async_set_preset(self, preset: Preset) -> None:
        """Select the preset of the room."""
        self._dispatch(SetRoomPreset(preset))

    async def async_restore(
        self,
        setpoint: float | None,
        mode: RoomMode | None,
        preset: Preset | None,
        frost_return: float | None = None,
    ) -> None:
        """Take the state remembered across a restart; call it before the start."""
        self._dispatch(RestoreRoom(setpoint, mode, preset, frost_return))

    def snapshot(self) -> dict[str, Any]:
        """Return configuration, tunables and state as JSON-able data."""
        room = self._state
        return {
            "config": _jsonable(self._config),
            "params": _jsonable(self._params),
            "state": _jsonable(room),
            "not_in_profile": {
                trv: sorted(state.not_in_profile)
                for trv, state in self._state.trvs.items()
                if state.not_in_profile
            },
            "hold": {
                "window_open": room.window_open,
                "window_opened_at": (
                    None
                    if room.window_opened_at is None
                    else room.window_opened_at.isoformat()
                ),
                "outdoor_hold": room.outdoor_hold,
            },
            "notes": {trv: note.value for trv, note in self.notes.items()},
            "observations": self._subscriptions.recent(),
        }

    async def async_start(self) -> None:
        """Subscribe to states and time, start the worker and begin the room."""
        config = self._config
        watched = [*config.temp_sensors, *self._firmware]
        track = self._unsubscribers.append
        track(
            async_track_state_change_event(
                self._hass, watched, self._handle_state_change
            )
        )
        if config.temp_sensors:
            # Reports (same state, fresher ``last_reported``) keep a steady
            # sensor from timing out.
            track(
                async_track_state_report_event(
                    self._hass, config.temp_sensors, self._handle_state_report
                )
            )
        track(
            async_track_time_interval(
                self._hass,
                self._handle_tick,
                timedelta(seconds=TICK_SECONDS),
                name=f"easy_trv room {config.name} tick",
                cancel_on_shutdown=True,
            )
        )
        self._worker_task = self._hass.async_create_background_task(
            self._worker(), f"easy_trv room {config.name}"
        )
        # The state starts fresh, so issues from before a reload (a re-paired or
        # updated thermostat, say) are stale. Whatever still holds is raised
        # again by the first operations.
        for trv in config.trvs:
            issues.clear_unreachable(self._hass, trv.ieee)
            issues.clear_firmware(self._hass, trv.ieee)
        issues.clear_window_open_long(self._hass, config.room_id)
        issues.clear_window_contact_suspect(self._hass, config.room_id)
        self._subscriptions.subscribe_all()
        window_open, outdoor = self._holds.start()
        self._dispatch(
            Start(
                sensors=tuple(
                    self._reading(entity_id) for entity_id in config.temp_sensors
                ),
                trv_available={
                    entity_id: self._trv_available(entity_id)
                    for entity_id in self._firmware
                },
                reported={
                    entity_id: self._reported(entity_id) for entity_id in self._firmware
                },
                window_open=window_open,
                outdoor=outdoor,
            )
        )

    async def async_stop(self) -> None:
        """Cancel listeners, timers and the worker; later callbacks are ignored."""
        self._stopped = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._subscriptions.stop()
        self._knobs.stop()
        self._holds.stop()
        if self._cancel_debounce is not None:
            self._cancel_debounce()
            self._cancel_debounce = None
        for cancel in self._cancel_retries:
            cancel()
        self._cancel_retries.clear()
        task, self._worker_task = self._worker_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # --- Home Assistant callbacks ----------------------------------------

    @callback
    def _handle_state_change(self, event: Event[EventStateChangedData]) -> None:
        """Turn a sensor change or a thermostat availability change into an event."""
        if self._stopped:
            return
        entity_id = event.data["entity_id"]
        if entity_id in self._trv_ids:
            available = _is_available(event.data["new_state"])
            if available != _is_available(event.data["old_state"]):
                if available:
                    # Never subscribed, or ZHA reloaded and dropped our listeners.
                    self._subscriptions.subscribe(entity_id)
                self._dispatch(TrvAvailabilityChanged(entity_id, available))
        elif entity_id in self._config.temp_sensors:
            self._dispatch(SensorChanged(self._reading(entity_id)))

    @callback
    def _handle_state_report(self, event: Event[EventStateReportedData]) -> None:
        """Refresh a sensor whose state was reported again without a change."""
        if self._stopped:
            return
        entity_id = event.data["entity_id"]
        self._dispatch(SensorChanged(self._reading(entity_id)))

    @callback
    def _handle_tick(self, _now: datetime) -> None:
        """Let the logic check timeouts and the periodic refresh."""
        self._dispatch(Tick())

    # --- Reading Home Assistant ------------------------------------------

    def _reading(self, entity_id: str) -> SensorReading:
        """Read a temperature sensor into °C, warning once about odd units."""
        state = self._hass.states.get(entity_id)
        if state is None:
            return SensorReading(entity_id, None, None)
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if (
            state.state not in _NO_READING_STATES
            and unit not in _SUPPORTED_UNITS
            and entity_id not in self._warned_units
        ):
            self._warned_units.add(entity_id)
            _LOGGER.warning(
                "%s: sensor %s reports unsupported unit %r, ignoring its readings",
                self._config.name,
                entity_id,
                unit,
            )
        return SensorReading(
            entity_id, parse_temperature(state.state, unit), state.last_reported
        )

    def _reported(self, entity_id: str) -> tuple[float | None, str | None]:
        """Read what a thermostat's climate entity shows: setpoint and mode."""
        state = self._hass.states.get(entity_id)
        if state is None:
            return None, None
        target = state.attributes.get(ATTR_TEMPERATURE)
        setpoint = (
            None
            if target is None
            else parse_temperature(
                str(target), self._hass.config.units.temperature_unit
            )
        )
        return setpoint, _REPORTED_MODES.get(state.state)

    def _trv_available(self, entity_id: str) -> bool:
        """Return whether the climate entity exists and is not ``unavailable``."""
        return _is_available(self._hass.states.get(entity_id))

    # --- Logic dispatch --------------------------------------------------

    def _capability_of(self, intent: Intent) -> str:
        """Name the capability handling an intent (the intent if there is none)."""
        return self._profile.capability_for(intent) or INTENT_NAMES[type(intent)]

    def _dispatch(self, event: LogicEvent) -> None:
        """Run the logic on an event and carry out the commands it returns."""
        if self._stopped:
            return
        self._state, commands = self._logic.handle(event, dt_util.utcnow(), self._state)
        for command in commands:
            self._execute(command)
        self._notify_update()

    def _notify_update(self) -> None:
        """Tell the update listeners the room may look different now."""
        for listener in list(self._update_listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("%s: update listener failed", self._config.name)

    def _execute(self, command: Command) -> None:
        """Carry out one command of the logic."""
        match command:
            case Execute(trv=trv, intent=intent):
                self._busy[trv, self._capability_of(intent)] += 1
                self._queue.put_nowait((trv, intent))
            case ScheduleDebounce(seconds=seconds):
                self._schedule_debounce(seconds)
            case ScheduleRetry(trv=trv, capability=capability, seconds=seconds):
                self._schedule_retry(trv, capability, seconds)
            case ScheduleKnobSettle(trv=trv, capability=capability, seconds=seconds):
                self._knobs.arm(trv, capability, seconds)
            case SetStatus(status=status, attributes=attributes):
                self._set_status(status, attributes)
            case RaiseIssue():
                self._raise_issue(command)
            case ClearIssue():
                self._clear_issue(command)
            case FireEvent(name=name, data=data):
                self._hass.bus.async_fire(name, dict(data))
            case Log(level=level, message=message):
                # The core's messages already carry the room name.
                _LOGGER.log(_LOG_LEVELS[level], "%s", message)
            case _:
                assert_never(command)

    def _schedule_debounce(self, seconds: float) -> None:
        """(Re-)arm the single debounce timer."""
        if self._cancel_debounce is not None:
            self._cancel_debounce()

        @callback
        def fire(_now: datetime) -> None:
            self._cancel_debounce = None
            self._dispatch(DebounceElapsed())

        self._cancel_debounce = async_call_later(self._hass, seconds, fire)

    def _schedule_retry(self, trv: str, capability: str, seconds: float) -> None:
        """Arm a retry timer; the logic decides which retries are still wanted."""

        @callback
        def fire(_now: datetime) -> None:
            if cancel in self._cancel_retries:
                self._cancel_retries.remove(cancel)
            self._dispatch(RetryDue(trv, capability))

        cancel = async_call_later(self._hass, seconds, fire)
        self._cancel_retries.append(cancel)

    def _set_status(self, status: RoomStatus, attributes: Mapping[str, Any]) -> None:
        """Store the published status and tell listeners if it changed."""
        new_attributes = dict(attributes)
        changed = status != self._status or new_attributes != self._attributes
        self._status = status
        self._attributes = new_attributes
        if changed:
            for listener in list(self._listeners):
                listener()

    def _raise_issue(self, command: RaiseIssue) -> None:
        """Create the repairs issue of a thermostat or of the room."""
        found = command.placeholders
        match command.kind:
            case "window_open_long":
                issues.raise_window_open_long(
                    self._hass, command.trv, self._config.name, found["minutes"]
                )
            case "window_contact_suspect":
                issues.raise_window_contact_suspect(
                    self._hass,
                    command.trv,
                    self._config.name,
                    found["minutes"],
                    found["drop"],
                )
            case "unreachable":
                ref = self._device_refs[command.trv]
                thermostat = thermostat_name(self._hass, ref.entity_id)
                issues.raise_unreachable(
                    self._hass,
                    ref.ieee,
                    room=self._config.name,
                    setting=self._setting_name(found["attribute"]),
                    thermostat=thermostat,
                    attempts=found["attempts"],
                    error=found["error"],
                )
            case "firmware":
                ref = self._device_refs[command.trv]
                thermostat = thermostat_name(self._hass, ref.entity_id)
                issues.raise_firmware(
                    self._hass,
                    ref.ieee,
                    room=self._config.name,
                    setting=self._setting_name(found["capability"]),
                    thermostat=thermostat,
                    required=found["required"],
                    current=found["installed"],
                )
            case _:
                assert_never(command.kind)

    def _setting_name(self, capability: str) -> str:
        """Return the plain-language name of a capability (its key if unlabelled).

        The labels are keyed by intent. A profile names its capabilities freely,
        so the capability is mapped to its intent first; where the profile has no
        such capability the core already reports the intent name.
        """
        spec = self._profile.capabilities.get(capability)
        intent = capability if spec is None else spec.intent
        return self._labels.get(intent, capability)

    def _clear_issue(self, command: ClearIssue) -> None:
        """Delete the repairs issue of a thermostat or of the room."""
        match command.kind:
            case "window_open_long":
                issues.clear_window_open_long(self._hass, command.trv)
            case "window_contact_suspect":
                issues.clear_window_contact_suspect(self._hass, command.trv)
            case "unreachable":
                issues.clear_unreachable(
                    self._hass, self._device_refs[command.trv].ieee
                )
            case "firmware":
                issues.clear_firmware(self._hass, self._device_refs[command.trv].ieee)
            case _:
                assert_never(command.kind)

    # --- Worker ----------------------------------------------------------

    async def _worker(self) -> None:
        """Run queued operations strictly one at a time and report each result."""
        while True:
            trv, intent = await self._queue.get()
            try:
                await self._process(trv, intent)
            except Exception:
                # Reached only if the logic itself fails on the result; the
                # worker must survive that as well.
                _LOGGER.exception(
                    "%s: unexpected error while handling the result of %s on %s",
                    self._config.name,
                    INTENT_NAMES[type(intent)],
                    trv,
                )
            finally:
                self._queue.task_done()

    async def _process(self, trv: str, intent: Intent) -> None:
        """Run one operation and feed its result, whatever it was, to the logic."""
        try:
            result = await self._run_operation(trv, intent)
        except Exception as exc:
            # Anything not mapped to an outcome yet: log it and report a
            # failure so the logic's retry and issue machinery still runs.
            _LOGGER.exception(
                "%s: unexpected error while running %s on %s",
                self._config.name,
                INTENT_NAMES[type(intent)],
                trv,
            )
            failed = Failed(f"unexpected: {type(exc).__name__}: {exc}")
            result = OperationResult(trv, intent, self._capability_of(intent), failed)
        # Before the result: its listeners see the operation as finished.
        self._busy[trv, self._capability_of(intent)] -= 1
        self._dispatch(result)

    async def _run_operation(self, trv: str, intent: Intent) -> OperationResult:
        """Plan, write and read back one intent; every failure is an outcome."""
        try:
            op = self._profile.plan(intent, self._firmware[trv])
        except CapabilityUnsupported as exc:
            capability = self._profile.capability_for(intent) or exc.capability
            outcome: Outcome = Unsupported(
                exc.capability,
                _fmt_version(exc.required),
                _fmt_version(exc.installed),
                # No required version: the profile declares no such capability.
                reason="firmware" if exc.required is not None else "profile",
            )
            if exc.required is not None:
                # The core logs a missing profile capability itself, once.
                self._log_not_sent(trv, capability, outcome)
            return OperationResult(trv, intent, capability, outcome)
        except ValueError as exc:
            capability = self._capability_of(intent)
            outcome = Failed(str(exc))
            self._log_not_sent(trv, capability, outcome)
            return OperationResult(trv, intent, capability, outcome)
        outcome = self._with_observed(
            op.capability, await self._write_and_verify(trv, op)
        )
        _LOGGER.debug(
            "%s: %s %s attr 0x%04X raw=%s -> %s",
            self._config.name,
            trv,
            op.capability,
            op.attribute,
            op.raw_value,
            _outcome_text(outcome),
        )
        return OperationResult(trv, intent, op.capability, outcome)

    def _with_observed(self, capability: str, outcome: Outcome) -> Outcome:
        """Decode the value a mismatch read back, for the capabilities observed."""
        if (
            isinstance(outcome, Mismatch)
            and outcome.actual is not None
            and capability in self._profile.observed()
        ):
            return replace(
                outcome, observed=self._profile.decode(capability, outcome.actual)
            )
        return outcome

    def _log_not_sent(self, trv: str, capability: str, outcome: Outcome) -> None:
        """Log an intent that could not be turned into an operation."""
        _LOGGER.debug(
            "%s: %s %s not sent -> %s",
            self._config.name,
            trv,
            capability,
            _outcome_text(outcome),
        )

    async def _write_and_verify(self, trv: str, op: Operation) -> Outcome:
        """Write the value, wait, read it back and judge the read-back."""
        ieee = self._device_refs[trv].ieee
        try:
            if not self._gateway.is_available(ieee):
                return Failed("device unavailable")
            async with asyncio.timeout(OPERATION_TIMEOUT):
                await self._gateway.write(
                    ieee, op.endpoint, op.cluster, op.attribute, op.raw_value
                )
            await asyncio.sleep(self._params.verify_delay)
            async with asyncio.timeout(OPERATION_TIMEOUT):
                values = await self._gateway.read(
                    ieee, op.endpoint, op.cluster, [op.attribute, *op.also_read]
                )
        except UnsupportedAttribute:
            # The device lacks the attribute: no retry will help. What the
            # firmware needs is not known, only what is installed.
            installed = _fmt_version(self._firmware[trv])
            return Unsupported(op.capability, "unknown", installed, reason="device")
        except (GatewayError, TimeoutError) as err:
            return Failed(str(err) or type(err).__name__)
        return _judge(op, values)
