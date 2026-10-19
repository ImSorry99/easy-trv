"""Room thermostat of an Easy TRV room."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import slugify

from custom_components.easy_trv.const import DOMAIN
from custom_components.easy_trv.core.model import (
    Note,
    OperatingMode,
    Preset,
    RoomMode,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from custom_components.easy_trv import EasyTrvConfigEntry
    from custom_components.easy_trv.controller import RoomController

PARALLEL_UPDATES = 0

# Home Assistant has no mode for frost protection: ``auto`` carries it.
_HVAC_MODES = {
    OperatingMode.OFF: HVACMode.OFF,
    OperatingMode.HEAT: HVACMode.HEAT,
    OperatingMode.FROST: HVACMode.AUTO,
}
_OPERATIONS = {mode: operation for operation, mode in _HVAC_MODES.items()}
_ROOM_MODES = {HVACMode.HEAT.value: RoomMode.HEAT, HVACMode.OFF.value: RoomMode.OFF}
_PRESETS = {preset.value: preset for preset in Preset}


@dataclass
class RoomExtraData(ExtraStoredData):
    """What a room remembers that its visible attributes do not show.

    ``preset`` is whether the room is on frost protection; ``None`` when the
    data predates it (0.3.0 kept it in the ``preset_mode`` attribute).
    ``away_override`` is a target set while away (0.7.0).
    """

    frost_return: float | None
    preset: Preset | None = None
    away_override: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return the data for the restore store."""
        return {
            "frost_return": self.frost_return,
            "preset": None if self.preset is None else self.preset.value,
            "away_override": self.away_override,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoomExtraData:
        """Read the stored data; a value that is not valid counts as unset."""
        return cls(
            _number(data.get("frost_return")),
            _PRESETS.get(str(data.get("preset"))),
            data.get("away_override") is True,
        )


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
    """The thermostat of a room: one setpoint and mode for all valves.

    Frost protection is not a preset here but a mode of the room: the mode
    ``auto``, which the translations show as Frost protection. The thermostat
    then holds the frost protection temperature; ``heat`` returns to the
    target from before and a new target ends frost protection too.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "room"
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT, HVACMode.AUTO]  # noqa: RUF012
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
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
        # Suggest the entity id like the state sensor does: HA would put the
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
        """Return whether the room heats, is on frost protection or is off."""
        return _HVAC_MODES[self._controller.operation]

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
            "presence": controller.presence,
            "away_target": controller.away_target,
            "away_override": controller.away_override,
        }

    @property
    def extra_restore_state_data(self) -> RoomExtraData:
        """Return what has to survive a restart besides the visible state."""
        controller = self._controller
        return RoomExtraData(
            controller.frost_return, controller.preset, controller.away_override
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature of the room, then the mode if one is given.

        Home Assistant passes ``hvac_mode`` on without checking it. The target
        comes first, so that ``auto`` remembers it as the target to return to
        and ``off`` keeps it.
        """
        mode = kwargs.get(ATTR_HVAC_MODE)
        if mode is not None:
            self._valid_mode_or_raise("hvac", mode, self.hvac_modes)
        if (celsius := kwargs.get(ATTR_TEMPERATURE)) is not None:
            await self._controller.async_set_setpoint(float(celsius))
        if mode is not None:
            await self.async_set_hvac_mode(HVACMode(mode))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Switch the room off, to frost protection (``auto``) or to heating.

        Off ends frost protection; heat brings back the target from before.
        """
        await self._controller.async_set_operation(_OPERATIONS[hvac_mode])

    async def async_turn_on(self) -> None:
        """Switch the room on: heating, also from frost protection."""
        await self._controller.async_set_operation(OperatingMode.HEAT)

    async def async_turn_off(self) -> None:
        """Switch the room off."""
        await self._controller.async_set_operation(OperatingMode.OFF)

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
        data = RoomExtraData.from_dict(extra.as_dict()) if extra else None
        mode = _ROOM_MODES.get(last.state)
        preset = data.preset if data else None
        if last.state == HVACMode.AUTO:
            # Frost protection shown as the mode ``auto`` (the room shows it as a mode).
            mode, preset = RoomMode.HEAT, Preset.FROST
        elif preset is None:
            # Remembered by 0.3.0, which had frost protection as a preset.
            preset = _PRESETS.get(str(last.attributes.get("preset_mode")))
        await self._controller.async_restore(
            _number(last.attributes.get(ATTR_TEMPERATURE)),
            mode,
            preset,
            data.frost_return if data else None,
            away_override=data.away_override if data else False,
        )
