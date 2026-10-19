"""What the thermostats report: echoes, knob turns, schedules and window phases.

The functions are pure, in the shape of ``RoomLogic.handle``: they take the room
configuration, the state and the time and return the next state with the
commands to run. A room whose configuration has no ``setpoint_range`` ignores
every observation.
"""

from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING

from custom_components.easy_trv.core.model import (
    EVENT_SETPOINT_ADOPTED,
    Command,
    Execute,
    FireEvent,
    Intent,
    KnobSettled,
    Log,
    Mismatch,
    Note,
    Preset,
    RoomConfig,
    RoomMode,
    RoomState,
    ScheduleKnobSettle,
    SetHvacMode,
    SetSetpoint,
    TrvObserved,
    TrvState,
)
from custom_components.easy_trv.core.setpoint import (
    ECHO_WINDOW,
    OWN_MODES,
    SAME,
    away_acts,
    clamp,
    device_driven,
    held_by_away_only,
    holding,
    lowered,
    on_schedule,
    resume,
    suspended,
    target,
    writable,
    write,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

type _Step = tuple[RoomState, list[Command]]
type _Capabilities = Callable[[Intent], str]

_AUTO = "auto"
# The raw ``system_mode`` of the thermostat's own schedule.
_AUTO_RAW = 1
# How long a knob must rest before its value is taken over (turning through
# 20.5, 21, 21.5 is one adoption).
KNOB_DEBOUNCE = 3.0
# The setpoint a thermostat shows during its open-window phase, and how long
# the room waits for it to leave that value before it writes its own again.
WINDOW_PHASE_SETPOINT = 12.0
WINDOW_PHASE_LIMIT = timedelta(minutes=30)


def is_echo(
    known: TrvState, capability: str, value: float | str | None, now: datetime
) -> bool:
    """Return whether ``value`` is one the room wrote in the last ``ECHO_WINDOW``.

    A write counts only if the thermostat has not reported a value that was no
    echo since: a knob turned away from the room's value and back again is a
    turn, not the echo of the older write.
    """
    if value is None:
        return False
    since = known.foreign_at.get(capability)
    return any(
        _equal(written, value)
        and now - at <= ECHO_WINDOW
        and (since is None or at >= since)
        for written, at in known.written_recently.get(capability, ())
    )


def is_stale_readback(
    known: TrvState,
    capability: str,
    reported: float | str | None,
    observed: float | str,
) -> bool:
    """Return whether a read-back shows what the thermostat reported before our write.

    That is the value it had until our write: it has not taken the write yet,
    which is a failure to retry, not a value somebody else set. A report that
    came after the write (a knob turn) is no such value.
    """
    if reported is None or not _equal(reported, observed):
        return False
    newest = max(
        (at for _, at in known.written_recently.get(capability, ())), default=None
    )
    stamp = known.reported_at.get(capability)
    return newest is not None and (stamp is None or stamp <= newest)


def is_foreign_readback(
    known: TrvState,
    intent: Intent,
    capability: str,
    outcome: Mismatch,
    now: datetime,
) -> bool:
    """Return whether a read-back shows a value somebody else set, not ours.

    Neither our own recent value nor the one the thermostat reported before our
    write (it has not applied the write yet) is such a value.
    """
    if (
        not isinstance(intent, SetSetpoint | SetHvacMode)
        or outcome.observed is None
        or outcome.actual is None
    ):
        return False
    reported = (
        known.reported_setpoint
        if isinstance(intent, SetSetpoint)
        else known.reported_mode
    )
    return not (
        is_echo(known, capability, outcome.observed, now)
        or is_stale_readback(known, capability, reported, outcome.observed)
    )


def on_readback(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    observed: TrvObserved,
    capability_of: _Capabilities,
) -> _Step:
    """Take what a write's read-back found as a report, and end that write.

    The value that was there supersedes the write: it is no failure and gets no
    retry. Used for somebody else's value and for a thermostat on its schedule.
    A write the room gave up on is given up no more: the value found replaces it.
    """
    trv, capability = observed.trv, observed.capability
    known = state.trvs[trv]
    known = replace(
        known,
        attempts=_dropped(known.attempts, capability),
        pending_retry=_dropped(known.pending_retry, capability),
        given_up=known.given_up - {capability},
        errors=_dropped(known.errors, capability),
    )
    return on_observed(cfg, _store(state, trv, known), now, observed, capability_of)


def on_observed(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    event: TrvObserved,
    capability_of: _Capabilities,
) -> _Step:
    """Take a value a thermostat reported (or had written by someone else).

    Every report updates what the thermostat is known to show. An echo of a
    recent write stops there. ``auto`` and the open-window setpoint are shown,
    not adopted. Any other change is a knob turn: it waits for the debounce
    (``KnobSettled``) so that several steps become one adoption.
    """
    if cfg.setpoint_range is None or event.trv not in state.trvs:
        return state, []
    value = event.value
    if event.capability == capability_of(SetSetpoint(0.0)):
        if isinstance(value, int | float) and not isinstance(value, bool):
            return _observe_setpoint(
                cfg, state, now, event, float(value), capability_of
            )
        return state, []
    if event.capability == capability_of(SetHvacMode("heat")):
        return _observe_mode(cfg, state, now, event, capability_of)
    return state, []


def knob_wakes_room(
    cfg: RoomConfig,
    state: RoomState,
    event: KnobSettled,
    capability_of: _Capabilities,
) -> bool:
    """Return whether adopting the settled knob turn takes the room out of ``off``."""
    off_room = state.mode is RoomMode.OFF and not holding(state)
    if (
        cfg.setpoint_range is None
        or event.trv not in state.trvs
        or not (off_room or held_by_away_only(state))
    ):
        return False
    known = state.trvs[event.trv]
    pending = known.knob_pending.get(event.capability)
    if (
        pending is None
        or device_driven(known)
        or event.capability in known.resync_pending
    ):
        return False
    if event.capability == capability_of(SetSetpoint(0.0)):
        return True
    return event.capability == capability_of(SetHvacMode("heat")) and pending == "heat"


def on_knob_settled(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    event: KnobSettled,
    capability_of: _Capabilities,
    resync: Sequence[Intent] = (),
) -> _Step:
    """Adopt the knob turn whose debounce elapsed: the room follows the thermostat.

    A setpoint is clamped (a clamped value goes back to that thermostat too),
    becomes the room setpoint and is written to the other thermostats; a mode
    change heat <-> off is adopted the same way. A knob turn while the room is
    off turns it on again. During a hold only the wish is taken over.
    """
    if cfg.setpoint_range is None or event.trv not in state.trvs:
        return state, []
    trv, capability = event.trv, event.capability
    known = state.trvs[trv]
    pending = known.knob_pending.get(capability)
    if pending is None:
        return state, []
    state = _store(
        state,
        trv,
        replace(known, knob_pending=_dropped(known.knob_pending, capability)),
    )
    if device_driven(known) or capability in known.resync_pending:
        return state, []
    if capability == capability_of(SetSetpoint(0.0)) and isinstance(pending, float):
        return _adopt_setpoint(cfg, state, now, trv, pending, capability_of, resync)
    if capability == capability_of(SetHvacMode("heat")) and pending in OWN_MODES:
        mode = RoomMode(str(pending))
        return _adopt_mode(cfg, state, now, trv, mode, capability_of, resync)
    return state, []


def expire_window_phase(
    cfg: RoomConfig, state: RoomState, now: datetime, capability_of: _Capabilities
) -> _Step:
    """End the window phase of thermostats that stayed in it for 30 minutes.

    The room setpoint is then written to them (and ``heat`` first to one a hold
    left off), unless the room is off. During a hold they are switched off
    instead; the resume at its end brings them back.
    """
    if cfg.setpoint_range is None:
        return state, []
    trvs = dict(state.trvs)
    expired: list[str] = []
    for trv in cfg.trvs:
        known = trvs[trv.entity_id]
        if known.note is Note.WINDOW_PHASE and (
            known.note_since is None or now - known.note_since >= WINDOW_PHASE_LIMIT
        ):
            trvs[trv.entity_id] = replace(known, note=None, note_since=None)
            expired.append(trv.entity_id)
    if not expired:
        return state, []
    state = replace(state, trvs=trvs)
    writes: list[tuple[str, Intent]] = []
    for name in expired:
        if not trvs[name].available:
            continue
        if holding(state):
            writes.append((name, SetHvacMode("off")))
            continue
        goal = target(cfg, state)
        if suspended(state) or goal is None:
            continue
        # A hold may have switched it off while it showed its window phase.
        if trvs[name].reported_mode == "off":
            writes.append((name, SetHvacMode("heat")))
        writes.append((name, SetSetpoint(goal)))
    return write(state, now, writes, capability_of)


def resync_trv(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    trv: str,
    capability_of: _Capabilities,
    *,
    with_mode: bool = True,
) -> _Step:
    """Bring one thermostat that was out of reach up to the room's mode and setpoint.

    Used when it comes back and when it leaves its schedule. The thermostat is
    marked with the written values until the results of exactly those writes
    arrive: what it reports meanwhile may be the old value and must not be
    mistaken for a knob turn. A room that is off only gets ``off`` written; so
    does one on hold, whatever ``with_mode`` says (the hold's end resumes). A room
    that has neither a setpoint nor been switched off has no say yet: nothing.
    """
    held = holding(state)
    if cfg.setpoint_range is None or (
        not held and state.setpoint is None and state.mode is RoomMode.HEAT
    ):
        return state, []
    known = state.trvs[trv]
    if not known.available:
        return state, []
    # A hold switches off the thermostats that are left alone otherwise.
    if not held and (device_driven(known) or known.left_off):
        return state, []
    writes: list[tuple[str, Intent]] = []
    if with_mode or held:
        mode = SetHvacMode("off" if suspended(state) else "heat")
        writes.append((trv, mode))
    goal = target(cfg, state)
    if not suspended(state) and goal is not None:
        writes.append((trv, SetSetpoint(goal)))
    state, commands = write(state, now, writes, capability_of)
    written = {
        capability_of(command.intent): _value_of(command.intent)
        for command in commands
        if isinstance(command, Execute)
        and isinstance(command.intent, SetSetpoint | SetHvacMode)
    }
    if written:
        known = state.trvs[trv]
        known = replace(known, resync_pending={**known.resync_pending, **written})
        state = _store(state, trv, known)
    return state, commands


# --- Observations --------------------------------------------------------


def _observe_setpoint(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    event: TrvObserved,
    value: float,
    capability_of: _Capabilities,
) -> _Step:
    """Handle a reported setpoint: echo, window phase, schedule or knob turn.

    A knob turn is a report that differs from what that thermostat was last
    known to show (its previous report or the room's newest write, whichever is
    newer). The room's own setpoint is no yardstick: while the room writes
    nothing (off, hold, window phase) the thermostats keep their old value.
    """
    trv, capability = event.trv, event.capability
    known = state.trvs[trv]
    previous = shown(known, capability, known.reported_setpoint)
    echo = is_echo(known, capability, value, now)
    known = replace(
        known,
        reported_setpoint=value,
        reported_at={**known.reported_at, capability: now},
        foreign_at=_foreign(known, capability, now, echo=echo),
    )
    at_window_value = abs(value - WINDOW_PHASE_SETPOINT) <= SAME
    leaving_window = known.note is Note.WINDOW_PHASE and not at_window_value
    if leaving_window:
        known = replace(known, note=None, note_since=None)
    if echo:
        # A write the room made while the thermostat showed its window phase
        # does not exist (it is left alone), so an echo never ends that phase.
        return _store(state, trv, known), []
    goal = target(cfg, state)
    wants_window_value = goal is not None and (
        abs(goal - WINDOW_PHASE_SETPOINT) <= SAME
    )
    if (
        known.note is None
        and not on_schedule(known)
        and at_window_value
        and not wants_window_value
        and cfg.window_detection == "on"
    ):
        known = replace(known, note=Note.WINDOW_PHASE, note_since=now)
    if device_driven(known) or capability in known.resync_pending:
        known = replace(known, knob_pending=_dropped(known.knob_pending, capability))
        return _store(state, trv, known), []
    state = _store(state, trv, known)
    if leaving_window:
        return _leave_window_phase(cfg, state, now, trv, value, capability_of)
    if previous is None or _equal(previous, value):
        return state, []
    return _pend(state, trv, known, capability, value)


def _leave_window_phase(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    trv: str,
    value: float,
    capability_of: _Capabilities,
) -> _Step:
    """Write the room setpoint to a thermostat that left its window phase.

    The value it shows now is not the user's: it is what the device went back
    to, so it is not adopted.
    """
    goal = target(cfg, state)
    if goal is not None and abs(value - goal) <= SAME and not holding(state):
        return state, []
    return resync_trv(cfg, state, now, trv, capability_of, with_mode=False)


def _observe_mode(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    event: TrvObserved,
    capability_of: _Capabilities,
) -> _Step:
    """Handle a reported mode: schedule, return from it, echo or knob turn."""
    trv, capability = event.trv, event.capability
    mode: str | None = event.value if isinstance(event.value, str) else None
    if mode is None and event.raw == _AUTO_RAW:
        mode = _AUTO
    if mode is None:
        return state, []
    known = state.trvs[trv]
    was_on_schedule = on_schedule(known)
    previous = shown(known, capability, known.reported_mode)
    echo = is_echo(known, capability, mode, now)
    known = replace(
        known,
        reported_mode=mode,
        reported_at={**known.reported_at, capability: now},
        foreign_at=_foreign(known, capability, now, echo=echo),
    )
    if mode not in OWN_MODES:
        return _on_schedule_report(cfg, state, now, trv, known, capability_of)
    if known.note is Note.SCHEDULE:
        known = replace(known, note=None, note_since=None)
    if known.left_off and mode == "off":
        # The thermostat shows what the hold made of it; it stays that way.
        return _store(state, trv, known), []
    if known.left_off and not holding(state):
        # Switched back on by hand: it rejoins the room like one off its schedule.
        known = replace(known, left_off=False)
        was_on_schedule = True
    state = _store(state, trv, known)
    settled = echo or capability in known.resync_pending
    # Back from its schedule, or (held) a stray ``heat`` while the thermostats
    # are meant to be off: it is brought in line with the room.
    if was_on_schedule or (holding(state) and mode == "heat" and not settled):
        return resync_trv(cfg, state, now, trv, capability_of)
    if settled or holding(state) or previous is None or _equal(previous, mode):
        return state, []
    return _pend(state, trv, known, capability, mode)


def _on_schedule_report(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    trv: str,
    known: TrvState,
    capability_of: _Capabilities,
) -> _Step:
    """Mark a thermostat that reports its schedule and leave it alone.

    During a hold every thermostat is meant to be off: this one is switched off
    again and, having been on its schedule, stays off after the hold.
    """
    commands: list[Command] = []
    held = holding(state)
    if not held:
        # Back on its schedule: it is no longer one the hold left off.
        known = replace(known, left_off=False)
    if known.note is not Note.SCHEDULE:
        known = replace(known, note=Note.SCHEDULE, note_since=now)
        action = "switching it off again" if held else "leaving it alone"
        note = f"{cfg.name}: {trv} runs its own schedule, {action}"
        commands.append(Log("info", note))
    known = replace(known, knob_pending={})
    state = _store(state, trv, known)
    if not held:
        return state, commands
    state, written = resync_trv(cfg, state, now, trv, capability_of)
    return state, [*commands, *written]


def _foreign(
    known: TrvState, capability: str, now: datetime, *, echo: bool
) -> Mapping[str, datetime]:
    """Return ``foreign_at`` after a report: it moves for one that is no echo.

    A report while the resync guard of the capability holds says nothing: it may
    still be the value from before our write.
    """
    if echo or capability in known.resync_pending:
        return known.foreign_at
    return {**known.foreign_at, capability: now}


def _pend(
    state: RoomState,
    trv: str,
    known: TrvState,
    capability: str,
    value: float | str,
) -> _Step:
    """Remember a knob value and (re)arm the debounce for it."""
    known = replace(known, knob_pending={**known.knob_pending, capability: value})
    return _store(state, trv, known), [
        ScheduleKnobSettle(trv, capability, KNOB_DEBOUNCE)
    ]


def _adopt_setpoint(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    trv: str,
    value: float,
    capability_of: _Capabilities,
    resync: Sequence[Intent],
) -> _Step:
    """Make a thermostat's knob value the room setpoint.

    The value goes to every thermostat that is not known to show it already,
    the one that was turned included (it may have been clamped, or another knob
    was turned meanwhile): the last turn wins and the room converges.
    """
    if cfg.setpoint_range is None:  # pragma: no cover - checked by the caller
        return state, []
    adopted = clamp(value, cfg.setpoint_range)
    if lowered(cfg, state):
        settled = _settled_while_lowered(cfg, state, now, trv, adopted, capability_of)
        if settled is not None:
            return settled
    # Any other value while lowered or held by presence is an override until
    # presence is home; ending the away hold resumes the room.
    ends_away_hold = held_by_away_only(state)
    override = state.away_override or lowered(cfg, state) or ends_away_hold
    capability = capability_of(SetSetpoint(adopted))
    changed = state.setpoint is None or abs(adopted - state.setpoint) > SAME
    waking = state.mode is RoomMode.OFF and not holding(state)
    behind = [
        other
        for other in writable(cfg, state)
        if not _shows(
            state.trvs[other], capability, state.trvs[other].reported_setpoint, adopted
        )
    ]
    if not (changed or waking or (behind and not holding(state))):
        return state, []
    state = replace(
        state,
        setpoint=adopted,
        setpoint_source=f"device:{trv}",
        preset=Preset.NONE,
        frost_return=None,
        away_override=override,
        # A turn during a hold only changes the wish; the room stays as it was.
        mode=state.mode if holding(state) else RoomMode.HEAT,
    )
    if waking and away_acts(cfg, state):
        # A turn that switches the room on while away is an override as well.
        state = replace(state, away_override=True)
    event = _adopted(cfg, trv, adopted)
    if holding(state):
        return state, [event]
    if waking or ends_away_hold:
        state, commands = resume(cfg, state, now, capability_of, resync)
        return state, [*commands, event]
    writes = [(other, SetSetpoint(adopted)) for other in behind]
    state, commands = write(state, now, writes, capability_of)
    return state, [*commands, event]


def _settled_while_lowered(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    trv: str,
    value: float,
    capability_of: _Capabilities,
) -> _Step | None:
    """Handle a knob value that is the lowered target or the wish; else ``None``.

    The lowered target is no turn. The wish from before the room was lowered
    comes from a thermostat that has not taken the lowered value yet: it gets
    the target again.
    """
    goal = target(cfg, state)
    if goal is not None and abs(value - goal) <= SAME:
        return state, []
    if state.setpoint is not None and abs(value - state.setpoint) <= SAME:
        return resync_trv(cfg, state, now, trv, capability_of, with_mode=False)
    return None


def _adopt_mode(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    trv: str,
    mode: RoomMode,
    capability_of: _Capabilities,
    resync: Sequence[Intent],
) -> _Step:
    """Make a thermostat's mode change (heat <-> off) the room's mode."""
    if state.mode is mode or holding(state):
        return state, []
    state = replace(state, mode=mode, setpoint_source=f"device:{trv}")
    event = _adopted(cfg, trv, mode.value)
    # Switched on into the away hold, the room stays off like its thermostats.
    if mode is RoomMode.HEAT and not holding(state):
        state, commands = resume(cfg, state, now, capability_of, resync)
        return state, [*commands, event]
    off = SetHvacMode("off")
    capability = capability_of(off)
    writes = [
        (other, off)
        for other in writable(cfg, state)
        if not _shows(
            state.trvs[other], capability, state.trvs[other].reported_mode, "off"
        )
    ]
    state, commands = write(state, now, writes, capability_of)
    return state, [*commands, event]


def _adopted(cfg: RoomConfig, trv: str, value: float | str) -> FireEvent:
    """Return the bus event that announces a taken-over knob turn."""
    return FireEvent(
        EVENT_SETPOINT_ADOPTED, {"room": cfg.name, "trv": trv, "value": value}
    )


def shown(
    known: TrvState, capability: str, reported: float | str | None
) -> float | str | None:
    """Return the value a thermostat was last known to show, or ``None``.

    That is its newest report or the room's newest write to it, whichever is
    newer (a report without a time counts as older than any write, and a write
    made at the instant of a report counts as the later one).
    """
    # Written in order: of several writes at the same instant the last one counts.
    newest = max(
        reversed(known.written_recently.get(capability, ())),
        key=lambda entry: entry[1],
        default=None,
    )
    if newest is None:
        return reported
    stamp = known.reported_at.get(capability)
    if reported is None or stamp is None or newest[1] >= stamp:
        return newest[0]
    return reported


def _shows(
    known: TrvState,
    capability: str,
    reported: float | str | None,
    value: float | str,
) -> bool:
    """Return whether a thermostat is known to show ``value`` already."""
    current = shown(known, capability, reported)
    return current is not None and _equal(current, value)


def _value_of(intent: SetSetpoint | SetHvacMode) -> float | str:
    """Return the value a setpoint or mode write carries."""
    return intent.celsius if isinstance(intent, SetSetpoint) else intent.mode


def release_resync(
    known: TrvState, capability: str, intent: SetSetpoint | SetHvacMode
) -> TrvState:
    """End the resync guard of ``capability`` if ``intent`` is the write it waits for.

    The result of an older write of the same capability (still in flight when
    the resync was issued) leaves the guard in place.
    """
    waiting = known.resync_pending.get(capability)
    if waiting is not None and _equal(waiting, _value_of(intent)):
        return replace(known, resync_pending=_dropped(known.resync_pending, capability))
    return known


def _equal(left: float | str, right: float | str) -> bool:
    """Return whether two reported values are the same one."""
    if isinstance(left, str) or isinstance(right, str):
        return left == right
    return abs(left - right) <= SAME


def _store(state: RoomState, trv: str, known: TrvState) -> RoomState:
    """Return ``state`` with the state of one thermostat replaced."""
    return replace(state, trvs={**state.trvs, trv: known})


def _dropped[V](mapping: Mapping[str, V], key: str) -> dict[str, V]:
    """Return a copy of ``mapping`` without ``key``."""
    return {k: v for k, v in mapping.items() if k != key}
