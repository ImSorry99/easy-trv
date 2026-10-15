"""Setpoint, mode and preset of a room: what the room owns and writes.

The functions are pure. Each takes the room configuration, the current state and
time and returns the next state with the commands to run, in the shape of
``RoomLogic.handle``. A room whose configuration has no ``setpoint_range`` (its
thermostats have no climate capabilities) behaves as before the room thermostat
existed: every function leaves it untouched and issues nothing.
"""

import math
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
    RestoreRoom,
    RoomConfig,
    RoomMode,
    RoomState,
    ScheduleKnobSettle,
    SetHvacMode,
    SetSetpoint,
    TrvObserved,
    TrvState,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from datetime import datetime

type _Step = tuple[RoomState, list[Command]]
type _Capabilities = Callable[[Intent], str]

# How long a write stays in ``written_recently``; older entries are pruned.
ECHO_WINDOW = timedelta(seconds=120)
# Two temperatures closer than this are the same one.
_SAME = 1e-6
# Reported system modes the room writes over; ``auto`` is the thermostat's schedule.
_OWN_MODES = ("heat", "off")
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


def clamp(value: float, rng: tuple[float, float, float]) -> float:
    """Round ``value`` to the step (half up) and limit it to the range."""
    low, high, step = rng
    if step > 0:
        value = round(math.floor(value / step + 0.5) * step, 6)
    return min(max(value, low), high)


def holding(state: RoomState) -> bool:
    """Return whether a window or outdoor hold keeps the thermostats off."""
    return state.window_open or state.outdoor_hold


def suspended(state: RoomState) -> bool:
    """Return whether the room writes nothing to the thermostats.

    That is the case while it is switched off and while a hold is active.
    """
    return state.mode is RoomMode.OFF or holding(state)


def on_set_setpoint(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    celsius: float,
    capability_of: _Capabilities,
) -> _Step:
    """Take a setpoint from Home Assistant and write it to the thermostats."""
    if cfg.setpoint_range is None:
        return state, []
    state = replace(
        state,
        setpoint=clamp(celsius, cfg.setpoint_range),
        setpoint_source="ha",
        preset=Preset.NONE,
        frost_return=None,
    )
    return _write_setpoint(cfg, state, now, capability_of)


def on_set_mode(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    mode: RoomMode,
    capability_of: _Capabilities,
    resync: Sequence[Intent] = (),
) -> _Step:
    """Switch the room on or off.

    ``off`` is written to every thermostat. ``heat`` writes the mode, then the
    ``resync`` intents (source and value, which the room logic knows), then the
    setpoint, thermostat by thermostat. During a hold only the state changes.
    """
    if cfg.setpoint_range is None:
        return state, []
    state = replace(state, mode=mode)
    if holding(state):
        return state, []
    if mode is RoomMode.HEAT:
        return resume(cfg, state, now, capability_of, resync)
    writes = [(trv, SetHvacMode("off")) for trv in _writable(cfg, state)]
    return _write(state, now, writes, capability_of)


def resume(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    capability_of: _Capabilities,
    resync: Sequence[Intent] = (),
) -> _Step:
    """Bring every thermostat up to date when the room starts heating again.

    Per writable thermostat: mode ``heat``, the ``resync`` intents (source and
    value, which the room logic knows), then the setpoint. Used whenever the
    room leaves ``off`` or a hold.
    """
    writes: list[tuple[str, Intent]] = []
    for trv in _writable(cfg, state):
        writes.append((trv, SetHvacMode("heat")))
        writes.extend((trv, intent) for intent in resync)
        if state.setpoint is not None:
            writes.append((trv, SetSetpoint(state.setpoint)))
    return _write(state, now, writes, capability_of)


def leaving_off(cfg: RoomConfig, state: RoomState, mode: RoomMode | None) -> bool:
    """Return whether setting ``mode`` takes a room that is off back to heating."""
    return (
        cfg.setpoint_range is not None
        and state.mode is RoomMode.OFF
        and mode is RoomMode.HEAT
        and not holding(state)
    )


def current(state: RoomState, intent: Intent) -> Intent | None:
    """Return what a retry of a setpoint or mode write should send now.

    Never an older value than the room's; ``None`` when the room has none.
    """
    if isinstance(intent, SetSetpoint):
        return None if state.setpoint is None else SetSetpoint(state.setpoint)
    if isinstance(intent, SetHvacMode):
        return SetHvacMode("off" if state.mode is RoomMode.OFF else "heat")
    return intent


def retry(
    state: RoomState,
    now: datetime,
    trv: str,
    intent: Intent,
    capability_of: _Capabilities,
) -> _Step:
    """Resend a setpoint or mode write whose retry is due, with the newest value.

    A setpoint is not resent while the room is off or on hold (the resume writes
    it); a mode is not resent during a hold, but ``off`` is while the room is off.
    """
    if holding(state) or (isinstance(intent, SetSetpoint) and suspended(state)):
        return state, []
    newest = current(state, intent)
    if newest is None:
        return state, []
    return _write(state, now, [(trv, newest)], capability_of)


def on_set_preset(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    preset: Preset,
    capability_of: _Capabilities,
) -> _Step:
    """Switch the frost preset on (remembering the setpoint) or off (restoring it)."""
    if cfg.setpoint_range is None or preset is state.preset:
        return state, []
    if preset is Preset.FROST:
        state = replace(
            state,
            preset=Preset.FROST,
            frost_return=state.setpoint,
            setpoint=clamp(cfg.frost_temperature, cfg.setpoint_range),
            setpoint_source="frost",
        )
    else:
        state = replace(
            state,
            preset=Preset.NONE,
            setpoint=state.setpoint
            if state.frost_return is None
            else state.frost_return,
            frost_return=None,
            setpoint_source="ha",
        )
    return _write_setpoint(cfg, state, now, capability_of)


def on_restore(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    restore: RestoreRoom,
    capability_of: _Capabilities,
    resync: Sequence[Intent] = (),
) -> _Step:
    """Take the room state remembered across a restart; it wins over the devices.

    Only thermostats known to report something else get a write. Thermostats
    that have not reported yet are handled by ``reconcile_start``. A room that
    was off and is restored to heat resumes every thermostat instead.
    """
    if cfg.setpoint_range is None:
        return state, []
    if restore.setpoint is not None:
        state = replace(
            state,
            setpoint=clamp(restore.setpoint, cfg.setpoint_range),
            setpoint_source="restore",
        )
    leaving = leaving_off(cfg, state, restore.mode)
    if restore.mode is not None:
        state = replace(state, mode=restore.mode)
    if restore.preset is not None:
        state = replace(state, preset=restore.preset)
    if restore.frost_return is not None:
        state = replace(state, frost_return=restore.frost_return)
    if leaving:
        return resume(cfg, state, now, capability_of, resync)
    return _sync_differing(cfg, state, now, capability_of, with_mode=True)


def reconcile_start(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    reported: Mapping[str, tuple[float | None, str | None]],
    capability_of: _Capabilities,
) -> _Step:
    """Compare what the thermostats report at start with the room.

    A room that was restored keeps its values and writes them to thermostats
    that report something else. Otherwise the room adopts the setpoint of the
    first writable thermostat that reports one (configuration order) and goes
    ``off`` only when every reported mode is ``off``; the other thermostats are
    then brought to the adopted setpoint.
    """
    if cfg.setpoint_range is None:
        return state, []
    trvs = dict(state.trvs)
    for trv, (setpoint, mode) in reported.items():
        if trv in trvs:
            trvs[trv] = replace(
                trvs[trv], reported_setpoint=setpoint, reported_mode=mode
            )
    state = replace(state, trvs=trvs)
    if state.setpoint is not None:
        return _sync_differing(cfg, state, now, capability_of, with_mode=True)
    writable = _writable(cfg, state)
    # A thermostat on its schedule (or with no mode) shows a setpoint that is
    # not the user's.
    first = next(
        (
            t
            for t in writable
            if state.trvs[t].reported_setpoint is not None
            and state.trvs[t].reported_mode in _OWN_MODES
        ),
        None,
    )
    modes = [state.trvs[t].reported_mode for t in writable]
    reported_modes = [m for m in modes if m is not None]
    if reported_modes and all(m == "off" for m in reported_modes):
        state = replace(state, mode=RoomMode.OFF)
    if first is None:
        return state, []
    adopted = state.trvs[first].reported_setpoint
    if adopted is None:  # pragma: no cover - ``first`` has a setpoint
        return state, []
    state = replace(
        state,
        setpoint=clamp(adopted, cfg.setpoint_range),
        setpoint_source="initial",
    )
    return _sync_differing(cfg, state, now, capability_of, with_mode=False)


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
            return _observe_setpoint(cfg, state, now, event, float(value))
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
        or _device_driven(known)
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
    if _device_driven(known) or capability in known.resync_pending:
        return state, []
    if capability == capability_of(SetSetpoint(0.0)) and isinstance(pending, float):
        return _adopt_setpoint(cfg, state, now, trv, pending, capability_of, resync)
    if capability == capability_of(SetHvacMode("heat")) and pending in _OWN_MODES:
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
    return _write(state, now, writes, capability_of)


def resync_trv(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    trv: str,
    capability_of: _Capabilities,
) -> _Step:
    """Bring one thermostat that was out of reach up to the room's mode and setpoint.

    Used when it comes back and when it leaves its schedule. The thermostat is
    marked until the results arrive: what it reports meanwhile may be the old
    value and must not be mistaken for a knob turn. A room that is off only gets
    ``off`` written; during a hold nothing (the hold's end resumes), and nothing
    while the room has neither a setpoint nor been switched off (it has no say yet).
    """
    if (
        cfg.setpoint_range is None
        or holding(state)
        or (state.setpoint is None and state.mode is RoomMode.HEAT)
    ):
        return state, []
    known = state.trvs[trv]
    if not known.available or _device_driven(known):
        return state, []
    writes: list[tuple[str, Intent]] = [
        (trv, SetHvacMode("off" if state.mode is RoomMode.OFF else "heat"))
    ]
    if not suspended(state) and state.setpoint is not None:
        writes.append((trv, SetSetpoint(state.setpoint)))
    state, commands = _write(state, now, writes, capability_of)
    written = frozenset(
        capability_of(command.intent)
        for command in commands
        if isinstance(command, Execute)
    )
    if written:
        known = state.trvs[trv]
        known = replace(known, resync_pending=known.resync_pending | written)
        state = _store(state, trv, known)
    return state, commands


# --- Observations --------------------------------------------------------


def _observe_setpoint(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    event: TrvObserved,
    value: float,
) -> _Step:
    """Handle a reported setpoint: echo, window phase, schedule or knob turn."""
    trv, capability = event.trv, event.capability
    known = state.trvs[trv]
    echo = is_echo(known, capability, value, now)
    known = replace(known, reported_setpoint=value)
    at_window_value = abs(value - WINDOW_PHASE_SETPOINT) <= _SAME
    if known.note is Note.WINDOW_PHASE and not at_window_value:
        known = replace(known, note=None, note_since=None)
    if echo:
        return _store(state, trv, known), []
    wants_window_value = state.setpoint is not None and (
        abs(state.setpoint - WINDOW_PHASE_SETPOINT) <= _SAME
    )
    if (
        known.note is None
        and not _on_schedule(known)
        and at_window_value
        and not wants_window_value
        and cfg.window_detection == "on"
    ):
        known = replace(known, note=Note.WINDOW_PHASE, note_since=now)
    if _device_driven(known) or capability in known.resync_pending:
        known = replace(known, knob_pending=_dropped(known.knob_pending, capability))
        return _store(state, trv, known), []
    if state.setpoint is not None and abs(value - state.setpoint) <= _SAME:
        known = replace(known, knob_pending=_dropped(known.knob_pending, capability))
        return _store(state, trv, known), []
    return _pend(state, trv, known, capability, value)


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
    was_on_schedule = _on_schedule(known)
    echo = is_echo(known, capability, mode, now)
    known = replace(known, reported_mode=mode)
    if mode not in _OWN_MODES:
        commands: list[Command] = []
        if known.note is not Note.SCHEDULE:
            known = replace(known, note=Note.SCHEDULE, note_since=now)
            commands.append(
                Log(
                    "info", f"{cfg.name}: {trv} runs its own schedule, leaving it alone"
                )
            )
        known = replace(known, knob_pending={})
        return _store(state, trv, known), commands
    if known.note is Note.SCHEDULE:
        known = replace(known, note=None, note_since=None)
    state = _store(state, trv, known)
    if was_on_schedule:
        return resync_trv(cfg, state, now, trv, capability_of)
    if echo or capability in known.resync_pending or holding(state):
        return state, []
    room_mode = "off" if state.mode is RoomMode.OFF else "heat"
    if mode == room_mode:
        known = replace(known, knob_pending=_dropped(known.knob_pending, capability))
        return _store(state, trv, known), []
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
    """Make a thermostat's knob value the room setpoint."""
    if cfg.setpoint_range is None:  # pragma: no cover - checked by the caller
        return state, []
    adopted = clamp(value, cfg.setpoint_range)
    changed = state.setpoint is None or abs(adopted - state.setpoint) > _SAME
    clamped = abs(adopted - value) > _SAME
    waking = state.mode is RoomMode.OFF
    state = replace(
        state,
        setpoint=adopted,
        setpoint_source=f"device:{trv}",
        preset=Preset.NONE,
        frost_return=None,
        mode=RoomMode.HEAT,
    )
    event = _adopted(cfg, trv, adopted)
    if holding(state):
        return state, [event]
    if waking:
        state, commands = resume(cfg, state, now, capability_of, resync)
        return state, [*commands, event]
    intent = SetSetpoint(adopted)
    writes = [
        (other, intent)
        for other in _writable(cfg, state)
        if (other == trv and clamped) or (other != trv and changed)
    ]
    state, commands = _write(state, now, writes, capability_of)
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
    writes = [
        (other, SetHvacMode("off")) for other in _writable(cfg, state) if other != trv
    ]
    state, commands = _write(state, now, writes, capability_of)
    return state, [*commands, event]


def _adopted(cfg: RoomConfig, trv: str, value: float | str) -> FireEvent:
    """Return the bus event that announces a taken-over knob turn."""
    return FireEvent(
        EVENT_SETPOINT_ADOPTED, {"room": cfg.name, "trv": trv, "value": value}
    )


def _equal(left: float | str, right: float | str) -> bool:
    """Return whether two reported values are the same one."""
    if isinstance(left, str) or isinstance(right, str):
        return left == right
    return abs(left - right) <= _SAME


def _store(state: RoomState, trv: str, known: TrvState) -> RoomState:
    """Return ``state`` with the state of one thermostat replaced."""
    return replace(state, trvs={**state.trvs, trv: known})


def _dropped[V](mapping: Mapping[str, V], key: str) -> dict[str, V]:
    """Return a copy of ``mapping`` without ``key``."""
    return {k: v for k, v in mapping.items() if k != key}


# --- Helpers -------------------------------------------------------------


def _on_schedule(known: TrvState) -> bool:
    """Return whether a thermostat runs its own schedule."""
    return known.note is Note.SCHEDULE or (
        known.reported_mode is not None and known.reported_mode not in _OWN_MODES
    )


def _device_driven(known: TrvState) -> bool:
    """Return whether a thermostat shows a value of its own and is left alone.

    That is its schedule and its open-window phase: nothing is written to it and
    nothing it reports is adopted.
    """
    return known.note is not None or _on_schedule(known)


def _writable(cfg: RoomConfig, state: RoomState) -> list[str]:
    """Return the available thermostats in configuration order."""
    return [trv.entity_id for trv in cfg.trvs if state.trvs[trv.entity_id].available]


def _write_setpoint(
    cfg: RoomConfig, state: RoomState, now: datetime, capability_of: _Capabilities
) -> _Step:
    """Write the room setpoint to every thermostat, unless writes are suspended."""
    if state.setpoint is None or suspended(state):
        return state, []
    intent = SetSetpoint(state.setpoint)
    writes = [(trv, intent) for trv in _writable(cfg, state)]
    return _write(state, now, writes, capability_of)


def _sync_differing(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    capability_of: _Capabilities,
    *,
    with_mode: bool,
) -> _Step:
    """Write the room values to thermostats known to report something else.

    A thermostat that runs its schedule or shows its window phase is left alone.
    """
    writes: list[tuple[str, Intent]] = []
    mode: SetHvacMode = SetHvacMode("off" if state.mode is RoomMode.OFF else "heat")
    for trv in _writable(cfg, state):
        known = state.trvs[trv]
        if _device_driven(known):
            continue
        if (
            with_mode
            and not holding(state)
            and known.reported_mode is not None
            and known.reported_mode != mode.mode
        ):
            writes.append((trv, mode))
        if (
            state.setpoint is not None
            and not suspended(state)
            and known.reported_setpoint is not None
            and abs(known.reported_setpoint - state.setpoint) > _SAME
        ):
            writes.append((trv, SetSetpoint(state.setpoint)))
    return _write(state, now, writes, capability_of)


def _write(
    state: RoomState,
    now: datetime,
    writes: Iterable[tuple[str, Intent]],
    capability_of: _Capabilities,
) -> _Step:
    """Issue the writes in order and remember setpoint and mode as recent writes.

    A thermostat on its own schedule or in its window phase gets no setpoint or
    mode write, and a capability it lacks is not written at all.
    """
    trvs = dict(state.trvs)
    commands: list[Command] = []
    for trv, intent in writes:
        capability = capability_of(intent)
        known = trvs[trv]
        if capability in known.unsupported:
            continue
        if isinstance(intent, SetSetpoint | SetHvacMode):
            if _device_driven(known):
                continue
            value = intent.celsius if isinstance(intent, SetSetpoint) else intent.mode
            trvs[trv] = _remember(known, capability, value, now)
        commands.append(Execute(trv, intent))
    return replace(state, trvs=trvs), commands


def _remember(
    known: TrvState, capability: str, value: float | str, now: datetime
) -> TrvState:
    """Record a write, dropping entries older than ``ECHO_WINDOW``."""
    recent = tuple(
        entry
        for entry in known.written_recently.get(capability, ())
        if now - entry[1] <= ECHO_WINDOW
    )
    return replace(
        known,
        written_recently={
            **known.written_recently,
            capability: (*recent, (value, now)),
        },
    )
