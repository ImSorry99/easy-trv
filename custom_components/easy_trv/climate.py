"""Room thermostat of an Easy TRV room."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import slugify

from custom_components.easy_trv.const import DOMAIN
from custom_components.easy_trv.core.model import Note, Preset, RoomMode

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from custom_components.easy_trv import EasyTrvConfigEntry
    from custom_components.easy_trv.controller import RoomController

PARALLEL_UPDATES = 0

_HVAC_MODES = {RoomMode.HEAT: HVACMode.HEAT, RoomMode.OFF: HVACMode.OFF}
_ROOM_MODES = {mode.value: room_mode for room_mode, mode in _HVAC_MODES.items()}
_PRESETS = {preset.value: preset for preset in Preset}


@dataclass
class RoomExtraData(ExtraStoredData):
    """What a room remembers that its visible attributes do not show."""

    frost_return: float | None

    def as_dict(self) -> dict[str, Any]:
        """Return the data for the restore store."""
        return {"frost_return": self.frost_return}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoomExtraData:
        """Read the stored data; a value that is no number counts as unset."""
        return cls(_number(data.get("frost_return")))


def _number(value: object) -> float | None:
    """Return ``value`` as a float, ``None`` for anything that is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except ValueError:
        return None


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: EasyTrvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a thermostat to every room whose thermostats can take a setpoint."""
    for subentry_id, controller in entry.runtime_data.rooms.items():
        if controller.effective_range is None:
            continue
        async_add_entities(
            [
                RoomClimate(
                    controller,
                    entry.subentries[subentry_id],
                    entry.runtime_data.areas[subentry_id],
                )
            ],
            config_subentry_id=subentry_id,
        )


class RoomClimate(ClimateEntity, RestoreEntity):
    """The thermostat of a room: one setpoint, mode and preset for all valves."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "room"
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]  # noqa: RUF012
    _attr_preset_modes = [Preset.NONE.value, Preset.FROST.value]  # noqa: RUF012
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
        | ClimateEntityFeature.PRESET_MODE
    )

    def __init__(
        self,
        controller: RoomController,
        subentry: ConfigSubentry,
        area_name: str | None,
    ) -> None:
        """Link the thermostat to the room's controller and device.

        ``area_name`` is the area the device is suggested to sit in.
        """
        self._controller = controller
        low, high, step = controller.effective_range or (0.0, 0.0, 0.5)
        self._attr_min_temp = low
        self._attr_max_temp = high
        self._attr_target_temperature_step = step
        self._thermostats = list(controller.thermostats)
        self._attr_unique_id = f"{subentry.subentry_id}_climate"
        # Suggest the entity id like the status sensor does: HA would put the
        # area in front of the device name otherwise.
        self.entity_id = f"climate.{slugify(subentry.title)}_heating"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            manufacturer="Easy TRV",
            translation_key="room",
            translation_placeholders={"room": subentry.title},
            suggested_area=area_name,
        )

    @property
    def hvac_mode(self) -> HVACMode:
        """Return whether the room heats or is switched off."""
        return _HVAC_MODES[self._controller.mode]

    @property
    def preset_mode(self) -> str:
        """Return the preset of the room."""
        return self._controller.preset.value

    @property
    def target_temperature(self) -> float | None:
        """Return the setpoint of the room."""
        return self._controller.setpoint

    @property
    def current_temperature(self) -> float | None:
        """Return the temperature the thermostats are told, else the sensor's."""
        return self._controller.current_temperature

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return where the setpoint came from and what holds the room."""
        controller = self._controller
        notes = controller.notes
        note: Note | None = next(iter(notes.values()), None)
        return {
            "setpoint_source": controller.setpoint_source,
            "in_sync": controller.in_sync,
            "thermostats": self._thermostats,
            "window_open": controller.window_open,
            "outdoor_cutoff": controller.outdoor_hold,
            "note": None if note is None else note.value,
        }

    @property
    def extra_restore_state_data(self) -> RoomExtraData:
        """Return what has to survive a restart besides the visible state."""
        return RoomExtraData(self._controller.frost_return)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature of the room."""
        if (celsius := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self._controller.async_set_setpoint(float(celsius))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch the room on or off."""
        await self._controller.async_set_mode(_ROOM_MODES[hvac_mode.value])

    async def async_turn_on(self) -> None:
        """Switch the room on."""
        await self._controller.async_set_mode(RoomMode.HEAT)

    async def async_turn_off(self) -> None:
        """Switch the room off."""
        await self._controller.async_set_mode(RoomMode.OFF)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Select a preset."""
        await self._controller.async_set_preset(_PRESETS[preset_mode])

    async def async_added_to_hass(self) -> None:
        """Restore the room and write the state whenever the room changes.

        The restore reaches the controller before it starts (Home Assistant
        starts the rooms once it is running), so the start reconciles the
        thermostats with the remembered values and any holds.
        """
        await super().async_added_to_hass()
        self.async_on_remove(
            self._controller.add_update_listener(self.async_write_ha_state)
        )
        last = await self.async_get_last_state()
        if last is None:
            return
        extra = await self.async_get_last_extra_data()
        frost_return = (
            RoomExtraData.from_dict(extra.as_dict()).frost_return if extra else None
        )
        await self._controller.async_restore(
            _number(last.attributes.get(ATTR_TEMPERATURE)),
            _ROOM_MODES.get(last.state),
            _PRESETS.get(str(last.attributes.get("preset_mode"))),
            frost_return,
        )
