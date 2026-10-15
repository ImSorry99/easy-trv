"""Room logic: sensor selection, source switching, writes and their outcomes.

``RoomLogic.handle`` is a pure state machine. It takes an event, the current
time and the current state and returns the next state together with the
commands the controller must carry out. It performs no I/O and has no Home
Assistant imports, so it can be tested without a running instance.
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, assert_never

from custom_components.easy_trv.core import hold, observe, setpoint
from custom_components.easy_trv.core.model import (
    EVENT_FAILSAFE,
    EVENT_RECOVERED,
    EVENT_WRITE_FAILED,
    SETTING_INTENTS,
    ClearIssue,
    Command,
    DebounceElapsed,
    Event,
    Execute,
    Failed,
    FireEvent,
    Intent,
    KnobSettled,
    Log,
    Mismatch,
    Ok,
    OperationResult,
    OutdoorChanged,
    RaiseIssue,
    RestoreRoom,
    RetryDue,
    RoomConfig,
    RoomMode,
    RoomParams,
    RoomState,
    RoomStatus,
    ScheduleDebounce,
    ScheduleRetry,
    SensorChanged,
    SensorReading,
    SetAdaptiveMode,
    SetFrostTemperature,
    SetHvacMode,
    SetHysteresis,
    SetRoomMode,
    SetRoomPreset,
    SetRoomSetpoint,
    SetRoomTemperature,
    SetSetpoint,
    SetSource,
    SetStatus,
    SetWindowDetection,
    Signal,
    Source,
    Start,
    Tick,
    TrvAvailabilityChanged,
    TrvHealth,
    TrvObserved,
    TrvState,
    Unsupported,
    WindowChanged,
)
from custom_components.easy_trv.core.values import is_in_range, round_tenth

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

_MIN_TEMPERATURE = 0.0
_MAX_TEMPERATURE = 99.9
# Absorbs binary float noise so a change of exactly ``min_delta`` always counts.
_DELTA_TOLERANCE = 1e-9

type _Step = tuple[RoomState, list[Command]]


def _without[V](mapping: Mapping[str, V], key: str) -> dict[str, V]:
    """Return a copy of ``mapping`` that no longer contains ``key``."""
    return {k: v for k, v in mapping.items() if k != key}


def _value_changed(previous: SensorReading | None, reading: SensorReading) -> bool:
    """Return whether a reading carries a new value for its sensor.

    A first reading and a reading that follows one without a value count as
    new; so does any different value.
    """
    return previous is None or previous.value is None or previous.value != reading.value


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
        params: RoomParams,
        capability_of: Callable[[Intent], str],
    ) -> None:
        """Store the room configuration, tunables and the intent-to-capability map."""
        self._cfg = cfg
        self._params = params
        self._capability_of = capability_of
        self._ieee = {trv.entity_id: trv.ieee for trv in cfg.trvs}

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
            case TrvAvailabilityChanged():
                state, commands = self._on_availability(event, now, state)
            case RetryDue():
                state, commands = self._on_retry_due(event, now, state)
            case OperationResult():
                state, commands = self._on_result(event, now, state)
            case SetRoomSetpoint() | SetRoomMode() | SetRoomPreset() | RestoreRoom():
                state, commands = self._on_room_event(event, now, state)
            case TrvObserved():
                state, commands = observe.on_observed(
                    self._cfg, state, now, event, self._capability_of
                )
            case KnobSettled():
                state, commands = self._on_knob_settled(event, now, state)
            case WindowChanged() | OutdoorChanged():
                state, commands = self._on_hold_event(event, now, state)
            case _:
                assert_never(event)
        commands = self._drop_unsupported(state, commands)
        state = self._supersede_pending(state, commands)
        return state, [*commands, self._status(state, now)]

    # --- Event handlers --------------------------------------------------

    def _on_start(self, event: Start, now: datetime, state: RoomState) -> _Step:
        """Seed readings and availability, apply settings, pick the source."""
        trvs = {}
        for trv in self._cfg.trvs:
            known = state.trvs.get(trv.entity_id, TrvState())
            known = replace(
                known, available=event.trv_available.get(trv.entity_id, True)
            )
            trvs[trv.entity_id] = replace(known, health=self._baseline(known))
        readings = {**state.readings, **{r.entity_id: r for r in event.sensors}}
        state = replace(state, readings=readings, trvs=trvs)
        state, commands = self._apply_settings(state, self._writable(state))
        # The holds first: a room that starts held is compared with the
        # thermostats as held, so it writes off and neither heat nor a value.
        state, holds = self._start_holds(event, now, state)
        commands = [*commands, *holds]
        # Before the source: a room that starts switched off writes no value.
        state, setpoint_commands = setpoint.reconcile_start(
            self._cfg, state, now, event.reported, self._capability_of
        )
        state, source_commands = self._reconcile_source(state, now)
        return state, [*commands, *setpoint_commands, *source_commands]

    def _start_holds(self, event: Start, now: datetime, state: RoomState) -> _Step:
        """Take the window contact and outdoor temperature known at start.

        Only the state: the reconcile that follows writes the mode each
        thermostat should show, once.
        """
        state, commands = hold.on_window(
            self._cfg,
            state,
            now,
            event.window_open,
            self._active_pair(state, now),
            self._capability_of,
            self._source_intents,
            at_start=True,
        )
        state, outdoor = hold.on_outdoor(
            self._cfg,
            state,
            now,
            event.outdoor,
            self._capability_of,
            self._source_intents,
            at_start=True,
        )
        return state, [*commands, *outdoor]

    def _on_room_event(
        self,
        event: SetRoomSetpoint | SetRoomMode | SetRoomPreset | RestoreRoom,
        now: datetime,
        state: RoomState,
    ) -> _Step:
        """Route the events that set what the room owns to ``setpoint``."""
        cfg, capability_of = self._cfg, self._capability_of
        match event:
            case SetRoomSetpoint():
                return setpoint.on_set_setpoint(
                    cfg, state, now, event.celsius, capability_of
                )
            case SetRoomMode():
                return self._on_set_mode(event, now, state)
            case SetRoomPreset():
                return setpoint.on_set_preset(
                    cfg, state, now, event.preset, capability_of
                )
            case RestoreRoom():
                resync: list[Intent] = []
                if setpoint.leaving_off(cfg, state, event.mode):
                    state, resync = self._source_intents(state, now)
                return setpoint.on_restore(
                    cfg, state, now, event, capability_of, resync
                )
            case _:
                assert_never(event)

    def _on_set_mode(
        self, event: SetRoomMode, now: datetime, state: RoomState
    ) -> _Step:
        """Switch the room on or off; switching on resyncs source and value first."""
        resync: list[Intent] = []
        if (
            event.mode is RoomMode.HEAT
            and self._cfg.setpoint_range is not None
            and not setpoint.holding(state)
        ):
            state, resync = self._source_intents(state, now)
        return setpoint.on_set_mode(
            self._cfg, state, now, event.mode, self._capability_of, resync
        )

    def _on_knob_settled(
        self, event: KnobSettled, now: datetime, state: RoomState
    ) -> _Step:
        """Adopt a settled knob turn; one that wakes the room resyncs source first."""
        resync: list[Intent] = []
        if observe.knob_wakes_room(self._cfg, state, event, self._capability_of):
            state, resync = self._source_intents(state, now)
        return observe.on_knob_settled(
            self._cfg, state, now, event, self._capability_of, resync
        )

    def _on_sensor_changed(
        self, event: SensorChanged, now: datetime, state: RoomState
    ) -> _Step:
        """Store the reading, then switch source or arm the debounce.

        A report that only carries a newer ``last_reported`` refreshes the
        stored reading and nothing else, so a steady stream of reports cannot
        keep pushing the debounce back. The same goes for a sensor that is not
        the active one: its value is not what gets written, so however chatty
        it is, it must not re-arm the debounce of the active sensor.
        """
        reading = event.reading
        previous = state.readings.get(reading.entity_id)
        before_sensor = state.active_sensor
        state = replace(state, readings={**state.readings, reading.entity_id: reading})
        before = state.source
        state, commands = self._reconcile_source(state, now)
        if state.source is not before:
            return state, commands
        # A sensor taking over without a source switch (for example the
        # priority sensor reporting again) has a value that may differ.
        handed_over = state.active_sensor != before_sensor
        is_active = reading.entity_id == state.active_sensor
        if not (handed_over or (is_active and _value_changed(previous, reading))):
            return state, commands
        state, debounce = self._debounce_if_changed(state, now)
        return state, [*commands, *debounce]

    def _on_debounce_elapsed(self, now: datetime, state: RoomState) -> _Step:
        """Write the current value once the readings have settled."""
        if not state.debounce_pending:
            return state, []
        state = replace(state, debounce_pending=False)
        active = self._active(state, now)
        if active is None:
            return state, []
        return self._write_value(state, active, now)

    def _on_hold_event(
        self, event: WindowChanged | OutdoorChanged, now: datetime, state: RoomState
    ) -> _Step:
        """Route a window or outdoor change to ``hold``."""
        cfg, capability_of = self._cfg, self._capability_of
        if isinstance(event, WindowChanged):
            return hold.on_window(
                cfg,
                state,
                now,
                event.open,
                self._active_pair(state, now),
                capability_of,
                self._source_intents,
            )
        return hold.on_outdoor(
            cfg, state, now, event.celsius, capability_of, self._source_intents
        )

    def _on_tick(self, now: datetime, state: RoomState) -> _Step:
        """Handle sensor timeouts, window phases and holds, then resend what is due."""
        state, phase = observe.expire_window_phase(
            self._cfg, state, now, self._capability_of
        )
        state, holds = hold.on_tick(
            self._cfg, state, now, self._active_pair(state, now)
        )
        state, commands = self._on_tick_sensors(now, state)
        return state, [*phase, *holds, *commands]

    def _on_tick_sensors(self, now: datetime, state: RoomState) -> _Step:
        """Handle sensor timeouts, then resend what is due: failed writes, the value."""
        before_source = state.source
        before_sensor = state.active_sensor
        state, commands = self._reconcile_source(state, now)
        if state.source is not before_source:
            return state, commands
        state, retries = self._retry_failed(state, now)
        state, refresh = self._refresh(state, now)
        commands = [*commands, *retries, *refresh]
        if not refresh and state.active_sensor != before_sensor:
            # A lower-priority sensor took over; its value may differ.
            state, debounce = self._debounce_if_changed(state, now)
            commands = [*commands, *debounce]
        return state, commands

    def _on_availability(
        self, event: TrvAvailabilityChanged, now: datetime, state: RoomState
    ) -> _Step:
        """Track a thermostat going away or coming back; resync it when it returns."""
        trv = event.entity_id
        current = state.trvs[trv]
        if current.available == event.available:
            return state, []
        if not event.available:
            gone = replace(
                current,
                available=False,
                health=TrvHealth.UNAVAILABLE,
                resync_pending={},
            )
            return self._with_trv(state, trv, gone), []
        back = replace(current, available=True)
        back = replace(back, health=self._baseline(back))
        state, commands = self._resync_trv(self._with_trv(state, trv, back), now, trv)
        state, climate = observe.resync_trv(
            self._cfg, state, now, trv, self._capability_of
        )
        return state, [*commands, *climate]

    def _on_retry_due(self, event: RetryDue, now: datetime, state: RoomState) -> _Step:
        """Resend the newest intent that is still waiting for its retry."""
        trv, capability = event.trv, event.capability
        current = state.trvs[trv]
        intent = current.pending_retry.get(capability)
        if intent is None:
            return state, []
        updated = replace(
            current, pending_retry=_without(current.pending_retry, capability)
        )
        if trv in self._writable(state):
            state = self._with_trv(state, trv, updated)
            if isinstance(intent, SetSetpoint | SetHvacMode):
                return setpoint.retry(state, now, trv, intent, self._capability_of)
            if setpoint.suspended(state) and isinstance(
                intent, SetRoomTemperature | SetSource
            ):
                return state, []
            return state, [Execute(trv, intent)]
        if isinstance(intent, SETTING_INTENTS):
            # Dropped while the thermostat is away: re-apply on its return.
            updated = replace(updated, settings_applied=False)
        return self._with_trv(state, trv, updated), []

    def _on_result(
        self, event: OperationResult, now: datetime, state: RoomState
    ) -> _Step:
        """Route the outcome of a finished operation."""
        outcome = event.outcome
        guarded = False
        if isinstance(event.intent, SetSetpoint | SetHvacMode):
            # The result of a resync write ends the time its reports are not trusted.
            known = state.trvs[event.trv]
            guarded = event.capability in known.resync_pending
            if guarded:
                released = observe.release_resync(known, event.capability, event.intent)
                state = self._with_trv(state, event.trv, released)
        match outcome:
            case Ok():
                return self._on_ok(event, now, state)
            case Failed() | Mismatch():
                return self._on_failed(event, outcome, now, state, guarded=guarded)
            case Unsupported():
                return self._on_unsupported(event, outcome, state)
            case Signal(name="failsafe") if isinstance(event.intent, SetSource):
                # The read-back of the re-enable write itself: not a new
                # fail-safe, otherwise a thermostat that stays in fail-safe
                # would be re-enabled again and again.
                return self._on_failure(event, outcome, now, state)
            case Signal(name="schedule") if isinstance(event.intent, SetHvacMode):
                return self._on_schedule_readback(event, now, state)
            case Signal():
                return self._on_signal(event, outcome, now, state)
            case _:
                assert_never(outcome)

    def _on_failed(
        self,
        event: OperationResult,
        outcome: Failed | Mismatch,
        now: datetime,
        state: RoomState,
        *,
        guarded: bool,
    ) -> _Step:
        """Retry a failed write, unless the read-back found somebody else's value.

        A mismatch on a resync write is never taken for such a value: the
        thermostat may just not have taken our write yet.
        """
        if (
            isinstance(outcome, Mismatch)
            and not guarded
            and self._is_foreign(event, outcome, now, state)
        ):
            return self._on_foreign_readback(event, outcome, now, state)
        return self._on_failure(event, outcome, now, state)

    def _is_foreign(
        self,
        event: OperationResult,
        outcome: Mismatch,
        now: datetime,
        state: RoomState,
    ) -> bool:
        """Return whether a read-back shows a value somebody else set, not ours."""
        if (
            not isinstance(event.intent, SetSetpoint | SetHvacMode)
            or outcome.observed is None
            or outcome.actual is None
        ):
            return False
        known = state.trvs[event.trv]
        return not observe.is_echo(known, event.capability, outcome.observed, now)

    def _on_foreign_readback(
        self,
        event: OperationResult,
        outcome: Mismatch,
        now: datetime,
        state: RoomState,
    ) -> _Step:
        """Treat a read-back that found somebody else's value as an observation.

        The write is over (the value that was there supersedes it): no retry.
        """
        trv, capability = event.trv, event.capability
        current = state.trvs[trv]
        state = self._with_trv(
            state,
            trv,
            replace(
                current,
                attempts=_without(current.attempts, capability),
                pending_retry=_without(current.pending_retry, capability),
            ),
        )
        observed = TrvObserved(
            trv, capability, outcome.observed, outcome.actual or 0, "report"
        )
        return observe.on_observed(self._cfg, state, now, observed, self._capability_of)

    def _on_schedule_readback(
        self, event: OperationResult, now: datetime, state: RoomState
    ) -> _Step:
        """Handle a mode write whose read-back found the thermostat on its schedule.

        The write is no failure: the thermostat is left alone from now on.
        """
        trv, capability = event.trv, event.capability
        current = state.trvs[trv]
        state = self._with_trv(
            state,
            trv,
            replace(
                current,
                attempts=_without(current.attempts, capability),
                pending_retry=_without(current.pending_retry, capability),
            ),
        )
        observed = TrvObserved(trv, capability, None, 1, "report")
        return observe.on_observed(self._cfg, state, now, observed, self._capability_of)

    def _on_ok(self, event: OperationResult, now: datetime, state: RoomState) -> _Step:
        """Forget earlier trouble with the capability and record a verified value.

        An error ends only when the capability that caused it verifies; a
        verified write of any other capability says nothing about it.
        """
        trv, capability = event.trv, event.capability
        current = state.trvs[trv]
        updated = replace(
            current,
            attempts=_without(current.attempts, capability),
            pending_retry=_without(current.pending_retry, capability),
            unsupported=current.unsupported - {capability},
            not_in_profile=current.not_in_profile - {capability},
        )
        commands: list[Command] = []
        if capability in current.unsupported - current.not_in_profile:
            commands.append(ClearIssue("firmware", trv))
        still_failing = False
        if self._reported(current):
            if current.error_capability in (None, capability):
                updated = replace(updated, last_error=None, error_capability=None)
                commands += [
                    ClearIssue("unreachable", trv),
                    FireEvent(EVENT_RECOVERED, self._event_data(trv)),
                    Log("info", f"{self._cfg.name}: {trv} accepts writes again"),
                ]
            else:
                still_failing = True
                note = (
                    f"{trv}: {capability} verified, still failing on "
                    f"{current.error_capability}"
                )
                commands.append(Log("debug", note))
        keeps_error = still_failing and updated.health is TrvHealth.ERROR
        if (
            updated.health in (TrvHealth.ERROR, TrvHealth.UNSUPPORTED)
            and not keeps_error
        ):
            updated = replace(updated, health=self._baseline(updated))
        state = self._with_trv(state, trv, updated)
        if isinstance(event.intent, SetRoomTemperature):
            state = replace(
                state, last_value_write=now, last_written=event.intent.celsius
            )
        return state, commands

    def _on_failure(
        self,
        event: OperationResult,
        outcome: Failed | Mismatch | Signal,
        now: datetime,
        state: RoomState,
    ) -> _Step:
        """Count the failed attempt, then retry with backoff or give up."""
        trv, capability = event.trv, event.capability
        current = state.trvs[trv]
        error = self._error_text(outcome)
        attempts = current.attempts.get(capability, 0) + 1
        updated = replace(
            current,
            attempts={**current.attempts, capability: attempts},
            pending_retry=_without(current.pending_retry, capability),
        )
        if attempts < self._params.max_attempts:
            retry = self._retry_intent(event.intent, state)
            updated = replace(
                updated, pending_retry={**updated.pending_retry, capability: retry}
            )
            backoff = self._params.backoff
            delay = backoff[min(attempts - 1, len(backoff) - 1)]
            return self._with_trv(state, trv, updated), [
                ScheduleRetry(trv, capability, delay)
            ]
        reported = self._reported(current)
        updated = replace(updated, health=TrvHealth.ERROR, last_error=error)
        if not reported:
            updated = replace(updated, error_capability=capability)
        if updated.error_capability == capability:
            # Only the reported capability moves the clock: another write that
            # keeps failing must not postpone the one that is sent again.
            updated = replace(updated, last_error_retry=now)
        if isinstance(event.intent, SETTING_INTENTS):
            # The setting never arrived: apply it again on the next resync.
            updated = replace(updated, settings_applied=False)
        state = self._with_trv(state, trv, updated)
        if reported:
            return state, []
        data = {"attribute": capability, "attempts": attempts, "error": error}
        return state, [
            RaiseIssue(
                "unreachable",
                trv,
                {
                    "room": self._cfg.name,
                    "trv": trv,
                    "attribute": capability,
                    "attempts": str(attempts),
                    "error": error,
                },
            ),
            FireEvent(EVENT_WRITE_FAILED, {**self._event_data(trv), **data}),
            Log(
                "warning",
                f"{self._cfg.name}: {trv} did not accept {capability} "
                f"after {attempts} attempts: {error}",
            ),
        ]

    def _on_unsupported(
        self, event: OperationResult, outcome: Unsupported, state: RoomState
    ) -> _Step:
        """Flag the capability as missing; it is never retried.

        A capability the profile does not declare (``reason == "profile"``) is
        remembered so it is not written again, but is no firmware problem: no
        issue, and the health stays as it was.
        """
        trv, capability = event.trv, event.capability
        current = state.trvs[trv]
        in_profile = outcome.reason != "profile"
        updated = replace(
            current,
            unsupported=current.unsupported | {capability},
            not_in_profile=(
                current.not_in_profile
                if in_profile
                else current.not_in_profile | {capability}
            ),
            pending_retry=_without(current.pending_retry, capability),
        )
        commands: list[Command] = []
        if in_profile:
            if current.health is not TrvHealth.ERROR:
                updated = replace(updated, health=self._baseline(updated))
            commands.append(
                RaiseIssue(
                    "firmware",
                    trv,
                    {
                        "trv": trv,
                        "capability": outcome.capability,
                        "required": outcome.required,
                        "installed": outcome.installed,
                    },
                )
            )
        elif capability not in current.not_in_profile:
            commands.append(
                Log(
                    "debug",
                    f"{self._cfg.name}: {trv} has no {capability} setting in its "
                    "profile, leaving it alone",
                )
            )
        if self._adaptive_was_in_charge(capability):
            # Adaptive mode is what suppressed the hysteresis write; without it
            # the thermostat needs the hysteresis (in the next apply if away).
            if trv in self._writable(state):
                commands.append(Execute(trv, SetHysteresis(self._cfg.hysteresis)))
            else:
                updated = replace(updated, settings_applied=False)
        return self._with_trv(state, trv, updated), commands

    def _on_signal(
        self,
        event: OperationResult,
        outcome: Signal,
        now: datetime,
        state: RoomState,
    ) -> _Step:
        """React to the thermostat falling back to its own sensor (fail-safe).

        The profile reads the fail-safe flag back on every value write, so the
        re-enable's own value write can report it again. Re-enabling only
        happens when the source write is not already failing, which bounds it.
        """
        trv = event.trv
        if outcome.name != "failsafe" or not isinstance(
            event.intent, SetRoomTemperature
        ):
            note = f"{trv}: ignoring signal {outcome.name!r} on {event.capability}"
            return state, [Log("debug", note)]
        if state.source is not Source.EXTERNAL:
            note = f"{trv}: ignoring fail-safe, source is {state.source.value}"
            return state, [Log("debug", note)]
        current = state.trvs[trv]
        state = self._with_trv(state, trv, replace(current, failsafe_seen_at=now))
        if setpoint.suspended(state):
            return state, [
                Log("debug", f"{trv}: fail-safe not re-enabled, room is idle")
            ]
        blocker = self._reenable_blocker(current)
        if blocker is not None:
            note = f"{trv}: fail-safe not re-enabled, {blocker}"
            return state, [Log("debug", note)]
        commands: list[Command] = [
            FireEvent(EVENT_FAILSAFE, self._event_data(trv)),
            Log("info", f"{self._cfg.name}: {trv} fell back to its own sensor"),
        ]
        active = self._active(state, now)
        if active is not None:
            commands += [
                Execute(trv, SetSource("external")),
                Execute(trv, SetRoomTemperature(round_tenth(active.value))),
            ]
        return state, commands

    # --- Paths -----------------------------------------------------------

    def _apply_settings(self, state: RoomState, trvs: list[str]) -> _Step:
        """Emit the configured settings to thermostats that have not had them yet."""
        updated = dict(state.trvs)
        commands: list[Command] = []
        for trv in trvs:
            if updated[trv].settings_applied:
                continue
            commands.extend(Execute(trv, i) for i in self._settings(updated[trv]))
            updated[trv] = replace(updated[trv], settings_applied=True)
        return replace(state, trvs=updated), commands

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
            return self._use_external(state, active, now)
        return replace(state, active_sensor=active.entity_id), []

    def _use_internal(self, state: RoomState) -> _Step:
        """Tell every writable thermostat to regulate on its own sensor."""
        commands: list[Command] = [
            Execute(trv, SetSource("internal"))
            for trv in self._writable(state)
            if not setpoint.suspended(state)
        ]
        return replace(state, source=Source.INTERNAL, active_sensor=None), commands

    def _use_external(self, state: RoomState, active: _Active, now: datetime) -> _Step:
        """Switch to the external source and send the current value at once."""
        commands: list[Command] = [
            Execute(trv, SetSource("external"))
            for trv in self._writable(state)
            if not setpoint.suspended(state)
        ]
        state = replace(state, source=Source.EXTERNAL, active_sensor=active.entity_id)
        state, value_commands = self._write_value(state, active, now)
        return state, [*commands, *value_commands]

    def _write_value(self, state: RoomState, active: _Active, now: datetime) -> _Step:
        """Send the rounded active value to every writable thermostat.

        A room that is off or on hold writes nothing; the value follows with the
        resync when it heats again.
        """
        if setpoint.suspended(state):
            return replace(state, debounce_pending=False), []
        celsius = round_tenth(active.value)
        commands: list[Command] = [
            Execute(trv, SetRoomTemperature(celsius)) for trv in self._writable(state)
        ]
        state = replace(
            state,
            last_written=celsius,
            last_value_issued=now,
            debounce_pending=False,
        )
        return state, commands

    def _debounce_if_changed(self, state: RoomState, now: datetime) -> _Step:
        """Arm (or re-arm) the debounce when the value moved by ``min_delta``."""
        active = self._active(state, now)
        if active is None or not self._differs(state, active.value):
            return state, []
        commands: list[Command] = [ScheduleDebounce(self._params.debounce)]
        return replace(state, debounce_pending=True), commands

    def _refresh(self, state: RoomState, now: datetime) -> _Step:
        """Resend the value once the newest value write is older than ``refresh``.

        A write counts from when it was verified or, if it never was, from when
        it was issued, so a thermostat whose writes keep failing is retried
        once per interval instead of never (or on every tick).
        """
        stamps = [
            stamp
            for stamp in (state.last_value_write, state.last_value_issued)
            if stamp is not None
        ]
        if (
            state.source is not Source.EXTERNAL
            or not stamps
            or now - max(stamps) < self._params.refresh
        ):
            return state, []
        active = self._active(state, now)
        if active is None:
            return state, []
        return self._write_value(state, active, now)

    def _retry_failed(self, state: RoomState, now: datetime) -> _Step:
        """Send a capability again that a thermostat gave up on.

        Only the room temperature is refreshed on its own; the source and the
        settings are otherwise written when something changes. A thermostat in
        error on one of them would stay there, so once per ``refresh`` the write
        that raised the error is issued again with a fresh set of attempts. The
        value write that follows in the same tick covers an error on the value.
        """
        trvs = dict(state.trvs)
        commands: list[Command] = []
        for trv in self._writable(state):
            current = trvs[trv]
            capability = current.error_capability
            intents = self._reissuable(state, current)
            intent = intents.get(capability) if capability is not None else None
            attempts = current.attempts.get(capability, 0) if capability else 0
            if (
                current.health is not TrvHealth.ERROR
                or capability is None
                or intent is None
                or (
                    current.last_error_retry is not None
                    and now - current.last_error_retry < self._params.refresh
                )
                # A chain of retries is still running: let it finish first.
                or capability in current.pending_retry
                or 0 < attempts < self._params.max_attempts
                or (setpoint.suspended(state) and isinstance(intent, SetSource))
            ):
                continue
            trvs[trv] = replace(
                current,
                attempts=_without(current.attempts, capability),
                pending_retry=_without(current.pending_retry, capability),
                last_error_retry=now,
            )
            commands.append(Execute(trv, intent))
        return replace(state, trvs=trvs), commands

    def _settings(self, trv_state: TrvState) -> list[Intent]:
        """Return the settings to write to a thermostat, in write order.

        The hysteresis is left out while adaptive mode is on, since adaptive mode
        manages it itself, unless the thermostat cannot do adaptive mode.
        """
        cfg = self._cfg
        intents: list[Intent] = []
        if cfg.window_detection != "leave":
            intents.append(SetWindowDetection(cfg.window_detection == "on"))
        if cfg.adaptive_mode != "leave":
            intents.append(SetAdaptiveMode(cfg.adaptive_mode == "on"))
        adaptive = self._capability_of(SetAdaptiveMode(enabled=True))
        adaptive_in_charge = (
            cfg.adaptive_mode == "on" and adaptive not in trv_state.unsupported
        )
        if not adaptive_in_charge:
            intents.append(SetHysteresis(cfg.hysteresis))
        intents.append(SetFrostTemperature(cfg.frost_temperature))
        return intents

    def _adaptive_was_in_charge(self, capability: str) -> bool:
        """Return whether ``capability`` is the adaptive mode that owns hysteresis."""
        return self._cfg.adaptive_mode == "on" and capability == self._capability_of(
            SetAdaptiveMode(enabled=True)
        )

    def _reissuable(self, state: RoomState, trv_state: TrvState) -> dict[str, Intent]:
        """Map each capability the room writes besides the value to its intent."""
        intents: list[Intent] = []
        if state.source is Source.EXTERNAL:
            intents.append(SetSource("external"))
        elif state.source is Source.INTERNAL:
            intents.append(SetSource("internal"))
        intents.extend(self._settings(trv_state))
        return {self._capability_of(intent): intent for intent in intents}

    def _resync_trv(self, state: RoomState, now: datetime, trv: str) -> _Step:
        """Bring a returning thermostat in line with the room's current state."""
        state, commands = self._apply_settings(state, [trv])
        if setpoint.suspended(state):
            return state, commands
        if state.source is Source.EXTERNAL:
            commands.append(Execute(trv, SetSource("external")))
            active = self._active(state, now)
            if active is not None:
                celsius = round_tenth(active.value)
                commands.append(Execute(trv, SetRoomTemperature(celsius)))
        elif state.source is Source.INTERNAL:
            commands.append(Execute(trv, SetSource("internal")))
        return state, commands

    def _source_intents(
        self, state: RoomState, now: datetime
    ) -> tuple[RoomState, list[Intent]]:
        """Return the source and value writes that bring a thermostat up to date."""
        if state.source is Source.INTERNAL:
            return state, [SetSource("internal")]
        if state.source is not Source.EXTERNAL:
            return state, []
        intents: list[Intent] = [SetSource("external")]
        active = self._active(state, now)
        if active is not None:
            celsius = round_tenth(active.value)
            intents.append(SetRoomTemperature(celsius))
            state = replace(
                state,
                last_written=celsius,
                last_value_issued=now,
                debounce_pending=False,
            )
        return state, intents

    # --- Helpers ---------------------------------------------------------

    def _reenable_blocker(self, trv_state: TrvState) -> str | None:
        """Return why a fail-safe must not be re-enabled now, or ``None``."""
        source = self._capability_of(SetSource("external"))
        if not trv_state.available:
            return "thermostat is unavailable"
        if trv_state.health is TrvHealth.ERROR:
            return "thermostat is in error"
        if source in trv_state.unsupported:
            return "source is not supported"
        if trv_state.attempts.get(source, 0) or source in trv_state.pending_retry:
            return "source write is already being retried"
        return None

    @staticmethod
    def _reported(trv_state: TrvState) -> bool:
        """Return whether the thermostat's error was already reported.

        ``last_error`` outlives the health, which falls back to unavailable or
        ok while the thermostat is away, so it is the mark of an issue that is
        still raised.
        """
        return trv_state.health is TrvHealth.ERROR or trv_state.last_error is not None

    @staticmethod
    def _retry_intent(intent: Intent, state: RoomState) -> Intent:
        """Return what a retry should send: never an older temperature."""
        if isinstance(intent, SetRoomTemperature) and state.last_written is not None:
            return SetRoomTemperature(state.last_written)
        return setpoint.current(state, intent) or intent

    def _drop_unsupported(
        self, state: RoomState, commands: list[Command]
    ) -> list[Command]:
        """Skip writes of capabilities the thermostat's firmware lacks."""
        return [
            command
            for command in commands
            if not isinstance(command, Execute)
            or self._capability_of(command.intent)
            not in state.trvs[command.trv].unsupported
        ]

    def _supersede_pending(
        self, state: RoomState, commands: list[Command]
    ) -> RoomState:
        """Let a newer write replace the intent a pending retry would resend."""
        for command in commands:
            if not isinstance(command, Execute):
                continue
            capability = self._capability_of(command.intent)
            current = state.trvs[command.trv]
            if capability in current.pending_retry:
                pending = {**current.pending_retry, capability: command.intent}
                state = self._with_trv(
                    state, command.trv, replace(current, pending_retry=pending)
                )
        return state

    @staticmethod
    def _with_trv(state: RoomState, trv: str, trv_state: TrvState) -> RoomState:
        """Return ``state`` with the state of one thermostat replaced."""
        return replace(state, trvs={**state.trvs, trv: trv_state})

    @staticmethod
    def _baseline(trv_state: TrvState) -> TrvHealth:
        """Return the health of a thermostat that has no error of its own."""
        if not trv_state.available:
            return TrvHealth.UNAVAILABLE
        if trv_state.unsupported - trv_state.not_in_profile:
            return TrvHealth.UNSUPPORTED
        return TrvHealth.OK

    @staticmethod
    def _error_text(outcome: Failed | Mismatch | Signal) -> str:
        """Describe a failed write for issues and the status."""
        if isinstance(outcome, Failed):
            return outcome.error
        if isinstance(outcome, Signal):
            return f"signal: {outcome.name}"
        return f"mismatch: expected {sorted(outcome.expected)} got {outcome.actual}"

    def _event_data(self, trv: str) -> dict[str, Any]:
        """Return the identifying payload shared by all bus events."""
        return {
            "room": self._cfg.name,
            "room_id": self._cfg.room_id,
            "trv_entity_id": trv,
            "ieee": self._ieee[trv],
        }

    def _active(self, state: RoomState, now: datetime) -> _Active | None:
        """Return the active sensor with a concrete value, if any."""
        reading = self.active_sensor(state, now)
        if reading is None or reading.value is None:
            return None
        return _Active(reading.entity_id, reading.value)

    def _active_pair(self, state: RoomState, now: datetime) -> tuple[str, float] | None:
        """Return the active sensor and its value, for the window check."""
        active = self._active(state, now)
        return None if active is None else (active.entity_id, active.value)

    def _differs(self, state: RoomState, value: float) -> bool:
        """Return whether ``value`` is at least ``min_delta`` from the last write."""
        last = state.last_written
        if last is None:
            return True
        return abs(value - last) + _DELTA_TOLERANCE >= self._params.min_delta

    def _writable(self, state: RoomState) -> list[str]:
        """Return the thermostats that can currently receive commands."""
        return [
            trv.entity_id
            for trv in self._cfg.trvs
            if state.trvs[trv.entity_id].available
        ]

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
