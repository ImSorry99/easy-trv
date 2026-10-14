"""Status sensor of an Easy TRV room."""

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify

from custom_components.easy_trv.const import DOMAIN

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
    """Add one status sensor to each room, attached to the room's subentry."""
    for subentry_id, controller in entry.runtime_data.rooms.items():
        subentry = entry.subentries[subentry_id]
        async_add_entities(
            [
                RoomStatusSensor(
                    controller, subentry, entry.runtime_data.areas[subentry_id]
                )
            ],
            config_subentry_id=subentry_id,
        )


class RoomStatusSensor(SensorEntity):
    """The status of a room (``ok``, ``degraded`` or ``error``).

    The state attributes carry the active temperature sensor, the temperature
    source, the health of each thermostat and the last error.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "room_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(
        self,
        controller: RoomController,
        subentry: ConfigSubentry,
        area_name: str | None,
    ) -> None:
        """Link the sensor to the room's controller and device.

        ``area_name`` is the area the device is suggested to sit in; a user's
        choice of area for the device is kept.
        """
        self._controller = controller
        self._attr_options = ["ok", "degraded", "error"]
        self._attr_unique_id = f"{subentry.subentry_id}_status"
        # Suggest the entity id: HA would put the area in front of the device
        # name, which repeats the area for a room created from it. Only used
        # while the unique id is not registered, so existing ids stay.
        self.entity_id = f"sensor.{slugify(subentry.title)}_heating_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            manufacturer="Easy TRV",
            translation_key="room",
            translation_placeholders={"room": subentry.title},
            suggested_area=area_name,
        )

    @property
    def native_value(self) -> str:
        """Return the room status."""
        return self._controller.status.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the details the controller published with the status."""
        return self._controller.attributes

    async def async_added_to_hass(self) -> None:
        """Write the state whenever the controller publishes a new status."""
        self.async_on_remove(self._controller.add_listener(self.async_write_ha_state))
