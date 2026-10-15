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

from custom_components.easy_trv.adapters.zha_gateway import GatewayError
from custom_components.easy_trv.core.model import (
    KnobSettled,
    OutdoorChanged,
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
# A contact in one of these states says nothing; it counts as closed.
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

    def __init__(
        self,
        hass: HomeAssistant,
        config: RoomConfig,
        outdoor_sensor: str | None,
        feed: Callable[[LogicEvent], None],
        changed: Callable[[], None],
    ) -> None:
        """Watch the contacts of ``config`` and, with a cut-off, ``outdoor_sensor``.

        ``changed`` is called when the contacts become (un)reliable, which the
        room shows as ``in_sync`` although the logic has no event for it.
        """
        self._hass = hass
        self._config = config
        self._outdoor = outdoor_sensor if config.outdoor_cutoff is not None else None
        self._feed = feed
        self._changed = changed
        self._unsubscribers: list[CALLBACK_TYPE] = []
        self._window_open = False
        self._unreliable: frozenset[str] = frozenset()

    @property
    def contacts_reliable(self) -> bool:
        """Return whether every window contact reports open or closed."""
        return not self._unreliable

    def start(self) -> tuple[bool, float | None]:
        """Start tracking; return whether a window is open and the outdoor °C.

        The window state is not persisted, so these are the inputs the room
        starts with; the outdoor temperature is ``None`` when it does not matter.
        """
        outdoor: float | None = None
        if self._config.window_contacts:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self._hass, self._config.window_contacts, self._on_contact
                )
            )
            self._window_open = self._resolve()
        if self._outdoor is not None:
            self._unsubscribers.append(
                async_track_state_change_event(
                    self._hass, [self._outdoor], self._on_outdoor
                )
            )
            outdoor = _outdoor_celsius(self._hass.states.get(self._outdoor))
        return self._window_open, outdoor

    def stop(self) -> None:
        """Stop tracking."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    def _resolve(self) -> bool:
        """Return whether any contact is open; note the contacts that say nothing."""
        is_open = False
        silent: set[str] = set()
        for entity_id in self._config.window_contacts:
            state = self._hass.states.get(entity_id)
            if state is None or state.state in _SILENT:
                silent.add(entity_id)
                _LOGGER.debug(
                    "%s: window contact %s is %s, counting it as closed",
                    self._config.name,
                    entity_id,
                    "missing" if state is None else state.state,
                )
            elif state.state == STATE_ON:
                is_open = True
        self._unreliable = frozenset(silent)
        return is_open

    @callback
    def _on_contact(self, _event: Event[EventStateChangedData]) -> None:
        """Resolve the contacts again; feed the logic when open/closed flips."""
        reliable = self.contacts_reliable
        is_open = self._resolve()
        if is_open is not self._window_open:
            self._window_open = is_open
            self._feed(WindowChanged(open=is_open))
        elif reliable != self.contacts_reliable:
            self._changed()

    @callback
    def _on_outdoor(self, event: Event[EventStateChangedData]) -> None:
        """Feed the new outdoor temperature (``None`` when it is unusable)."""
        self._feed(OutdoorChanged(_outdoor_celsius(event.data["new_state"])))
