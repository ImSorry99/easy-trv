"""Status and state sensors of an Easy TRV room."""

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify

from custom_components.easy_trv.const import DOMAIN
from custom_components.easy_trv.core.model import Note
from custom_components.easy_trv.core.room_state import HeatingState

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
    """Add a status and a state sensor to each room, on the room's subentry."""
    for subentry_id, controller in entry.runtime_data.rooms.items():
        subentry = entry.subentries[subentry_id]
        area_name = entry.runtime_data.areas[subentry_id]
        async_add_entities(
            [
                RoomStatusSensor(controller, subentry, area_name),
                RoomStateSensor(controller, subentry, area_name),
            ],
            config_subentry_id=subentry_id,
        )


def _room_device_info(subentry: ConfigSubentry, area_name: str | None) -> DeviceInfo:
    """Return the device of a room, the one every entity of the room sits on.

    ``area_name`` is the area the device is suggested to sit in; a user's
    choice of area for the device is kept.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, subentry.subentry_id)},
        manufacturer="Easy TRV",
        translation_key="room",
        translation_placeholders={"room": subentry.title},
        suggested_area=area_name,
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
        """Link the sensor to the room's controller and device."""
        self._controller = controller
        self._attr_options = ["ok", "degraded", "error"]
        self._attr_unique_id = f"{subentry.subentry_id}_status"
        # Suggest the entity id: HA would put the area in front of the device
        # name, which repeats the area for a room created from it. Only used
        # while the unique id is not registered, so existing ids stay.
        self.entity_id = f"sensor.{slugify(subentry.title)}_heating_status"
        self._attr_device_info = _room_device_info(subentry, area_name)

    @property
    def native_value(self) -> str:
        """Return the room status."""
        return self._controller.status.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the details the controller published with the status."""
        controller = self._controller
        return {
            **controller.attributes,
            "window_open": controller.window_open,
            "outdoor_cutoff": controller.outdoor_hold,
            "setpoint_source": controller.setpoint_source,
            "in_sync": controller.in_sync,
            "schedule_thermostats": [
                trv for trv, note in controller.notes.items() if note is Note.SCHEDULE
            ],
        }

    async def async_added_to_hass(self) -> None:
        """Write the state whenever the controller publishes a new status."""
        self.async_on_remove(self._controller.add_listener(self.async_write_ha_state))
        # The room's holds and sync change without a new status.
        self.async_on_remove(
            self._controller.add_update_listener(self.async_write_ha_state)
        )


class RoomStateSensor(SensorEntity):
    """What the room is doing: one of the eleven heating states.

    The state is the first condition that holds (see ``HeatingState``). The
    attributes carry every condition that holds, the time of the last change,
    the deadline of a running open-window delay, the thermostats that deviate
    and the window contacts that say nothing. Until the room has started the
    state is unknown and the reasons and the time of the change are empty.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "room_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_should_poll = False

    def __init__(
        self,
        controller: RoomController,
        subentry: ConfigSubentry,
        area_name: str | None,
    ) -> None:
        """Link the sensor to the room's controller and device."""
        self._controller = controller
        self._attr_options = [state.value for state in HeatingState]
        self._attr_unique_id = f"{subentry.subentry_id}_state"
        # Suggested like the status sensor's id, for the same reason.
        self.entity_id = f"sensor.{slugify(subentry.title)}_heating_state"
        self._attr_device_info = _room_device_info(subentry, area_name)

    @property
    def native_value(self) -> str | None:
        """Return the heating state, ``None`` until the room has started."""
        controller = self._controller
        if controller.state_since is None:
            return None
        return controller.heating_state.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the reasons, the time of the change and what deviates."""
        controller = self._controller
        since = controller.state_since
        window_off_at = controller.window_off_at
        return {
            "reasons": (
                []
                if since is None
                else [reason.value for reason in controller.state_reasons]
            ),
            "since": None if since is None else since.isoformat(),
            "window_off_at": (
                None if window_off_at is None else window_off_at.isoformat()
            ),
            "thermostats": controller.deviations,
            "silent_contacts": list(controller.silent_contacts),
        }

    async def async_added_to_hass(self) -> None:
        """Write the state after every event the room handles."""
        self.async_on_remove(
            self._controller.add_update_listener(self.async_write_ha_state)
        )
