"""The away setback number of the house."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature

from custom_components.easy_trv.const import CONF_AWAY_SETBACK, DEFAULT_AWAY_SETBACK
from custom_components.easy_trv.core.settings import overrides
from custom_components.easy_trv.house import house_device_info

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from custom_components.easy_trv import EasyTrvConfigEntry

PARALLEL_UPDATES = 0


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EasyTrvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the away setback number to the house device."""
    async_add_entities([AwaySetbackNumber(entry)])


class AwaySetbackNumber(NumberEntity):
    """Show and set the global away setback; rooms with their own keep it."""

    _attr_has_entity_name = True
    _attr_translation_key = "away_setback"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0.5
    _attr_native_max_value = 10.0
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfTemperature.KELVIN

    def __init__(self, entry: EasyTrvConfigEntry) -> None:
        """Link the number to the config entry."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_away_setback"
        self._attr_device_info = house_device_info(entry.entry_id)

    @property
    def native_value(self) -> float:
        """Return the global setback."""
        return float(self._entry.options.get(CONF_AWAY_SETBACK, DEFAULT_AWAY_SETBACK))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the names of the rooms that have a setback of their own."""
        return {
            "rooms_with_own_setback": [
                subentry.title
                for subentry in self._entry.subentries.values()
                if CONF_AWAY_SETBACK in overrides(subentry.data)
            ]
        }

    async def async_added_to_hass(self) -> None:
        """Show the option again whenever the entry's options change."""
        self.async_on_remove(self._entry.add_update_listener(self._on_update))

    async def _on_update(
        self, _hass: HomeAssistant, _entry: EasyTrvConfigEntry
    ) -> None:
        """Write the state; the options form may have changed the setback."""
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Write the global option; the update listener applies it live."""
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_AWAY_SETBACK: value}
        )
        self.async_write_ha_state()
