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
    Command,
    Execute,
    Intent,
    Note,
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
_SAME = 1e-6
# Reported system modes the room writes over; ``auto`` is the thermostat's schedule.
_OWN_MODES = ("heat", "off")


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
    writes: list[tuple[str, Intent]] = []
    for trv in _writable(cfg, state):
        if mode is RoomMode.OFF:
            writes.append((trv, SetHvacMode("off")))
            continue
        writes.append((trv, SetHvacMode("heat")))
        writes.extend((trv, intent) for intent in resync)
        if state.setpoint is not None:
            writes.append((trv, SetSetpoint(state.setpoint)))
    return _write(state, now, writes, capability_of)


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


def on_restore(
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    restore: RestoreRoom,
    capability_of: _Capabilities,
) -> _Step:
    """Take the room state remembered across a restart; it wins over the devices.

    Only thermostats known to report something else get a write. Thermostats
    that have not reported yet are handled by ``reconcile_start``.
    """
    if cfg.setpoint_range is None:
        return state, []
    if restore.setpoint is not None:
        state = replace(
            state,
            setpoint=clamp(restore.setpoint, cfg.setpoint_range),
            setpoint_source="restore",
        )
    if restore.mode is not None:
        state = replace(state, mode=restore.mode)
    if restore.preset is not None:
        state = replace(state, preset=restore.preset)
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
    first = next(
        (t for t in writable if state.trvs[t].reported_setpoint is not None), None
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


# --- Helpers -------------------------------------------------------------


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

    A thermostat in ``auto`` runs its own schedule and is left alone.
    """
    writes: list[tuple[str, Intent]] = []
    mode: SetHvacMode = SetHvacMode("off" if state.mode is RoomMode.OFF else "heat")
    for trv in _writable(cfg, state):
        known = state.trvs[trv]
        if known.reported_mode is not None and known.reported_mode not in _OWN_MODES:
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

    A thermostat on its own schedule gets no setpoint or mode write, and a
    capability it lacks is not written at all.
    """
    trvs = dict(state.trvs)
    commands: list[Command] = []
    for trv, intent in writes:
        capability = capability_of(intent)
        known = trvs[trv]
        if capability in known.unsupported:
            continue
        if isinstance(intent, SetSetpoint | SetHvacMode):
            if known.note is Note.SCHEDULE:
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
