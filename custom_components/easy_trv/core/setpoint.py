"""Setpoint, mode and operation of a room: what the room owns and writes.

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
    Command,
    Execute,
    Intent,
    Note,
    OperatingMode,
    Preset,
    RestoreRoom,
    RoomConfig,
    RoomMode,
    RoomState,
    SetHvacMode,
    SetSetpoint,
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
SAME = 1e-6
# Reported system modes the room writes over; ``auto`` is the thermostat's schedule.
OWN_MODES = ("heat", "off")


def clamp(value: float, rng: tuple[float, float, float]) -> float:
    """Round ``value`` to the step (half up) and limit it to the range."""
    low, high, step = rng
    if step > 0:
        value = round(math.floor(value / step + 0.5) * step, 6)
    return min(max(value, low), high)


def away_acts(cfg: RoomConfig, state: RoomState) -> bool:
    """Return whether presence acts on the room now.

    The room has a presence entity, it reads away, nobody overrode it, and the
    room heats (operating mode heat).
    """
    return (
        cfg.away_action is not None
        and state.away
        and not state.away_override
        and operation(state) is OperatingMode.HEAT
    )


def lowered(cfg: RoomConfig, state: RoomState) -> bool:
    """Return whether the room is lowered because nobody is home."""
    return away_acts(cfg, state) and cfg.away_action == "setback"


def target(cfg: RoomConfig, state: RoomState) -> float | None:
    """Return the value the thermostats should show: the wish, unless lowered.

    Lowered is the wish minus the setback, never below the frost protection
    temperature (a wish below it stays) and rounded and clamped to the range.
    """
    wish = state.setpoint
    if wish is None or cfg.setpoint_range is None or not lowered(cfg, state):
        return wish
    floor = min(cfg.frost_temperature, wish)
    return clamp(max(wish - cfg.away_setback, floor), cfg.setpoint_range)


def away_held(state: RoomState) -> bool:
    """Return whether presence keeps the thermostats off (``away_action`` off).

    Only while the room heats and nobody overrode it.
    """
    return (
        state.away_off
        and not state.away_override
        and operation(state) is OperatingMode.HEAT
    )


def held_by_away_only(state: RoomState) -> bool:
    """Return whether presence is the only hold that keeps the room off."""
    return away_held(state) and not (state.window_open or state.outdoor_hold)


def holding(state: RoomState) -> bool:
    """Return whether a window, outdoor or away hold keeps the thermostats off."""
    return state.window_open or state.outdoor_hold or away_held(state)


def suspended(state: RoomState) -> bool:
    """Return whether the room writes nothing to the thermostats.

    That is the case while it is switched off and while a hold is active.
    """
    return state.mode is RoomMode.OFF or holding(state)


def on_set_setpoint(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    celsius: float,
    capability_of: _Capabilities,
    resync: Sequence[Intent] = (),
) -> _Step:
    """Take a setpoint from Home Assistant and write it to the thermostats.

    While presence acts on the room, the new setpoint is an override: the room
    heats to it until presence reads home. An override that ends the away hold
    resumes the thermostats, writing the ``resync`` intents too.
    """
    ends_away_hold = held_by_away_only(state)
    if cfg.setpoint_range is None:
        return state, []
    state = _forget_knobs(state, capability_of(SetSetpoint(celsius)))
    state = replace(
        state,
        setpoint=clamp(celsius, cfg.setpoint_range),
        setpoint_source="ha",
        preset=Preset.NONE,
        frost_return=None,
        away_override=state.away_override or away_acts(cfg, state),
    )
    if ends_away_hold:
        return resume(cfg, state, now, capability_of, resync)
    return write_target(cfg, state, now, capability_of)


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
    setpoint, thermostat by thermostat. During a hold only the state changes, and
    ``heat`` for a room that heats already writes nothing (it only drops the knob
    turns still waiting for their debounce).
    """
    if cfg.setpoint_range is None:
        return state, []
    state = _forget_knobs(state, capability_of(SetHvacMode("heat")))
    if mode is RoomMode.OFF:
        # A setpoint turned just before must not wake the room when it settles.
        state = _forget_knobs(state, capability_of(SetSetpoint(0.0)))
        # Off ends frost protection: switched on again, the room heats as before.
        state = _with_frost(cfg, state, frost=False)
    heating = state.mode is RoomMode.HEAT
    state = replace(state, mode=mode)
    if holding(state) or (heating and mode is RoomMode.HEAT):
        return state, []
    if mode is RoomMode.HEAT:
        return resume(cfg, state, now, capability_of, resync)
    writes = [(trv, SetHvacMode("off")) for trv in writable(cfg, state)]
    return write(state, now, writes, capability_of)


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
    room leaves ``off`` or a hold. A thermostat a hold switched off while it ran
    its schedule stays off (``left_off``).
    """
    writes: list[tuple[str, Intent]] = []
    for trv in writable(cfg, state):
        if state.trvs[trv].left_off:
            continue
        writes.append((trv, SetHvacMode("heat")))
        writes.extend((trv, intent) for intent in resync)
        goal = target(cfg, state)
        if goal is not None:
            writes.append((trv, SetSetpoint(goal)))
    return write(state, now, writes, capability_of)


def leaving_off(cfg: RoomConfig, state: RoomState, mode: RoomMode | None) -> bool:
    """Return whether setting ``mode`` takes a room that is off back to heating."""
    return (
        cfg.setpoint_range is not None
        and state.mode is RoomMode.OFF
        and mode is RoomMode.HEAT
        and not holding(state)
    )


def current(cfg: RoomConfig, state: RoomState, intent: Intent) -> Intent | None:
    """Return what a retry of a setpoint or mode write should send now.

    Never an older value than the room's target; ``None`` when the room has
    none. During a hold every thermostat is meant to be off, whatever mode the
    room wants.
    """
    if isinstance(intent, SetSetpoint):
        goal = target(cfg, state)
        return None if goal is None else SetSetpoint(goal)
    if isinstance(intent, SetHvacMode):
        return SetHvacMode("off" if suspended(state) else "heat")
    return intent


def retry(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    trv: str,
    intent: Intent,
    capability_of: _Capabilities,
) -> _Step:
    """Resend a setpoint or mode write whose retry is due, with the newest value.

    A setpoint is not resent while the room is off or on hold (the resume writes
    it). A mode is: ``off`` while the room is off or on hold.
    """
    if isinstance(intent, SetSetpoint) and suspended(state):
        return state, []
    newest = current(cfg, state, intent)
    if newest is None:
        return state, []
    return write(state, now, [(trv, newest)], capability_of)


def operation(state: RoomState) -> OperatingMode:
    """Return the operating mode of the room: off, frost protection or heat."""
    if state.mode is RoomMode.OFF:
        return OperatingMode.OFF
    return OperatingMode.FROST if state.preset is Preset.FROST else OperatingMode.HEAT


def _with_frost(cfg: RoomConfig, state: RoomState, *, frost: bool) -> RoomState:
    """Start frost protection (remembering the setpoint) or end it (restoring it).

    Only the state changes; the caller writes.
    """
    if frost is (state.preset is Preset.FROST) or cfg.setpoint_range is None:
        return state
    if frost:
        return replace(
            state,
            preset=Preset.FROST,
            frost_return=state.setpoint,
            setpoint=clamp(cfg.frost_temperature, cfg.setpoint_range),
            setpoint_source="frost",
        )
    return replace(
        state,
        preset=Preset.NONE,
        setpoint=state.setpoint if state.frost_return is None else state.frost_return,
        frost_return=None,
        setpoint_source="ha",
    )


def on_set_operation(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    selected: OperatingMode,
    capability_of: _Capabilities,
    resync: Sequence[Intent] = (),
) -> _Step:
    """Switch the room off, to frost protection only, or to heating.

    ``off`` is the room mode ``off`` (which ends frost protection). From ``off``,
    ``frost`` and ``heat`` switch the room on with their setpoint; the
    ``resync`` intents are written as for ``on_set_mode``. On a heating room
    they only write the setpoint, and nothing if the operation does not change.
    """
    if cfg.setpoint_range is None:
        return state, []
    if selected is OperatingMode.OFF:
        return on_set_mode(cfg, state, now, RoomMode.OFF, capability_of)
    frost = selected is OperatingMode.FROST
    if state.mode is RoomMode.OFF:
        state = _with_frost(cfg, state, frost=frost)
        return on_set_mode(cfg, state, now, RoomMode.HEAT, capability_of, resync)
    if frost is (state.preset is Preset.FROST):
        return state, []
    state = _forget_knobs(state, capability_of(SetSetpoint(0.0)))
    state = _with_frost(cfg, state, frost=frost)
    return write_target(cfg, state, now, capability_of)


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
    state = _forget_knobs(state, capability_of(SetSetpoint(0.0)))
    state = _forget_knobs(state, capability_of(SetHvacMode("heat")))
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
    if state.mode is RoomMode.OFF:
        # A room remembered off with frost protection (0.3.0 allowed that) is off.
        state = _with_frost(cfg, state, frost=False)
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
    stamps = {
        capability_of(SetSetpoint(0.0)): now,
        capability_of(SetHvacMode("heat")): now,
    }
    for trv, (setpoint, mode) in reported.items():
        if trv in trvs:
            trvs[trv] = replace(
                trvs[trv],
                reported_setpoint=setpoint,
                reported_mode=mode,
                reported_at=stamps,
            )
    state = replace(state, trvs=trvs)
    if state.setpoint is not None:
        return _sync_differing(cfg, state, now, capability_of, with_mode=True)
    available = writable(cfg, state)
    # A thermostat on its schedule (or with no mode) shows a setpoint that is
    # not the user's.
    first = next(
        (
            t
            for t in available
            if state.trvs[t].reported_setpoint is not None
            and state.trvs[t].reported_mode in OWN_MODES
        ),
        None,
    )
    modes = [state.trvs[t].reported_mode for t in available]
    reported_modes = [m for m in modes if m is not None]
    if reported_modes and all(m == "off" for m in reported_modes):
        state = replace(state, mode=RoomMode.OFF)
    # Thermostats that report off in a room that comes up heating get heat, and
    # a held room still brings heating thermostats to off.
    with_mode = True
    if first is None:
        return _sync_differing(cfg, state, now, capability_of, with_mode=with_mode)
    adopted = state.trvs[first].reported_setpoint
    if adopted is None:  # pragma: no cover - ``first`` has a setpoint
        return state, []
    state = replace(
        state,
        setpoint=clamp(adopted, cfg.setpoint_range),
        setpoint_source="initial",
    )
    return _sync_differing(cfg, state, now, capability_of, with_mode=with_mode)


# --- Helpers -------------------------------------------------------------


def _forget_knobs(state: RoomState, capability: str) -> RoomState:
    """Drop the knob values still waiting for their debounce.

    The room itself just set that capability (Home Assistant or a restore); a
    turn made before it must not overwrite it when its debounce elapses.
    """
    trvs = {
        trv: replace(
            known,
            knob_pending={
                k: v for k, v in known.knob_pending.items() if k != capability
            },
        )
        for trv, known in state.trvs.items()
    }
    return replace(state, trvs=trvs)


def on_schedule(known: TrvState) -> bool:
    """Return whether a thermostat runs its own schedule."""
    return known.note is Note.SCHEDULE or (
        known.reported_mode is not None and known.reported_mode not in OWN_MODES
    )


def device_driven(known: TrvState) -> bool:
    """Return whether a thermostat shows a value of its own and is left alone.

    That is its schedule and its open-window phase: nothing is written to it and
    nothing it reports is adopted.
    """
    return known.note is not None or on_schedule(known)


def writable(cfg: RoomConfig, state: RoomState) -> list[str]:
    """Return the available thermostats in configuration order."""
    return [trv.entity_id for trv in cfg.trvs if state.trvs[trv.entity_id].available]


def write_target(
    cfg: RoomConfig, state: RoomState, now: datetime, capability_of: _Capabilities
) -> _Step:
    """Write the room's target to every thermostat, unless writes are suspended."""
    goal = target(cfg, state)
    if goal is None or suspended(state):
        return state, []
    intent = SetSetpoint(goal)
    writes = [(trv, intent) for trv in writable(cfg, state)]
    return write(state, now, writes, capability_of)


def _sync_differing(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    capability_of: _Capabilities,
    *,
    with_mode: bool,
) -> _Step:
    """Write the room values to thermostats known to report something else.

    A thermostat that runs its schedule or shows its window phase is left alone,
    except that a hold switches it off too.
    """
    writes: list[tuple[str, Intent]] = []
    # What the thermostats should show now: off when the room is off or held.
    mode: SetHvacMode = SetHvacMode("off" if suspended(state) else "heat")
    for trv in writable(cfg, state):
        known = state.trvs[trv]
        driven = device_driven(known) or known.left_off
        # A mode nobody has reported counts as differing under a hold only: the
        # window must not leave a thermostat heating; elsewhere nothing is
        # written to a thermostat whose mode is unknown.
        if (
            with_mode
            and (holding(state) or not driven)
            and known.reported_mode != mode.mode
            and (known.reported_mode is not None or holding(state))
        ):
            writes.append((trv, mode))
        if driven:
            continue
        goal = target(cfg, state)
        if (
            goal is not None
            and not suspended(state)
            and known.reported_setpoint is not None
            and abs(known.reported_setpoint - goal) > SAME
        ):
            writes.append((trv, SetSetpoint(goal)))
    return write(state, now, writes, capability_of)


def write(
    state: RoomState,
    now: datetime,
    writes: Iterable[tuple[str, Intent]],
    capability_of: _Capabilities,
) -> _Step:
    """Issue the writes in order and remember setpoint and mode as recent writes.

    A thermostat on its own schedule or in its window phase, or one a hold left
    off, gets no setpoint or mode write, and a capability it lacks is not written
    at all. The one exception is the ``off`` of a hold: it reaches every
    thermostat, those too (see ``_held_off``).
    """
    trvs = dict(state.trvs)
    commands: list[Command] = []
    for trv, intent in writes:
        capability = capability_of(intent)
        known = trvs[trv]
        if capability in known.unsupported:
            continue
        if isinstance(intent, SetSetpoint | SetHvacMode):
            hold_off = holding(state) and intent == SetHvacMode("off")
            if not hold_off and (device_driven(known) or known.left_off):
                continue
            value = intent.celsius if isinstance(intent, SetSetpoint) else intent.mode
            known = _remember(known, capability, value, now)
            trvs[trv] = _held_off(known) if hold_off else known
        commands.append(Execute(trv, intent))
    return replace(state, trvs=trvs), commands


def _held_off(known: TrvState) -> TrvState:
    """Return a thermostat after a hold switched it off.

    One on its schedule stays off after the hold (``left_off``; Easy TRV never
    writes ``auto``). One in its window phase is an ordinary thermostat from now
    on, so that the end of the hold brings it back.
    """
    if on_schedule(known):
        known = replace(known, left_off=True)
    if known.note is Note.WINDOW_PHASE:
        known = replace(known, note=None, note_since=None)
    return known


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
