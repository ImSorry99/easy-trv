"""What flows into a room from outside: reports, knob timers and holds.

These are the parts of the controller that watch the world and feed the room
logic: the thermostats' own reports (through the gateway), the timers that
debounce knob turns, the window contacts and the outdoor sensor. Each one hands
its events to a ``feed`` callable, which is the controller's entry point into
the logic, and releases everything again on ``stop``.
"""

import logging
import math
from collections import deque
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from custom_components.easy_trv import issues
from custom_components.easy_trv.adapters.zha_gateway import GatewayError
from custom_components.easy_trv.const import CONTACT_SILENT_ISSUE_MINUTES
from custom_components.easy_trv.core.model import (
    KnobSettled,
    OutdoorChanged,
    PresenceChanged,
    TrvObserved,
    WindowChanged,
)
from custom_components.easy_trv.core.values import parse_temperature

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from homeassistant.core import (
        CALLBACK_TYPE,
        Event,
        EventStateChangedData,
        HomeAssistant,
        State,
    )

    from custom_components.easy_trv.adapters.zha_gateway import (
        ObservationKind,
        TrvGateway,
    )
    from custom_components.easy_trv.core.model import Event as LogicEvent
    from custom_components.easy_trv.core.model import RoomConfig
    from custom_components.easy_trv.profiles.engine import Profile

_LOGGER = logging.getLogger(__name__)

# How many of the latest observations per thermostat diagnostics keep.
OBSERVATIONS_KEPT: Final = 10
_FAHRENHEIT: Final = frozenset({"°F", "F"})
# A contact in one of these states says nothing; it keeps its last state.
_SILENT: Final = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN})


class ThermostatSubscriptions:
    """Listen to the reports of the observed capabilities of every thermostat."""

    def __init__(
        self,
        gateway: TrvGateway,
        profile: Profile,
        ieees: Mapping[str, str],
        feed: Callable[[LogicEvent], None],
        room: str,
    ) -> None:
        """Group the profile's observed capabilities by endpoint and cluster.

        ``ieees`` maps each thermostat's climate entity to its IEEE address.
        """
        self._gateway = gateway
        self._profile = profile
        self._ieees = ieees
        self._feed = feed
        self._room = room
        groups: dict[tuple[int, int], dict[int, str]] = {}
        for name in profile.observed():
            spec = profile.capabilities[name]
            groups.setdefault((spec.endpoint, spec.cluster), {})[spec.attribute] = name
        self._groups = groups
        self._active: dict[str, list[CALLBACK_TYPE]] = {}
        self._recent: dict[str, deque[dict[str, Any]]] = {
            trv: deque(maxlen=OBSERVATIONS_KEPT) for trv in ieees
        }

    def recent(self) -> dict[str, list[dict[str, Any]]]:
        """Return the latest observations of each thermostat, oldest first."""
        return {trv: list(seen) for trv, seen in self._recent.items()}

    def subscribe_all(self) -> None:
        """Subscribe every thermostat."""
        for trv in self._ieees:
            self.subscribe(trv)

    def subscribe(self, trv: str) -> None:
        """(Re-)subscribe one thermostat; a gateway that cannot is retried later.

        An existing subscription is released first: ZHA may have reloaded and
        dropped its listeners, and the old ones must not deliver twice.
        """
        self.unsubscribe(trv)
        unsubscribers = self._active.setdefault(trv, [])
        for (endpoint, cluster), capabilities in self._groups.items():
            try:
                unsubscribers.append(
                    self._gateway.subscribe(
                        self._ieees[trv],
                        endpoint,
                        cluster,
                        list(capabilities),
                        self._deliver(trv, capabilities),
                    )
                )
            except GatewayError as err:
                _LOGGER.debug(
                    "%s: cannot observe %s yet (subscribe failed: %s); "
                    "trying again when it is available",
                    self._room,
                    trv,
                    err,
                )
                return

    def unsubscribe(self, trv: str) -> None:
        """Release the subscriptions of one thermostat."""
        for unsubscribe in self._active.pop(trv, []):
            unsubscribe()

    def stop(self) -> None:
        """Release every subscription."""
        for trv in list(self._active):
            self.unsubscribe(trv)

    def _deliver(
        self, trv: str, capabilities: Mapping[int, str]
    ) -> Callable[[str, int, ObservationKind, int], None]:
        """Return the gateway callback of one thermostat and cluster."""

        def deliver(
            _ieee: str, attribute: int, kind: ObservationKind, raw: int
        ) -> None:
            capability = capabilities.get(attribute)
            if capability is None:
                return
            self._recent[trv].append(
                {
                    "at": dt_util.utcnow().isoformat(),
                    "capability": capability,
                    "raw": raw,
                    "kind": kind,
                }
            )
            value = self._profile.decode(capability, raw)
            self._feed(TrvObserved(trv, capability, value, raw, kind))

        return deliver


class KnobTimers:
    """One debounce timer per thermostat capability, re-armed on every knob step."""

    def __init__(self, hass: HomeAssistant, feed: Callable[[LogicEvent], None]) -> None:
        """Keep the timers of a room."""
        self._hass = hass
        self._feed = feed
        self._timers: dict[tuple[str, str], CALLBACK_TYPE] = {}

    def arm(self, trv: str, capability: str, seconds: float) -> None:
        """Start the timer of a capability, replacing one that is running."""
        key = (trv, capability)
        self.cancel(trv, capability)

        @callback
        def fire(_now: datetime) -> None:
            self._timers.pop(key, None)
            self._feed(KnobSettled(trv, capability))

        self._timers[key] = async_call_later(self._hass, seconds, fire)

    def cancel(self, trv: str, capability: str) -> None:
        """Stop the timer of a capability, if it runs."""
        cancel = self._timers.pop((trv, capability), None)
        if cancel is not None:
            cancel()

    def stop(self) -> None:
        """Stop every timer."""
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()


# What each presence domain reports while someone is home.
_HOME_STATES: Final[Mapping[str, frozenset[str]]] = {
    "person": frozenset({"home"}),
    "device_tracker": frozenset({"home"}),
    "group": frozenset({"home", "on"}),
    "binary_sensor": frozenset({"on"}),
    "input_boolean": frozenset({"on"}),
}
_AWAY_STATES: Final[Mapping[str, frozenset[str]]] = {
    "group": frozenset({"not_home", "off"}),
    "binary_sensor": frozenset({"off"}),
    "input_boolean": frozenset({"off"}),
}
_UNKNOWN_STATES: Final = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN})


def presence_home(domain: str, state: str | None) -> bool | None:
    """Return whether an entity's state says someone is home; ``None``: unknown.

    ``person`` and ``device_tracker`` are home at ``home`` and away in any other
    zone; ``zone`` counts the persons in it; the switch-like domains read
    ``on`` as home. Anything else, a missing or unavailable state included, is
    unknown.
    """
    if state is None or state in _UNKNOWN_STATES:
        return None
    if domain == "zone":
        try:
            return float(state) > 0
        except ValueError:
            return None
    home = _HOME_STATES.get(domain)
    if home is None:
        return None
    if state in home:
        return True
    # ``person`` and ``device_tracker`` list no away states: any zone is away.
    away = _AWAY_STATES.get(domain)
    return False if away is None or state in away else None


class PresenceInput:
    """Track a room's presence entity and feed home and away to the room."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        *,
        unknown_is_away: bool,
        feed: Callable[[LogicEvent], None],
    ) -> None:
        """Watch ``entity_id``; an unknown state reads as ``unknown_is_away``."""
        self._hass = hass
        self._entity_id = entity_id
        self._unknown_is_away = unknown_is_away
        self._feed = feed
        self._away: bool | None = None
        self._unsubscribe: CALLBACK_TYPE | None = None

    def start(self) -> bool:
        """Start tracking; return whether presence reads away now."""
        self._unsubscribe = async_track_state_change_event(
            self._hass, [self._entity_id], self._on_change
        )
        self._away = self._read(self._hass.states.get(self._entity_id))
        return self._away

    def stop(self) -> None:
        """Stop tracking."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _read(self, state: State | None) -> bool:
        """Return whether ``state`` reads away, the unknown policy applied."""
        home = presence_home(
            self._entity_id.partition(".")[0], None if state is None else state.state
        )
        return self._unknown_is_away if home is None else not home

    @callback
    def _on_change(self, event: Event[EventStateChangedData]) -> None:
        """Feed a change between home and away; anything else is not news."""
        away = self._read(event.data["new_state"])
        if away is self._away:
            return
        self._away = away
        self._feed(PresenceChanged(away))


def _outdoor_celsius(state: State | None) -> float | None:
    """Read the outdoor sensor in °C; ``None`` when it has no usable number."""
    if state is None or state.state in _SILENT:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit in _FAHRENHEIT:
        return parse_temperature(state.state, unit)
    try:
        value = float(state.state)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


class HoldInputs:
    """Track the window contacts and the outdoor sensor of a room."""

    def __init__(  # noqa: PLR0913 - the room's entry points, one argument each
        self,
        hass: HomeAssistant,
        config: RoomConfig,
        outdoor_sensor: str | None,
        feed: Callable[[LogicEvent], None],
        changed: Callable[[], None],
        *,
        raw_changed: Callable[[bool], None] | None = None,
    ) -> None:
        """Watch the contacts of ``config`` and, when needed, ``outdoor_sensor``.

        The outdoor sensor is needed for a cut-off, and for window contacts
        whose long-open warning waits until it is colder outside. ``changed``
        is called when the set of silent contacts changes and when the open delay
        starts or is cancelled, which the room shows although the logic has no
        event for it. ``raw_changed`` is called with the new state when the
        contacts go from all closed to any open or back, at that moment and not
        after the open delay; a contact that says nothing counts as it last was.
        It runs before the room is told (by ``feed`` or ``changed``, one of which
        always follows), and not for the state the room starts with.
        """
        self._hass = hass
        self._config = config
        needed = config.outdoor_cutoff is not None or (
            config.window_warn_only_colder and bool(config.window_contacts)
        )
        self._outdoor = outdoor_sensor if needed else None
        self._feed = feed
        self._changed = changed
        self._raw_changed = raw_changed
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._window_open = False
        # Whether any contact is open, before the open delay.
        self._raw_open = False
        self._unreliable: frozenset[str] = frozenset()
        # contact -> whether it was open the last time it said anything
        self._last: dict[str, bool] = {}
        # The timer of a window that is open but not yet counted as such, and
        # when it ends.
        self._pending_open: CALLBACK_TYPE | None = None
        self._window_off_at: datetime | None = None
        # The timer that raises the issue of silent contacts, and whether it has.
        self._silent_timer: CALLBACK_TYPE | None = None
        self._silent_raised = False

    @property
    def contacts_reliable(self) -> bool:
        """Return whether every window contact reports open or closed."""
        return not self._unreliable

    @property
    def silent_contacts(self) -> frozenset[str]:
        """Return the contacts that say nothing: unavailable, unknown or missing."""
        return self._unreliable

    @property
    def window_off_at(self) -> datetime | None:
        """Return when the running open delay switches the room off, else ``None``."""
        return self._window_off_at

    def start(self) -> tuple[bool, float | None]:
        """Start tracking; return whether a window is open and the outdoor °C.

        The window state is not persisted, so these are the inputs the room
        starts with (a contact that is open already is open without the delay: it
        was open before); the outdoor temperature is ``None`` when it does not
        matter.
        """
        outdoor: float | None = None
        if self._config.window_contacts:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self._hass, self._config.window_contacts, self._on_contact
                )
            )
            self._window_open = self._resolve()
            self._raw_open = self._window_open
            self._track_silent(frozenset())
        if self._outdoor is not None:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self._hass, [self._outdoor], self._on_outdoor
                )
            )
            outdoor = _outdoor_celsius(self._hass.states.get(self._outdoor))
        return self._window_open, outdoor

    def stop(self) -> None:
        """Stop tracking, drop both timers and take the contacts issue back.

        Nobody is told: the room is going away.
        """
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._drop_pending_open()
        self._cancel_silent_timer()
        self._silent_raised = False
        issues.clear_window_contact_unavailable(self._hass, self._config.room_id)

    def _resolve(self) -> bool:
        """Return whether any contact is open; note the contacts that say nothing.

        A contact that says nothing keeps the state it last had: one that was
        open stays open (it did not report closing), and one never seen with a
        state counts as closed.
        """
        is_open = False
        silent: set[str] = set()
        for entity_id in self._config.window_contacts:
            state = self._hass.states.get(entity_id)
            if state is None or state.state in _SILENT:
                silent.add(entity_id)
                kept = self._last.get(entity_id)
                if entity_id not in self._unreliable:
                    _LOGGER.debug(
                        "%s: window contact %s is %s, keeping its last state (%s)",
                        self._config.name,
                        entity_id,
                        "missing" if state is None else state.state,
                        "never seen, closed"
                        if kept is None
                        else "open"
                        if kept
                        else "closed",
                    )
                is_open = is_open or bool(kept)
            else:
                self._last[entity_id] = state.state == STATE_ON
                is_open = is_open or self._last[entity_id]
                if entity_id in self._unreliable:
                    _LOGGER.debug(
                        "%s: window contact %s reports again (%s)",
                        self._config.name,
                        entity_id,
                        state.state,
                    )
        self._unreliable = frozenset(silent)
        return is_open

    @callback
    def _on_contact(self, _event: Event[EventStateChangedData]) -> None:
        """Resolve the contacts again; feed the logic when open/closed flips.

        Closed is fed at once. Open is fed after ``window_open_delay`` if a
        contact is still open then, so that airing for a moment does not switch
        the heating off.
        """
        silent_before = self._unreliable
        is_open = self._resolve()
        self._track_silent(silent_before)
        if is_open != self._raw_open:
            # Before the room is told, which is what makes it notify.
            self._raw_open = is_open
            if self._raw_changed is not None:
                self._raw_changed(is_open)
        fed = False
        if is_open and not self._window_open:
            fed = self._open_after_delay()
        elif not is_open:
            self._cancel_pending_open()
            if self._window_open:
                self._window_open = False
                self._feed(WindowChanged(open=False))
                fed = True
        if not fed and silent_before != self._unreliable:
            self._changed()

    def _track_silent(self, before: frozenset[str]) -> None:
        """Time the silence of the contacts, raise the issue and take it back.

        The timer starts when the silent set goes from empty to non-empty and
        stops when it empties again, so a contact that comes and goes starts
        over each time; a change of the set in between leaves it running. While
        the issue is raised, a change of the set updates it.
        """
        silent = self._unreliable
        if not silent:
            self._cancel_silent_timer()
            if self._silent_raised:
                self._silent_raised = False
                issues.clear_window_contact_unavailable(
                    self._hass, self._config.room_id
                )
        elif self._silent_raised:
            if silent != before:
                self._raise_silent_issue()
        elif self._silent_timer is None:

            @callback
            def fire(_now: datetime) -> None:
                # Cancelled as soon as the set empties: contacts are silent now.
                self._silent_timer = None
                self._silent_raised = True
                self._raise_silent_issue()

            self._silent_timer = async_call_later(
                self._hass, CONTACT_SILENT_ISSUE_MINUTES * 60, fire
            )

    def _cancel_silent_timer(self) -> None:
        """Stop the timer that raises the issue of silent contacts."""
        if self._silent_timer is not None:
            self._silent_timer()
            self._silent_timer = None

    def _raise_silent_issue(self) -> None:
        """Raise the issue naming the contacts that are silent now."""
        names = []
        for entity_id in self._unreliable:
            state = self._hass.states.get(entity_id)
            names.append(entity_id if state is None else state.name)
        issues.raise_window_contact_unavailable(
            self._hass,
            self._config.room_id,
            self._config.name,
            ", ".join(sorted(names)),
        )

    def _open_after_delay(self) -> bool:
        """Feed an open window now, or arm the delay; return whether it was fed."""
        delay = self._config.window_open_delay
        if not delay:
            self._window_open = True
            self._feed(WindowChanged(open=True))
            return True
        if self._pending_open is None:

            @callback
            def fire(_now: datetime) -> None:
                self._pending_open = None
                self._window_off_at = None
                self._window_open = True
                self._feed(WindowChanged(open=True))

            self._pending_open = async_call_later(
                self._hass, delay.total_seconds(), fire
            )
            self._window_off_at = dt_util.utcnow() + delay
            self._changed()
        return False

    def _cancel_pending_open(self) -> None:
        """Stop the delay of a window that has not been counted as open yet."""
        if self._drop_pending_open():
            self._changed()

    def _drop_pending_open(self) -> bool:
        """Stop the delay without telling anyone; return whether one was running."""
        if self._pending_open is None:
            return False
        self._pending_open()
        self._pending_open = None
        self._window_off_at = None
        return True

    @callback
    def _on_outdoor(self, event: Event[EventStateChangedData]) -> None:
        """Feed the new outdoor temperature (``None`` when it is unusable)."""
        self._feed(OutdoorChanged(_outdoor_celsius(event.data["new_state"])))
