"""Holds: window contacts and the outdoor cut-off.

A hold keeps the thermostats of the room ``off`` while a window is open or the
outdoor temperature is at or above the cut-off. The room keeps its mode and
setpoint as its wish; they are written back when the last hold ends. The
functions are pure, in the shape of ``RoomLogic.handle``: they take the room
configuration, the state and the time and return the next state with the
commands to run.
"""

from dataclasses import replace
from typing import TYPE_CHECKING

from custom_components.easy_trv.core import setpoint
from custom_components.easy_trv.core.model import (
    ClearIssue,
    Command,
    FireEvent,
    Intent,
    Log,
    RaiseIssue,
    RoomConfig,
    RoomMode,
    RoomState,
    SetHvacMode,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

type _Step = tuple[RoomState, list[Command]]
type _Capabilities = Callable[[Intent], str]
# Source and value intents that bring a thermostat up to date (room logic knows).
type _Resync = Callable[[RoomState, datetime], tuple[RoomState, list[Intent]]]

# Bus event names; they mirror ``const.EVENT_*``, which core cannot import
# because ``const`` needs Home Assistant.
EVENT_WINDOW_HOLD = "easy_trv_window_hold"
EVENT_OUTDOOR_HOLD = "easy_trv_outdoor_hold"
# The outdoor temperature must fall this far below the cut-off to end the hold.
OUTDOOR_DEADBAND = 0.5
# Absorbs binary float noise in the drop comparison.
_TOLERANCE = 1e-9


def held(state: RoomState) -> bool:
    """Return whether a window or outdoor hold keeps the thermostats off."""
    return setpoint.holding(state)


def on_window(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    open_: bool,  # noqa: FBT001
    active: float | None,
    capability_of: _Capabilities,
    resync: _Resync,
) -> _Step:
    """Take the window contacts changing between open and closed.

    ``active`` is the value of the active sensor, remembered at opening for the
    stuck-contact check.
    """
    if open_ is state.window_open:
        return state, []
    was_held = held(state)
    if open_:
        after = replace(
            state,
            window_open=True,
            window_opened_at=now,
            window_open_value=active,
            window_warned=False,
            window_suspect=False,
            window_checked=False,
        )
        commands: list[Command] = []
    else:
        after = replace(
            state,
            window_open=False,
            window_opened_at=None,
            window_open_value=None,
            window_warned=False,
            window_suspect=False,
            window_checked=False,
        )
        commands = [
            ClearIssue("window_open_long", cfg.room_id),
            ClearIssue("window_contact_suspect", cfg.room_id),
        ]
    commands.append(
        FireEvent(
            EVENT_WINDOW_HOLD,
            {"room": cfg.name, "state": "open" if open_ else "closed"},
        )
    )
    after, moved = _transition(cfg, after, now, was_held, capability_of, resync)
    return after, [*commands, *moved]


def on_outdoor(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    celsius: float | None,
    capability_of: _Capabilities,
    resync: _Resync,
) -> _Step:
    """Take a new outdoor temperature; ``None`` means the sensor is unknown.

    The hold starts at the cut-off and ends half a kelvin below it. An unknown
    temperature ends it too (and says so), for the room must not stay off on a
    value nobody can see.
    """
    cutoff = cfg.outdoor_cutoff
    if cutoff is None:
        return state, []
    commands: list[Command] = []
    if celsius is None:
        if not state.outdoor_hold:
            return state, []
        commands.append(
            Log(
                "warning",
                f"{cfg.name}: outdoor sensor unavailable, outdoor cut-off ended",
            )
        )
        hold = False
    elif celsius >= cutoff:
        hold = True
    elif celsius < cutoff - OUTDOOR_DEADBAND:
        hold = False
    else:
        hold = state.outdoor_hold
    if hold is state.outdoor_hold:
        return state, commands
    was_held = held(state)
    after = replace(state, outdoor_hold=hold)
    commands.append(
        FireEvent(
            EVENT_OUTDOOR_HOLD, {"room": cfg.name, "state": "on" if hold else "off"}
        )
    )
    after, moved = _transition(cfg, after, now, was_held, capability_of, resync)
    return after, [*commands, *moved]


def on_tick(
    cfg: RoomConfig, state: RoomState, now: datetime, active: float | None
) -> _Step:
    """Raise the long-open warning and run the stuck-contact check when due."""
    opened = state.window_opened_at
    if not state.window_open or opened is None:
        return state, []
    elapsed = now - opened
    minutes = str(int(elapsed.total_seconds() // 60))
    commands: list[Command] = []
    warn = cfg.window_warn
    if warn is not None and not state.window_warned and elapsed >= warn:
        state = replace(state, window_warned=True)
        commands.append(
            RaiseIssue(
                "window_open_long", cfg.room_id, {"room": cfg.name, "minutes": minutes}
            )
        )
    check = cfg.window_check
    before = state.window_open_value
    if (
        check is not None
        and not state.window_checked
        and elapsed >= check
        and before is not None
        and active is not None
    ):
        state = replace(state, window_checked=True)
        if before - active < cfg.window_check_drop - _TOLERANCE:
            state = replace(state, window_suspect=True)
            commands.append(
                RaiseIssue(
                    "window_contact_suspect",
                    cfg.room_id,
                    {
                        "room": cfg.name,
                        "minutes": minutes,
                        "drop": f"{cfg.window_check_drop:.1f}",
                    },
                )
            )
    return state, commands


# --- Helpers -------------------------------------------------------------


def _transition(  # noqa: PLR0913
    cfg: RoomConfig,
    state: RoomState,
    now: datetime,
    was_held: bool,  # noqa: FBT001
    capability_of: _Capabilities,
    resync: _Resync,
) -> _Step:
    """Switch the thermostats when the first hold starts or the last one ends."""
    if was_held is held(state):
        return state, []
    if cfg.setpoint_range is None:
        return state, [
            Log("debug", f"{cfg.name}: no climate capability, hold writes nothing")
        ]
    if held(state):
        # An off room has its thermostats off already.
        if state.mode is RoomMode.OFF:
            return state, []
        writes = [(trv, SetHvacMode("off")) for trv in setpoint.writable(cfg, state)]
        return setpoint.write(state, now, writes, capability_of)
    if state.mode is not RoomMode.HEAT:
        return state, []
    state, intents = resync(state, now)
    return setpoint.resume(cfg, state, now, capability_of, intents)
