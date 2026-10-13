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
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Final, assert_never

from homeassistant.const import (
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
from custom_components.easy_trv.adapters.zha_gateway import GatewayError
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
    RetryDue,
    RoomState,
    RoomStatus,
    ScheduleDebounce,
    ScheduleRetry,
    SensorChanged,
    SensorReading,
    SetStatus,
    Signal,
    Start,
    Tick,
    TrvAvailabilityChanged,
    Unsupported,
)
from custom_components.easy_trv.core.room_logic import RoomLogic
from custom_components.easy_trv.core.values import parse_temperature
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
        GlobalParams,
        Intent,
        Outcome,
        RoomConfig,
    )
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


def _is_available(state: State | None) -> bool:
    """Return whether a thermostat state exists and is not ``unavailable``."""
    return state is not None and state.state != STATE_UNAVAILABLE


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
        params: GlobalParams,
        profile: Profile,
        gateway: TrvGateway,
        device_refs: Mapping[str, DeviceRef],
    ) -> None:
        """Wire the room logic to Home Assistant, the profile and the gateway."""
        self._hass = hass
        self._config = config
        self._params = params
        self._profile = profile
        self._gateway = gateway
        self._device_refs = device_refs
        self._firmware = {trv.entity_id: trv.firmware for trv in config.trvs}
        self._trv_ids = frozenset(self._firmware)
        self._logic = RoomLogic(config, params, self._capability_of)
        self._state = RoomState.initial(config)
        self._status = RoomStatus.OK
        self._attributes: dict[str, Any] = {}
        self._listeners: list[Callable[[], None]] = []
        self._queue: asyncio.Queue[tuple[str, Intent]] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._cancel_debounce: CALLBACK_TYPE | None = None
        self._cancel_retries: list[CALLBACK_TYPE] = []
        self._warned_units: set[str] = set()
        self._stopped = False

    # --- Public surface --------------------------------------------------

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

    def snapshot(self) -> dict[str, Any]:
        """Return configuration, tunables and state as JSON-able data."""
        return {
            "config": _jsonable(self._config),
            "params": _jsonable(self._params),
            "state": _jsonable(self._state),
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
        self._dispatch(
            Start(
                sensors=tuple(
                    self._reading(entity_id) for entity_id in config.temp_sensors
                ),
                trv_available={
                    entity_id: self._trv_available(entity_id)
                    for entity_id in self._firmware
                },
            )
        )

    async def async_stop(self) -> None:
        """Cancel listeners, timers and the worker; later callbacks are ignored."""
        self._stopped = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
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

    def _execute(self, command: Command) -> None:
        """Carry out one command of the logic."""
        match command:
            case Execute(trv=trv, intent=intent):
                self._queue.put_nowait((trv, intent))
            case ScheduleDebounce(seconds=seconds):
                self._schedule_debounce(seconds)
            case ScheduleRetry(trv=trv, capability=capability, seconds=seconds):
                self._schedule_retry(trv, capability, seconds)
            case SetStatus(status=status, attributes=attributes):
                self._set_status(status, attributes)
            case RaiseIssue():
                self._raise_issue(command)
            case ClearIssue():
                self._clear_issue(command)
            case FireEvent(name=name, data=data):
                self._hass.bus.async_fire(name, dict(data))
            case Log(level=level, message=message):
                _LOGGER.log(_LOG_LEVELS[level], "%s: %s", self._config.name, message)
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
        """Create the repairs issue of a thermostat."""
        ref = self._device_refs[command.trv]
        placeholders = {"trv_name": ref.entity_id, **command.placeholders}
        match command.kind:
            case "unreachable":
                issues.raise_unreachable(self._hass, ref.ieee, placeholders)
            case "firmware":
                issues.raise_firmware(self._hass, ref.ieee, placeholders)
            case _:
                assert_never(command.kind)

    def _clear_issue(self, command: ClearIssue) -> None:
        """Delete the repairs issue of a thermostat."""
        ieee = self._device_refs[command.trv].ieee
        match command.kind:
            case "unreachable":
                issues.clear_unreachable(self._hass, ieee)
            case "firmware":
                issues.clear_firmware(self._hass, ieee)
            case _:
                assert_never(command.kind)

    # --- Worker ----------------------------------------------------------

    async def _worker(self) -> None:
        """Run queued operations strictly one at a time and report each result."""
        while True:
            trv, intent = await self._queue.get()
            try:
                self._dispatch(await self._run_operation(trv, intent))
            except Exception:
                _LOGGER.exception(
                    "%s: unexpected error while running %s on %s",
                    self._config.name,
                    INTENT_NAMES[type(intent)],
                    trv,
                )
            finally:
                self._queue.task_done()

    async def _run_operation(self, trv: str, intent: Intent) -> OperationResult:
        """Plan, write and read back one intent; every failure is an outcome."""
        try:
            op = self._profile.plan(intent, self._firmware[trv])
        except CapabilityUnsupported as exc:
            capability = self._profile.capability_for(intent) or exc.capability
            outcome = Unsupported(exc.capability, str(exc.required), str(exc.installed))
            return OperationResult(trv, intent, capability, outcome)
        except ValueError as exc:
            capability = self._capability_of(intent)
            return OperationResult(trv, intent, capability, Failed(str(exc)))
        return OperationResult(
            trv, intent, op.capability, await self._write_and_verify(trv, op)
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
        except (GatewayError, TimeoutError) as err:
            return Failed(str(err) or type(err).__name__)
        return _judge(op, values)
