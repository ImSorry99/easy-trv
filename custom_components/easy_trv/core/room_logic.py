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
    EVENT_AWAY,
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
    OperatingMode,
    OperationResult,
    OutdoorChanged,
    PresenceChanged,
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
    SetRoomOperation,
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
from custom_components.easy_trv.core.room_state import failsafe_recent, internal_source
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
        before = state
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
            case SetRoomSetpoint() | SetRoomMode() | SetRoomOperation() | RestoreRoom():
                state, commands = self._on_room_event(event, now, state)
            case TrvObserved():
                state, commands = observe.on_observed(
                    self._cfg, state, now, event, self._capability_of
                )
            case KnobSettled():
                state, commands = self._on_knob_settled(event, now, state)
            case WindowChanged() | OutdoorChanged() | PresenceChanged():
                state, commands = self._on_hold_event(event, now, state)
            case _:
                assert_never(event)
        state, settled = self._settle(before, state)
        commands = [
            *self._drop_unsupported(state, commands),
            *self._left_off(before, state),
            *self._away_event(before, state),
            *settled,
        ]
        state = self._supersede_pending(state, commands)
        state = self._mark_resent(state, commands)
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
        state = replace(
            state,
            readings=readings,
            trvs=trvs,
            away=event.away,
            away_off=event.away and self._cfg.away_action == "off",
            away_override=state.away_override and event.away,
        )
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
        event: SetRoomSetpoint | SetRoomMode | SetRoomOperation | RestoreRoom,
        now: datetime,
        state: RoomState,
    ) -> _Step:
        """Route the events that set what the room owns to ``setpoint``."""
        cfg, capability_of = self._cfg, self._capability_of
        match event:
            case SetRoomSetpoint():
                return self._on_set_setpoint(event, now, state)
            case SetRoomMode():
                return self._on_set_mode(event, now, state)
            case SetRoomOperation():
                return self._on_set_operation(event, now, state)
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
        """Switch the room on or off; switching on resyncs source and value first.

        Switching on a room that heats already writes nothing.
        """
        resync: list[Intent] = []
        if (
            event.mode is RoomMode.HEAT
            and state.mode is not RoomMode.HEAT
            and self._cfg.setpoint_range is not None
            and not setpoint.holding(state)
        ):
            state, resync = self._source_intents(state, now)
        return setpoint.on_set_mode(
            self._cfg, state, now, event.mode, self._capability_of, resync
        )

    def _on_set_setpoint(
        self, event: SetRoomSetpoint, now: datetime, state: RoomState
    ) -> _Step:
        """Set the room's target; one that ends the away hold resyncs first."""
        resync: list[Intent] = []
        if setpoint.held_by_away_only(state):
            state, resync = self._source_intents(state, now)
        return setpoint.on_set_setpoint(
            self._cfg, state, now, event.celsius, self._capability_of, resync
        )

    def _on_set_operation(
        self, event: SetRoomOperation, now: datetime, state: RoomState
    ) -> _Step:
        """Select off, frost or heat; leaving off resyncs source and value first."""
        resync: list[Intent] = []
        if event.operation is not OperatingMode.OFF and (
            setpoint.leaving_off(self._cfg, state, RoomMode.HEAT)
            or setpoint.held_by_away_only(state)
        ):
            state, resync = self._source_intents(state, now)
        return setpoint.on_set_operation(
            self._cfg, state, now, event.operation, self._capability_of, resync
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
        self,
        event: WindowChanged | OutdoorChanged | PresenceChanged,
        now: datetime,
        state: RoomState,
    ) -> _Step:
        """Route a window or outdoor change to ``hold``, presence to ``setpoint``."""
        cfg, capability_of = self._cfg, self._capability_of
        if isinstance(event, PresenceChanged):
            return hold.on_presence(
                cfg, state, now, event.away, capability_of, self._source_intents
            )
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
        """Resend the newest intent that is still waiting for its retry.

        A retry that sends nothing (the room is off, the thermostat runs its own
        schedule or is away) ends its chain: the attempts are forgotten, so the
        next write of the capability starts a fresh chain with the backoff.
        """
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
                state, commands = setpoint.retry(
                    self._cfg, state, now, trv, intent, self._capability_of
                )
                if not any(
                    isinstance(command, Execute)
                    and command.trv == trv
                    and self._capability_of(command.intent) == capability
                    for command in commands
                ):
                    state = self._forget_attempts(state, trv, capability)
                return state, commands
            if setpoint.suspended(state) and isinstance(
                intent, SetRoomTemperature | SetSource
            ):
                return self._forget_attempts(state, trv, capability), []
            return state, [Execute(trv, intent)]
        if isinstance(intent, SETTING_INTENTS):
            # Dropped while the thermostat is away: re-apply on its return.
            updated = replace(updated, settings_applied=False)
        state = self._with_trv(state, trv, updated)
        return self._forget_attempts(state, trv, capability), []

    def _forget_attempts(
        self, state: RoomState, trv: str, capability: str
    ) -> RoomState:
        """Return ``state`` without the attempts of a chain that ended unsent."""
        current = state.trvs[trv]
        if capability not in current.attempts:
            return state
        return self._with_trv(
            state,
            trv,
            replace(current, attempts=_without(current.attempts, capability)),
        )

    def _on_result(
        self, event: OperationResult, now: datetime, state: RoomState
    ) -> _Step:
        """Route the outcome of a finished operation."""
        outcome = event.outcome
        guarded = False
        sent = state.trvs[event.trv]
        if event.capability in sent.resent:
            # Whatever it is, the result ends the wait for this write.
            sent = replace(sent, resent=sent.resent - {event.capability})
            state = self._with_trv(state, event.trv, sent)
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
                return self._on_schedule_readback(event, outcome, now, state)
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
            and observe.is_foreign_readback(
                state.trvs[event.trv], event.intent, event.capability, outcome, now
            )
        ):
            observed = TrvObserved(
                event.trv, event.capability, outcome.observed, outcome.actual or 0
            )
            return observe.on_readback(
                self._cfg, state, now, observed, self._capability_of
            )
        return self._on_failure(event, outcome, now, state)

    def _on_schedule_readback(
        self, event: OperationResult, outcome: Signal, now: datetime, state: RoomState
    ) -> _Step:
        """Handle a mode write whose read-back found the thermostat on its schedule.

        The write is no failure: the thermostat is left alone from now on. The
        ``off`` of a hold is the exception, it has to arrive and is tried again.
        """
        intent = event.intent
        if (
            isinstance(intent, SetHvacMode)
            and intent.mode == "off"
            and setpoint.holding(state)
        ):
            return self._on_failure(event, outcome, now, state)
        observed = TrvObserved(event.trv, event.capability, None, 1)
        return observe.on_readback(self._cfg, state, now, observed, self._capability_of)

    def _on_ok(self, event: OperationResult, now: datetime, state: RoomState) -> _Step:
        """Forget earlier trouble with the capability and record a verified value.

        An error ends only when nothing is given up on any more that the room
        still writes to the thermostat; a verified write of any other capability
        says nothing about the one that did not arrive. When the one that owns
        the issue arrives (or is not written any more) and another has not, the
        issue moves on to that.
        """
        trv, capability = event.trv, event.capability
        current = state.trvs[trv]
        updated = replace(
            current,
            attempts=_without(current.attempts, capability),
            pending_retry=_without(current.pending_retry, capability),
            unsupported=current.unsupported - {capability},
            not_in_profile=current.not_in_profile - {capability},
            given_up=current.given_up - {capability},
            errors=_without(current.errors, capability),
        )
        commands: list[Command] = []
        if capability in current.unsupported - current.not_in_profile:
            commands.append(ClearIssue("firmware", trv))
        still_failing = False
        if self._reported(current):
            updated, notice, still_failing = self._follow_notice(
                state, trv, updated, capability
            )
            commands += notice
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

    def _follow_notice(
        self, state: RoomState, trv: str, updated: TrvState, verified: str | None
    ) -> tuple[TrvState, list[Command], bool]:
        """Decide what the issue of a thermostat does now that ``updated`` holds.

        It stays with the capability that owns it as long as the room still owes
        that one, moves on to the first it still owes (by name) when that one
        arrived, was forgotten or is not written any more, and ends, said once,
        when none is left. ``verified`` is the capability whose write just
        arrived, if that is what happened. Returns the thermostat, the commands
        and whether the issue goes on.
        """
        owed = self._owed(state, updated)
        owner = updated.error_capability
        if owner is not None and owner != verified and owner in owed:
            note: list[Command] = []
            if verified is not None:
                text = f"{trv}: {verified} verified, still failing on {owner}"
                note.append(Log("debug", text))
            return updated, note, True
        if owed:
            next_capability = owed[0]
            updated = replace(
                updated,
                error_capability=next_capability,
                last_error=updated.errors.get(next_capability, updated.last_error),
            )
            return updated, self._still_failing(trv, updated, next_capability), True
        updated = replace(updated, last_error=None, error_capability=None)
        return (
            updated,
            [
                ClearIssue("unreachable", trv),
                FireEvent(EVENT_RECOVERED, self._event_data(trv)),
                Log("info", f"{self._cfg.name}: {trv} accepts writes again"),
            ],
            False,
        )

    def _settle(self, before: RoomState, state: RoomState) -> _Step:
        """Forget what the room does not owe a thermostat any more, after any event.

        A value that was given up on and that the room no longer writes (it went
        off, the thermostat is on its schedule, the read-back showed another
        value, the firmware lacks it) is forgotten with its attempts, so a later
        write starts a fresh chain. The issue of a thermostat that had one before
        the event follows: it moves on or, with nothing left, ends, once. An
        issue raised by the event itself is left alone.
        """
        commands: list[Command] = []
        for trv, known in before.trvs.items():
            if not self._reported(known):
                continue
            current = state.trvs[trv]
            updated = current
            stale = current.given_up - set(self._owed(state, current))
            if stale:
                updated = replace(
                    updated,
                    attempts={
                        k: v for k, v in updated.attempts.items() if k not in stale
                    },
                    given_up=updated.given_up - stale,
                    errors={k: v for k, v in updated.errors.items() if k not in stale},
                )
            if self._reported(updated):
                updated, notice, going = self._follow_notice(state, trv, updated, None)
                commands += notice
                if not going and updated.health in (
                    TrvHealth.ERROR,
                    TrvHealth.UNSUPPORTED,
                ):
                    updated = replace(updated, health=self._baseline(updated))
            if updated != current:
                state = self._with_trv(state, trv, updated)
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
        updated = replace(
            updated,
            health=TrvHealth.ERROR,
            last_error=error,
            given_up=current.given_up | {capability},
            errors={**current.errors, capability: error},
        )
        if current.health is not TrvHealth.ERROR:
            # The thermostat goes into error: the clock for sending again what
            # did not arrive starts now. Failures while it is in error leave the
            # clock alone, it counts from the last time they were sent.
            updated = replace(updated, last_error_retry=now)
        if not reported:
            # The first to give up owns the issue.
            updated = replace(updated, error_capability=capability)
        if isinstance(event.intent, SETTING_INTENTS):
            # The setting never arrived: apply it again on the next resync.
            updated = replace(updated, settings_applied=False)
        state = self._with_trv(state, trv, updated)
        if reported:
            # The notice stands: nothing new for a capability that fails again.
            return state, []
        data = {"attribute": capability, "attempts": attempts, "error": error}
        return state, [
            self._unreachable(trv, capability, attempts, error),
            FireEvent(EVENT_WRITE_FAILED, {**self._event_data(trv), **data}),
            Log(
                "warning",
                f"{self._cfg.name}: {trv} did not accept {capability} "
                f"after {attempts} attempts: {error}",
            ),
        ]

    def _unreachable(
        self, trv: str, capability: str, attempts: int, error: str
    ) -> RaiseIssue:
        """Return the Repairs issue for a capability the thermostat did not take."""
        return RaiseIssue(
            "unreachable",
            trv,
            {
                "room": self._cfg.name,
                "trv": trv,
                "attribute": capability,
                "attempts": str(attempts),
                "error": error,
            },
        )

    def _still_failing(
        self, trv: str, trv_state: TrvState, capability: str
    ) -> list[Command]:
        """Move the issue on to a capability that has not arrived either.

        It cites the attempts that were used up and the error of that capability
        itself. A capability that is being written again has not failed since:
        the issue moves to it, but nothing is said yet about it failing.
        """
        attempts = max(trv_state.attempts.get(capability, 0), self._params.max_attempts)
        error = trv_state.errors.get(capability) or trv_state.last_error or "unknown"
        if self._in_flight(trv_state, capability):
            note = f"{trv}: {capability} is on its way, the issue waits for it"
            return [
                self._unreachable(trv, capability, attempts, error),
                Log("debug", note),
            ]
        return [
            self._unreachable(trv, capability, attempts, error),
            Log(
                "warning",
                f"{self._cfg.name}: {trv} still did not accept {capability} "
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
            attempts=_without(current.attempts, capability),
            pending_retry=_without(current.pending_retry, capability),
            given_up=current.given_up - {capability},
            errors=_without(current.errors, capability),
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
        """Send every capability again that a thermostat gave up on.

        Only the room temperature is refreshed on its own; the source and the
        settings are otherwise written when something changes. A thermostat in
        error would stay there, so once per ``refresh`` everything that did not
        arrive is issued again, each with a fresh set of attempts, for ever. The
        attempts start over, the capability stays given up on until it arrives.
        The value write that follows in the same tick covers an error on the
        value.
        """
        commands: list[Command] = []
        for trv in self._writable(state):
            current = state.trvs[trv]
            if current.health is not TrvHealth.ERROR or (
                current.last_error_retry is not None
                and now - current.last_error_retry < self._params.refresh
            ):
                continue
            intents = self._sendable_again(state, current)
            if not intents:
                continue
            attempts, pending_retry = current.attempts, current.pending_retry
            for intent in intents:
                capability = self._capability_of(intent)
                attempts = _without(attempts, capability)
                pending_retry = _without(pending_retry, capability)
            # One clock for the thermostat: all of them go out together.
            state = self._with_trv(
                state,
                trv,
                replace(
                    current,
                    attempts=attempts,
                    pending_retry=pending_retry,
                    last_error_retry=now,
                ),
            )
            for intent in intents:
                if isinstance(intent, SetSetpoint | SetHvacMode):
                    # Through the room's writes, which remember them as echoes.
                    state, written = setpoint.write(
                        state, now, [(trv, intent)], self._capability_of
                    )
                    commands.extend(written)
                else:
                    commands.append(Execute(trv, intent))
        return state, commands

    def _sendable_again(self, state: RoomState, trv_state: TrvState) -> list[Intent]:
        """Return the writes to a thermostat that are due again, in write order.

        That is every capability that gave up and that the room writes to the
        thermostat now.
        """
        gave_up = trv_state.given_up - trv_state.unsupported
        intents: list[Intent] = []
        for capability, intent in self._reissuable(state, trv_state).items():
            attempts = trv_state.attempts.get(capability, 0)
            if (
                capability not in gave_up
                # A chain of retries is still running: let it finish first.
                or capability in trv_state.pending_retry
                or 0 < attempts < self._params.max_attempts
                # The last send has no result yet: do not queue it twice.
                or capability in trv_state.resent
                or (setpoint.suspended(state) and isinstance(intent, SetSource))
            ):
                continue
            intents.append(intent)
        return intents

    def _owed(self, state: RoomState, trv_state: TrvState) -> list[str]:
        """Return the capabilities that keep the issue open, sorted by name.

        Those are the ones given up on that the room still writes to the
        thermostat: what it sends again, and the room temperature while the
        source is external. One the firmware lacks is never written, one the room
        no longer writes (the mode of a thermostat on its schedule, the setpoint
        of a room that is off) does not matter until it writes it again.
        """
        written = set(self._reissuable(state, trv_state))
        if state.source is Source.EXTERNAL:
            written.add(self._capability_of(SetRoomTemperature(_MIN_TEMPERATURE)))
        return sorted((trv_state.given_up - trv_state.unsupported) & written)

    def _in_flight(self, trv_state: TrvState, capability: str) -> bool:
        """Return whether a write of ``capability`` is out or waiting for a retry."""
        return (
            capability in trv_state.resent
            or capability in trv_state.pending_retry
            or 0 < trv_state.attempts.get(capability, 0) < self._params.max_attempts
        )

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
        """Map each capability the room writes besides the value to its intent.

        That includes the mode and setpoint the room wants on the thermostat now
        (``off`` while the room is off or held, the setpoint only when it heats),
        not for a thermostat the room leaves alone outside a hold.
        """
        intents: list[Intent] = []
        if state.source is Source.EXTERNAL:
            intents.append(SetSource("external"))
        elif state.source is Source.INTERNAL:
            intents.append(SetSource("internal"))
        intents.extend(self._settings(trv_state))
        if self._cfg.setpoint_range is not None and (
            setpoint.holding(state)
            or not (setpoint.device_driven(trv_state) or trv_state.left_off)
        ):
            intents.append(SetHvacMode("off" if setpoint.suspended(state) else "heat"))
            goal = setpoint.target(self._cfg, state)
            if goal is not None and not setpoint.suspended(state):
                intents.append(SetSetpoint(goal))
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

    def _retry_intent(self, intent: Intent, state: RoomState) -> Intent:
        """Return what a retry should send: never an older temperature."""
        if isinstance(intent, SetRoomTemperature) and state.last_written is not None:
            return SetRoomTemperature(state.last_written)
        return setpoint.current(self._cfg, state, intent) or intent

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

    def _mark_resent(self, state: RoomState, commands: list[Command]) -> RoomState:
        """Remember which writes go to a capability that is given up on.

        Its result is outstanding then; until it is in, the issue does not say
        that the capability still fails.
        """
        for command in commands:
            if not isinstance(command, Execute):
                continue
            capability = self._capability_of(command.intent)
            current = state.trvs[command.trv]
            if capability in current.given_up and capability not in current.resent:
                resent = current.resent | {capability}
                state = self._with_trv(
                    state, command.trv, replace(current, resent=resent)
                )
        return state

    def _away_event(self, before: RoomState, after: RoomState) -> list[Command]:
        """Fire ``easy_trv_away`` when presence starts or stops acting on the room."""
        was = setpoint.away_acts(self._cfg, before)
        now = setpoint.away_acts(self._cfg, after)
        if was is now:
            return []
        return [
            FireEvent(
                EVENT_AWAY, {"room": self._cfg.name, "state": "on" if now else "off"}
            )
        ]

    def _left_off(self, before: RoomState, after: RoomState) -> list[Command]:
        """Raise or clear the issue of a thermostat a hold left off, as it changes."""
        commands: list[Command] = []
        for trv in self._cfg.trvs:
            was = before.trvs[trv.entity_id].left_off
            now = after.trvs[trv.entity_id].left_off
            if now and not was:
                commands.append(
                    RaiseIssue(
                        "schedule_thermostat_off",
                        trv.entity_id,
                        {"room": self._cfg.name},
                    )
                )
            elif was and not now:
                commands.append(ClearIssue("schedule_thermostat_off", trv.entity_id))
        return commands

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
        if TrvHealth.ERROR in healths:
            status = RoomStatus.ERROR
        elif (
            internal_source(state, self._cfg)
            or TrvHealth.UNAVAILABLE in healths
            or TrvHealth.UNSUPPORTED in healths
            or failsafe_recent(state, now, self._params.refresh)
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
