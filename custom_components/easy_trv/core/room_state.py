"""The heating state of a room: one state out of the facts, with all its reasons.

The functions are pure and have no Home Assistant imports. ``state_facts`` reads
the facts off the room state, ``heating_state`` turns them into the state and the
list of every condition that holds, in priority order.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from custom_components.easy_trv.core.model import (
    Note,
    OperatingMode,
    RoomConfig,
    RoomState,
    Source,
    TrvHealth,
    TrvState,
)
from custom_components.easy_trv.core.setpoint import operation as room_operation

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime, timedelta


class HeatingState(StrEnum):
    """What a room is doing, in priority order: the first condition that holds."""

    FAULT = "fault"
    OFF_MANUAL = "off_manual"
    OFF_WINDOW = "off_window"
    OFF_OUTDOOR = "off_outdoor"
    OFF_AWAY = "off_away"
    WINDOW_PENDING = "window_pending"
    EMERGENCY = "emergency"
    RESTRICTED = "restricted"
    BOOST = "boost"
    FROST = "frost"
    HEATING = "heating"


@dataclass(frozen=True)
class StateFacts:
    """Everything the heating state is derived from."""

    operation: OperatingMode
    window_hold: bool
    outdoor_hold: bool
    window_pending: bool
    internal_source: bool
    failsafe_recent: bool
    # Thermostat climate entity -> why it deviates (see ``thermostat_deviations``).
    deviations: Mapping[str, str]
    silent_contacts: frozenset[str]


def failsafe_recent(state: RoomState, now: datetime, refresh: timedelta) -> bool:
    """Return whether a thermostat signalled its fail-safe within ``refresh``."""
    return any(
        trv.failsafe_seen_at is not None and now - trv.failsafe_seen_at <= refresh
        for trv in state.trvs.values()
    )


def _deviation(trv: TrvState) -> str | None:
    """Return the first reason the thermostat deviates, or None if it does not."""
    if trv.health is TrvHealth.ERROR:
        return "error"
    if trv.health is TrvHealth.UNAVAILABLE:
        return "unavailable"
    if trv.health is TrvHealth.UNSUPPORTED:
        return "unsupported"
    if trv.note is Note.SCHEDULE:
        return "schedule"
    if trv.note is Note.WINDOW_PHASE:
        return "window_phase"
    return None


def thermostat_deviations(state: RoomState) -> dict[str, str]:
    """Return why each thermostat deviates; thermostats that do not are absent."""
    deviations: dict[str, str] = {}
    for entity_id, trv in state.trvs.items():
        reason = _deviation(trv)
        if reason is not None:
            deviations[entity_id] = reason
    return deviations


def state_facts(  # noqa: PLR0913 - the room, its config, the clock and the two outside facts
    state: RoomState,
    config: RoomConfig,
    now: datetime,
    refresh: timedelta,
    *,
    window_pending: bool,
    silent_contacts: frozenset[str],
) -> StateFacts:
    """Read the facts of the heating state off the room state."""
    return StateFacts(
        operation=room_operation(state),
        window_hold=state.window_open,
        outdoor_hold=state.outdoor_hold,
        window_pending=window_pending,
        internal_source=bool(config.temp_sensors)
        and state.source is not Source.EXTERNAL,
        failsafe_recent=failsafe_recent(state, now, refresh),
        deviations=thermostat_deviations(state),
        silent_contacts=silent_contacts,
    )


def heating_state(facts: StateFacts) -> tuple[HeatingState, tuple[HeatingState, ...]]:
    """Return the state and every condition that holds, both in priority order.

    The operating mode (off, frost or heat) is always among the reasons, so the
    list is never empty. ``OFF_AWAY`` and ``BOOST`` are placeholders that never
    hold yet.
    """
    deviating = set(facts.deviations.values())
    holds = {
        HeatingState.FAULT: "error" in deviating,
        HeatingState.OFF_MANUAL: facts.operation is OperatingMode.OFF,
        HeatingState.OFF_WINDOW: facts.window_hold,
        HeatingState.OFF_OUTDOOR: facts.outdoor_hold,
        HeatingState.OFF_AWAY: False,
        HeatingState.WINDOW_PENDING: facts.window_pending,
        HeatingState.EMERGENCY: facts.internal_source or facts.failsafe_recent,
        HeatingState.RESTRICTED: bool(deviating - {"error"})
        or bool(facts.silent_contacts),
        HeatingState.BOOST: False,
        HeatingState.FROST: facts.operation is OperatingMode.FROST,
        HeatingState.HEATING: facts.operation is OperatingMode.HEAT,
    }
    reasons = tuple(state for state in HeatingState if holds[state])
    return reasons[0], reasons
