"""Accumulators for the statistics of a room (pure Python).

A ``Duration`` adds up the time a condition held: the finished periods in
``total`` and the start of the one that is running in ``since``. The functions
return a new value and never mutate, in the shape of the other core modules.
``deviation`` is the control deviation of a room while it heats.
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from custom_components.easy_trv.core.model import OperatingMode
from custom_components.easy_trv.core.setpoint import operation as room_operation

if TYPE_CHECKING:
    from datetime import datetime

    from custom_components.easy_trv.core.model import RoomConfig, RoomState


@dataclass(frozen=True)
class Duration:
    """Time a condition held: finished periods plus the running one, if any."""

    # Seconds of the finished periods.
    total: float
    # When the running period began; ``None`` while the condition does not hold.
    since: datetime | None


def start(duration: Duration, now: datetime) -> Duration:
    """Begin a period; a period that already runs keeps its start."""
    if duration.since is not None:
        return duration
    return replace(duration, since=now)


def stop(duration: Duration, now: datetime) -> Duration:
    """End the running period and add it to the total; no-op when none runs."""
    if duration.since is None:
        return duration
    elapsed = max((now - duration.since).total_seconds(), 0.0)
    return Duration(duration.total + elapsed, None)


def seconds(duration: Duration, now: datetime) -> float:
    """Return the finished periods plus the running part, in seconds."""
    if duration.since is None:
        return duration.total
    return duration.total + max((now - duration.since).total_seconds(), 0.0)


def restored(duration: Duration, stored: float) -> Duration:
    """Raise the total to a stored value; it never lowers it."""
    return replace(duration, total=max(duration.total, stored))


def deviation(
    state: RoomState,
    config: RoomConfig,  # noqa: ARG001 - the shape of the other room functions
    now: datetime,  # noqa: ARG001 - the shape of the other room functions
) -> float | None:
    """Return the active sensor's value minus the setpoint, rounded to 0.1 K.

    Only while the room heats with no hold; ``None`` otherwise, or when the
    sensor or the setpoint is unknown, so the statistics cover heating time only.
    """
    if (
        room_operation(state) is not OperatingMode.HEAT
        or state.window_open
        or state.outdoor_hold
        or state.setpoint is None
    ):
        return None
    reading = state.readings.get(state.active_sensor or "")
    if reading is None or reading.value is None:
        return None
    return round(reading.value - state.setpoint, 1)
