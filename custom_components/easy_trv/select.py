"""Operating mode of an Easy TRV room: off, frost protection or heating."""

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify

from custom_components.easy_trv.const import DOMAIN
from custom_components.easy_trv.core.model import OperatingMode

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
    """Add the operating mode to every room that has a room thermostat."""
    for subentry_id, controller in entry.runtime_data.rooms.items():
        if controller.effective_range is None:
            continue
        async_add_entities(
            [
                RoomOperatingModeSelect(
                    controller,
                    entry.subentries[subentry_id],
                    entry.runtime_data.areas[subentry_id],
                )
            ],
            config_subentry_id=subentry_id,
        )


class RoomOperatingModeSelect(SelectEntity):
    """Off, frost protection only, or heating enabled.

    The room thermostat remembers the selection across a restart, so this
    entity needs no restore of its own.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "operating_mode"
    _attr_should_poll = False
    _attr_options = [mode.value for mode in OperatingMode]  # noqa: RUF012

    def __init__(
        self,
        controller: RoomController,
        subentry: ConfigSubentry,
        area_name: str | None,
    ) -> None:
        """Link the selection to the room's controller and device.

        ``area_name`` is the area the device is suggested to sit in.
        """
        self._controller = controller
        self._attr_unique_id = f"{subentry.subentry_id}_operating_mode"
        # Suggest the entity id like the status sensor does: HA would put the
        # area in front of the device name otherwise.
        self.entity_id = f"select.{slugify(subentry.title)}_heating_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            manufacturer="Easy TRV",
            translation_key="room",
            translation_placeholders={"room": subentry.title},
            suggested_area=area_name,
        )

    @property
    def current_option(self) -> str:
        """Return whether the room is off, on frost protection or heats."""
        return self._controller.operation.value

    async def async_select_option(self, option: str) -> None:
        """Switch the room off, to frost protection or to heating."""
        await self._controller.async_set_operation(OperatingMode(option))

    async def async_added_to_hass(self) -> None:
        """Write the state whenever the room changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._controller.add_update_listener(self.async_write_ha_state)
        )
