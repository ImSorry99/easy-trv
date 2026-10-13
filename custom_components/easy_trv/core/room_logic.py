"""Room logic: sensor selection, source switching and temperature writes.

``RoomLogic.handle`` is a pure state machine. It takes an event, the current
time and the current state and returns the next state together with the
commands the controller must carry out. It performs no I/O and has no Home
Assistant imports, so it can be tested without a running instance.
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from custom_components.easy_trv.core.model import (
    Command,
    DebounceElapsed,
    Event,
    Execute,
    GlobalParams,
    Intent,
    RoomConfig,
    RoomState,
    RoomStatus,
    ScheduleDebounce,
    SensorChanged,
    SensorReading,
    SetHysteresis,
    SetRoomTemperature,
    SetSource,
    SetStatus,
    SetWindowDetection,
    Source,
    Start,
    Tick,
    TrvHealth,
    TrvState,
)
from custom_components.easy_trv.core.values import is_in_range, round_tenth

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

_MIN_TEMPERATURE = 0.0
_MAX_TEMPERATURE = 99.9
# Absorbs binary float noise so a change of exactly ``min_delta`` always counts.
_DELTA_TOLERANCE = 1e-9

type _Step = tuple[RoomState, list[Command]]


@dataclass(frozen=True)
class _Active:
    """The usable sensor with the highest priority and its raw value."""

    entity_id: str
    value: float


class RoomLogic:
    """Decide which sensor feeds the thermostats and when to write its value."""

    def __init__(
        self,
        cfg: RoomConfig,
        params: GlobalParams,
        capability_of: Callable[[Intent], str],
    ) -> None:
        """Store the room configuration, tunables and the intent-to-capability map."""
        self._cfg = cfg
        self._params = params
        self._capability_of = capability_of

    def usable(self, reading: SensorReading, now: datetime) -> bool:
        """Return whether a reading is in range and, if required, fresh."""
        value = reading.value
        if value is None or not is_in_range(value, _MIN_TEMPERATURE, _MAX_TEMPERATURE):
            return False
        timeout = self._params.sensor_timeout
        if timeout is None:
            return True
        return (
            reading.last_reported is not None and now - reading.last_reported <= timeout
        )

    def active_sensor(self, state: RoomState, now: datetime) -> SensorReading | None:
        """Return the first usable reading in configured priority order."""
        for entity_id in self._cfg.temp_sensors:
            reading = state.readings.get(entity_id)
            if reading is not None and self.usable(reading, now):
                return reading
        return None

    def handle(
        self, event: Event, now: datetime, state: RoomState
    ) -> tuple[RoomState, list[Command]]:
        """Apply an event and return the new state and the commands to run.

        The last command is always exactly one ``SetStatus``.
        """
        match event:
            case Start():
                state, commands = self._on_start(event, now, state)
            case SensorChanged():
                state, commands = self._on_sensor_changed(event, now, state)
            case DebounceElapsed():
                state, commands = self._on_debounce_elapsed(now, state)
            case Tick():
                state, commands = self._on_tick(now, state)
            case _:
                msg = "handled in Task 6"
                raise NotImplementedError(msg)
        return state, [*commands, self._status(state, now)]

    # --- Event handlers --------------------------------------------------

    def _on_start(self, event: Start, now: datetime, state: RoomState) -> _Step:
        """Seed readings and availability, apply settings, pick the source."""
        trvs = {}
        for trv in self._cfg.trvs:
            available = event.trv_available.get(trv.entity_id, True)
            trvs[trv.entity_id] = replace(
                state.trvs.get(trv.entity_id, TrvState()),
                available=available,
                health=TrvHealth.OK if available else TrvHealth.UNAVAILABLE,
            )
        readings = {**state.readings, **{r.entity_id: r for r in event.sensors}}
        state = replace(state, readings=readings, trvs=trvs)
        state, commands = self._apply_settings(state)
        state, source_commands = self._reconcile_source(state, now)
        return state, [*commands, *source_commands]

    def _on_sensor_changed(
        self, event: SensorChanged, now: datetime, state: RoomState
    ) -> _Step:
        """Store the reading, then switch source or arm the debounce."""
        reading = event.reading
        state = replace(state, readings={**state.readings, reading.entity_id: reading})
        before = state.source
        state, commands = self._reconcile_source(state, now)
        if state.source is not before:
            return state, commands
        state, debounce = self._debounce_if_changed(state, now)
        return state, [*commands, *debounce]

    def _on_debounce_elapsed(self, now: datetime, state: RoomState) -> _Step:
        """Write the current value once the readings have settled."""
        state = replace(state, debounce_pending=False)
        active = self._active(state, now)
        if active is None:
            return state, []
        return self._write_value(state, active)

    def _on_tick(self, now: datetime, state: RoomState) -> _Step:
        """Handle sensor timeouts, then refresh the value if it is due."""
        before_source = state.source
        before_sensor = state.active_sensor
        state, commands = self._reconcile_source(state, now)
        if state.source is not before_source:
            return state, commands
        state, refresh = self._refresh(state, now)
        commands = [*commands, *refresh]
        if not refresh and state.active_sensor != before_sensor:
            # A lower-priority sensor took over; its value may differ.
            state, debounce = self._debounce_if_changed(state, now)
            commands = [*commands, *debounce]
        return state, commands

    # --- Paths -----------------------------------------------------------

    def _apply_settings(self, state: RoomState) -> _Step:
        """Emit the configured window-detection and hysteresis settings once."""
        if state.settings_applied:
            return state, []
        commands: list[Command] = []
        for trv in self._writable(state):
            if self._cfg.window_detection != "leave":
                enabled = self._cfg.window_detection == "on"
                commands.append(Execute(trv, SetWindowDetection(enabled)))
            if self._cfg.hysteresis is not None:
                commands.append(Execute(trv, SetHysteresis(self._cfg.hysteresis)))
        return replace(state, settings_applied=True), commands

    def _reconcile_source(self, state: RoomState, now: datetime) -> _Step:
        """Switch the thermostats between the external and internal source.

        Rooms without temperature sensors never touch the source. The active
        sensor is tracked in the state even when no source switch is needed.
        """
        if not self._cfg.temp_sensors:
            return state, []
        active = self._active(state, now)
        if active is None:
            if state.source is Source.INTERNAL:
                return replace(state, active_sensor=None), []
            return self._use_internal(state)
        if state.source is not Source.EXTERNAL:
            return self._use_external(state, active)
        return replace(state, active_sensor=active.entity_id), []

    def _use_internal(self, state: RoomState) -> _Step:
        """Tell every writable thermostat to regulate on its own sensor."""
        commands: list[Command] = [
            Execute(trv, SetSource("internal")) for trv in self._writable(state)
        ]
        return replace(state, source=Source.INTERNAL, active_sensor=None), commands

    def _use_external(self, state: RoomState, active: _Active) -> _Step:
        """Switch to the external source and send the current value at once."""
        commands: list[Command] = [
            Execute(trv, SetSource("external")) for trv in self._writable(state)
        ]
        state = replace(state, source=Source.EXTERNAL, active_sensor=active.entity_id)
        state, value_commands = self._write_value(state, active)
        return state, [*commands, *value_commands]

    def _write_value(self, state: RoomState, active: _Active) -> _Step:
        """Send the rounded active value to every writable thermostat."""
        celsius = round_tenth(active.value)
        commands: list[Command] = [
            Execute(trv, SetRoomTemperature(celsius)) for trv in self._writable(state)
        ]
        return replace(state, last_written=celsius), commands

    def _debounce_if_changed(self, state: RoomState, now: datetime) -> _Step:
        """Arm (or re-arm) the debounce when the value moved by ``min_delta``."""
        active = self._active(state, now)
        if active is None or not self._differs(state, active.value):
            return state, []
        commands: list[Command] = [ScheduleDebounce(self._params.debounce)]
        return replace(state, debounce_pending=True), commands

    def _refresh(self, state: RoomState, now: datetime) -> _Step:
        """Resend the value when the last verified write is older than ``refresh``."""
        last = state.last_value_write
        if (
            state.source is not Source.EXTERNAL
            or last is None
            or now - last < self._params.refresh
        ):
            return state, []
        active = self._active(state, now)
        if active is None:
            return state, []
        return self._write_value(state, active)

    # --- Helpers ---------------------------------------------------------

    def _active(self, state: RoomState, now: datetime) -> _Active | None:
        """Return the active sensor with a concrete value, if any."""
        reading = self.active_sensor(state, now)
        if reading is None or reading.value is None:
            return None
        return _Active(reading.entity_id, reading.value)

    def _differs(self, state: RoomState, value: float) -> bool:
        """Return whether ``value`` is at least ``min_delta`` from the last write."""
        last = state.last_written
        if last is None:
            return True
        return abs(value - last) + _DELTA_TOLERANCE >= self._params.min_delta

    def _writable(self, state: RoomState) -> list[str]:
        """Return the thermostats that can currently receive commands."""
        writable: list[str] = []
        for trv in self._cfg.trvs:
            trv_state = state.trvs[trv.entity_id]
            if trv_state.available and trv_state.health is not TrvHealth.UNSUPPORTED:
                writable.append(trv.entity_id)
        return writable

    def _status(self, state: RoomState, now: datetime) -> SetStatus:
        """Derive the room status and its attributes from the state."""
        healths = [trv.health for trv in state.trvs.values()]
        failsafe_recent = any(
            trv.failsafe_seen_at is not None
            and now - trv.failsafe_seen_at <= self._params.refresh
            for trv in state.trvs.values()
        )
        if TrvHealth.ERROR in healths:
            status = RoomStatus.ERROR
        elif (
            (bool(self._cfg.temp_sensors) and state.source is not Source.EXTERNAL)
            or TrvHealth.UNAVAILABLE in healths
            or TrvHealth.UNSUPPORTED in healths
            or failsafe_recent
        ):
            status = RoomStatus.DEGRADED
        else:
            status = RoomStatus.OK
        last_error = next(
            (
                trv.last_error
                for trv in state.trvs.values()
                if trv.last_error is not None
            ),
            None,
        )
        return SetStatus(
            status,
            {
                "active_sensor": state.active_sensor,
                "source": state.source.value,
                "trv_health": {
                    entity_id: trv.health.value for entity_id, trv in state.trvs.items()
                },
                "last_error": last_error,
            },
        )
