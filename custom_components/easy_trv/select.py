"""The presence select of the house."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from custom_components.easy_trv.house import OPTIONS, house_device_info

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from custom_components.easy_trv import EasyTrvConfigEntry
    from custom_components.easy_trv.house import HousePresence, PresenceOption

PARALLEL_UPDATES = 0


@dataclass
class PresenceExtraData(ExtraStoredData):
    """What is stored besides the option: when it was set and what presence read."""

    forced_at: datetime | None
    raw_away: bool | None

    def as_dict(self) -> dict[str, Any]:
        """Return the data as a dict for storage."""
        return {
            "forced_at": None if self.forced_at is None else self.forced_at.isoformat(),
            "raw_away": self.raw_away,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PresenceExtraData:
        """Read the stored dict back."""
        forced_at = data.get("forced_at")
        return cls(
            dt_util.parse_datetime(forced_at) if isinstance(forced_at, str) else None,
            data.get("raw_away"),
        )


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EasyTrvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the presence select to the house device."""
    async_add_entities([PresenceSelect(entry.runtime_data.house)])


class PresenceSelect(SelectEntity, RestoreEntity):
    """Force the presence of every room with presence: automatic, home or away."""

    _attr_has_entity_name = True
    _attr_translation_key = "presence"
    _attr_should_poll = False

    def __init__(self, house: HousePresence) -> None:
        """Link the select to the house."""
        self._house = house
        self._attr_options = list(OPTIONS)
        self._attr_unique_id = f"{house.entry_id}_presence"
        self._attr_device_info = house_device_info(house.entry_id)

    @property
    def current_option(self) -> str:
        """Return the forced option."""
        return self._house.option

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the global presence entity and, while forced, when it was set."""
        attributes: dict[str, Any] = {}
        if self._house.global_entity is not None:
            attributes["presence_entity"] = self._house.global_entity
        if self._house.forced_at is not None:
            attributes["forced_at"] = self._house.forced_at.isoformat()
        return attributes

    @property
    def extra_restore_state_data(self) -> PresenceExtraData:
        """Return what is stored besides the option."""
        return PresenceExtraData(self._house.forced_at, self._house.raw_away)

    async def async_select_option(self, option: str) -> None:
        """Force ``option``."""
        await self._house.async_set_option(_as_option(option))

    async def async_added_to_hass(self) -> None:
        """Restore the option of the last run, then follow the house."""
        last = await self.async_get_last_state()
        extra = await self.async_get_last_extra_data()
        if last is not None and last.state in OPTIONS and last.state != "auto":
            data = PresenceExtraData.from_dict(extra.as_dict() if extra else {})
            self._house.restore(_as_option(last.state), data.forced_at, data.raw_away)
        self.async_on_remove(self._house.subscribe(self.async_write_ha_state))


def _as_option(option: str) -> PresenceOption:
    """Return ``option`` as one of the three options."""
    if option == "home":
        return "home"
    if option == "away":
        return "away"
    return "auto"
