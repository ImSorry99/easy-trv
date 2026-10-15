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
    clamp,
    device_driven,
    holding,
    on_schedule,
    resume,
    suspended,
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
    """Return whether ``value`` is one the room wrote in the last ``ECHO_WINDOW``."""
    if value is None:
        return False
    return any(
        _equal(written, value) and now - at <= ECHO_WINDOW
        for written, at in known.written_recently.get(capability, ())
    )


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
    if (
        cfg.setpoint_range is None
        or event.trv not in state.trvs
        or state.mode is not RoomMode.OFF
        or holding(state)
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

    The room setpoint is then written to them, unless the room is off or held
    (the resume writes it).
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
    if suspended(state) or state.setpoint is None:
        return state, []
    intent = SetSetpoint(state.setpoint)
    writes = [(trv, intent) for trv in expired if trvs[trv].available]
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
    if not known.available or device_driven(known):
        return state, []
    writes: list[tuple[str, Intent]] = []
    if with_mode or held:
        mode = SetHvacMode("off" if suspended(state) else "heat")
        writes.append((trv, mode))
    if not suspended(state) and state.setpoint is not None:
        writes.append((trv, SetSetpoint(state.setpoint)))
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
    previous = _known(known, capability, known.reported_setpoint)
    echo = is_echo(known, capability, value, now)
    known = replace(
        known,
        reported_setpoint=value,
        reported_at={**known.reported_at, capability: now},
    )
    at_window_value = abs(value - WINDOW_PHASE_SETPOINT) <= SAME
    leaving_window = known.note is Note.WINDOW_PHASE and not at_window_value
    if leaving_window:
        known = replace(known, note=None, note_since=None)
    if echo:
        state = _store(state, trv, known)
        guarded = device_driven(known) or capability in known.resync_pending
        if leaving_window and not guarded:
            return _leave_window_phase(cfg, state, now, trv, value, capability_of)
        return state, []
    wants_window_value = state.setpoint is not None and (
        abs(state.setpoint - WINDOW_PHASE_SETPOINT) <= SAME
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
    if (
        state.setpoint is not None
        and abs(value - state.setpoint) <= SAME
        and not holding(state)
    ):
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
    previous = _known(known, capability, known.reported_mode)
    echo = is_echo(known, capability, mode, now)
    known = replace(
        known, reported_mode=mode, reported_at={**known.reported_at, capability: now}
    )
    if mode not in OWN_MODES:
        commands: list[Command] = []
        if known.note is not Note.SCHEDULE:
            known = replace(known, note=Note.SCHEDULE, note_since=now)
            note = f"{cfg.name}: {trv} runs its own schedule, leaving it alone"
            commands.append(Log("info", note))
        known = replace(known, knob_pending={})
        return _store(state, trv, known), commands
    if known.note is Note.SCHEDULE:
        known = replace(known, note=None, note_since=None)
    state = _store(state, trv, known)
    if was_on_schedule:
        return resync_trv(cfg, state, now, trv, capability_of)
    settled = echo or capability in known.resync_pending
    if holding(state) and mode == "heat" and not settled:
        # The thermostats are meant to be off: a stray ``heat`` is put right.
        return resync_trv(cfg, state, now, trv, capability_of)
    if settled or holding(state) or previous is None or _equal(previous, mode):
        return state, []
    return _pend(state, trv, known, capability, mode)


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
        # A turn during a hold only changes the wish; the room stays as it was.
        mode=state.mode if holding(state) else RoomMode.HEAT,
    )
    event = _adopted(cfg, trv, adopted)
    if holding(state):
        return state, [event]
    if waking:
        state, commands = resume(cfg, state, now, capability_of, resync)
        return state, [*commands, event]
    writes = [(other, SetSetpoint(adopted)) for other in behind]
    state, commands = write(state, now, writes, capability_of)
    return state, [*commands, event]


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
    if mode is RoomMode.HEAT:
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


def _known(
    known: TrvState, capability: str, reported: float | str | None
) -> float | str | None:
    """Return the value a thermostat was last known to show, or ``None``.

    That is its newest report or the room's newest write to it, whichever is
    newer (a report without a time counts as older than any write).
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
    if reported is None or stamp is None or newest[1] > stamp:
        return newest[0]
    return reported


def _shows(
    known: TrvState,
    capability: str,
    reported: float | str | None,
    value: float | str,
) -> bool:
    """Return whether a thermostat is known to show ``value`` already."""
    shown = _known(known, capability, reported)
    return shown is not None and _equal(shown, value)


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
