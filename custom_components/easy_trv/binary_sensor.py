"""The window and presence binary sensors of an Easy TRV room."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (  # type: ignore[attr-defined]
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.util import slugify

from custom_components.easy_trv.sensor import _room_device_info

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from custom_components.easy_trv import EasyTrvConfigEntry
    from custom_components.easy_trv.controller import RoomController

PARALLEL_UPDATES = 0


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EasyTrvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the window sensor to rooms with contacts, the presence one to the rest."""
    for subentry_id, controller in entry.runtime_data.rooms.items():
        subentry = entry.subentries[subentry_id]
        area_name = entry.runtime_data.areas[subentry_id]
        sensors: list[RoomBinarySensor] = []
        if controller.has_window_contacts:
            sensors.append(WindowSensor(controller, subentry, area_name))
        if controller.presence_entity is not None:
            sensors.append(PresenceSensor(controller, subentry, area_name))
        if sensors:
            async_add_entities(sensors, config_subentry_id=subentry_id)


class RoomBinarySensor(BinarySensorEntity):
    """Base of the binary sensors of a room, written after every room event."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _suffix: str

    def __init__(
        self,
        controller: RoomController,
        subentry: ConfigSubentry,
        area_name: str | None,
    ) -> None:
        """Link the sensor to the room's controller and device."""
        self._controller = controller
        self._attr_unique_id = f"{subentry.subentry_id}_{self._suffix}"
        # Suggested like the heating-state sensor's, to keep the area out of it.
        self.entity_id = f"binary_sensor.{slugify(subentry.title)}_{self._suffix}"
        self._attr_device_info = _room_device_info(subentry, area_name)

    async def async_added_to_hass(self) -> None:
        """Write the state after every event of the room."""
        self.async_on_remove(
            self._controller.add_update_listener(self.async_write_ha_state)
        )


class WindowSensor(RoomBinarySensor):
    """On as soon as any window contact reads open, without the opening delay."""

    _attr_translation_key = "window"
    _attr_device_class = BinarySensorDeviceClass.WINDOW
    _suffix = "window"

    @property
    def is_on(self) -> bool:
        """Return whether any contact reads open."""
        return bool(self._controller.open_contacts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the contacts that are open."""
        return {"contacts_open": list(self._controller.open_contacts)}


class PresenceSensor(RoomBinarySensor):
    """On while the room reads home: the delay and the house's forcing applied."""

    _attr_translation_key = "presence"
    _attr_device_class = BinarySensorDeviceClass.PRESENCE
    _suffix = "presence"

    @property
    def is_on(self) -> bool | None:
        """Return whether the room reads home."""
        return self._controller.presence_home

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return what decides presence, the entity and when away will count."""
        controller = self._controller
        away_from = controller.presence_away_from
        attributes: dict[str, Any] = {
            "source": controller.presence_source,
            "presence_entity": controller.presence_entity,
        }
        if away_from is not None:
            attributes["away_from"] = away_from.isoformat()
        return attributes
