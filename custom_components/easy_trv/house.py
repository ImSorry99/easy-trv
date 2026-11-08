"""The house: presence forced to home or away for every room with presence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from custom_components.easy_trv.const import DOMAIN, EVENT_PRESENCE_FORCED
from custom_components.easy_trv.observation import presence_home

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.core import Event, EventStateChangedData, HomeAssistant

type PresenceOption = Literal["auto", "home", "away"]
type ForcedReason = Literal["user", "presence_changed"]

OPTIONS: Final = ("auto", "home", "away")


def house_device_info(entry_id: str) -> DeviceInfo:
    """Return the device of the house, the one the house entities sit on."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        manufacturer="Easy TRV",
        name="Easy TRV",
    )


class HousePresence:
    """The forced presence of one config entry.

    ``home`` and ``away`` override what every room with presence reads. They
    end by themselves when the global presence entity flips between home and
    away (its raw state, the unknown policy applied); without that entity they
    stay until set back.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        global_entity: str | None,
        *,
        unknown_is_away: bool,
    ) -> None:
        """Hold the option for ``entry_id``; ``global_entity`` may be ``None``."""
        self._hass = hass
        self.entry_id = entry_id
        self.global_entity = global_entity
        self._unknown_is_away = unknown_is_away
        self.option: PresenceOption = "auto"
        self.forced_at: datetime | None = None
        # What the global entity read when the option was set.
        self.raw_away: bool | None = None
        self._current_raw: bool | None = None
        self._listeners: list[Callable[[], None]] = []
        self._unsubscribe: CALLBACK_TYPE | None = None
        self._started = False

    def forced_away(self) -> bool | None:
        """Return the forced value (``True`` away), ``None`` on auto."""
        return None if self.option == "auto" else self.option == "away"

    def subscribe(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call ``listener`` when the forced value changes; return the unsubscribe."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def _read(self) -> bool | None:
        """Return whether the global entity reads away; ``None`` without one."""
        if self.global_entity is None:
            return None
        state = self._hass.states.get(self.global_entity)
        home = presence_home(
            self.global_entity.partition(".")[0],
            None if state is None else state.state,
        )
        return self._unknown_is_away if home is None else not home

    def start(self) -> None:
        """Watch the global entity; drop a restored option if it reads differently."""
        self._started = True
        if self.global_entity is not None:
            self._unsubscribe = async_track_state_change_event(
                self._hass, [self.global_entity], self._on_change
            )
        self._check()

    def stop(self) -> None:
        """Stop watching the global entity."""
        self._started = False
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def restore(
        self,
        option: PresenceOption,
        forced_at: datetime | None,
        raw_away: bool | None,  # noqa: FBT001
    ) -> None:
        """Take the option of the last run; ``start`` checks it against the entity."""
        self.option = option
        self.forced_at = None if option == "auto" else forced_at
        self.raw_away = raw_away
        if self._started:
            self._check()
        self._notify()

    def _check(self) -> None:
        """Drop the option when the entity reads differently than when it was set."""
        self._current_raw = self._read()
        if (
            self.option != "auto"
            and self.raw_away is not None
            and self._current_raw is not self.raw_away
        ):
            self._release()

    async def async_set_option(
        self, option: PresenceOption, reason: ForcedReason = "user"
    ) -> None:
        """Set the option, tell the rooms and fire the event."""
        self.option = option
        if option == "auto":
            self.forced_at = None
            self.raw_away = None
        else:
            self.forced_at = dt_util.utcnow()
            self.raw_away = self._read()
        self._hass.bus.async_fire(
            EVENT_PRESENCE_FORCED, {"option": option, "reason": reason}
        )
        self._notify()

    def _release(self) -> None:
        """Go back to auto because the global entity changed."""
        self.option = "auto"
        self.forced_at = None
        self.raw_away = None
        self._hass.bus.async_fire(
            EVENT_PRESENCE_FORCED, {"option": "auto", "reason": "presence_changed"}
        )
        self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @callback
    def _on_change(self, _event: Event[EventStateChangedData]) -> None:
        """End forcing when the global entity flipped between home and away."""
        raw = self._read()
        flipped = raw is not self._current_raw
        self._current_raw = raw
        if flipped and self.option != "auto":
            self._release()
