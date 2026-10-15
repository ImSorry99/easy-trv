"""The heating-state sensor of an Easy TRV room and its statistics sensors."""

import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify

from custom_components.easy_trv.const import DOMAIN
from custom_components.easy_trv.core.model import Note
from custom_components.easy_trv.core.room_state import HeatingState
from custom_components.easy_trv.labels import async_option_labels, thermostat_name
from custom_components.easy_trv.statistics_sensor import statistics_sensors

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

    from custom_components.easy_trv import EasyTrvConfigEntry
    from custom_components.easy_trv.controller import RoomController

PARALLEL_UPDATES = 0

# The phrases of the ``hint`` attribute in English. They stand in until the
# translations are loaded and for any phrase a translation lacks.
HINT_PHRASES: Final = {
    "fault": "{name}: error",
    "unavailable": "{name} not reachable",
    "contact": "{name} reports nothing ({why})",
    "unsupported": "{name}: lacks a function ({functions})",
    "schedule": "{name} runs its own schedule",
    "window_phase": "{name}: own window detection active",
    "why_unavailable": "unavailable",
    "why_unknown": "unknown",
    "why_missing": "does not exist",
}
_PLACEHOLDER: Final = re.compile(r"\{(\w+)\}")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EasyTrvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the heating-state sensor and the statistics sensors to each room.

    They sit on the room's subentry and device.
    """
    for subentry_id, controller in entry.runtime_data.rooms.items():
        subentry = entry.subentries[subentry_id]
        area_name = entry.runtime_data.areas[subentry_id]
        async_add_entities(
            [
                RoomStateSensor(controller, subentry, area_name),
                *statistics_sensors(
                    hass,
                    controller,
                    subentry,
                    _room_device_info(subentry, area_name),
                ),
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


def _fill(phrase: str, **values: str) -> str:
    """Put the values into the ``{placeholders}`` of a phrase in one pass.

    A placeholder without a value stays as it is, and so does text that looks
    like one inside a value (a device may be called anything).
    """
    return _PLACEHOLDER.sub(lambda found: values.get(found[1], found[0]), phrase)


class RoomStateSensor(SensorEntity):
    """What the room is doing: one of the fifteen heating states.

    The state is the first condition that holds (see ``HeatingState``). The
    attributes carry a plain-text hint naming what is affected, every condition
    that holds, the time of the last change, the deadline of a running
    open-window delay, the thermostats that deviate and the window contacts that
    say nothing, and the details the room publishes: the active temperature
    sensor, the temperature source, where the setpoint came from, whether the
    thermostats are in sync and the last error. Until the room has started the
    state is unknown and the hint, the reasons and the time of the change are
    empty, as is whatever the room has not published yet; the attributes are
    always all there.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "room_state"
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
        self._phrases = dict(HINT_PHRASES)
        self._attr_options = [state.value for state in HeatingState]
        self._attr_unique_id = f"{subentry.subentry_id}_state"
        # Suggest the entity id: HA would put the area in front of the device
        # name, which repeats the area for a room created from it. Only used
        # while the unique id is not registered, so existing ids stay.
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
        """Return the hint, the reasons, what deviates and the room's details."""
        controller = self._controller
        since = controller.state_since
        window_off_at = controller.window_off_at
        published = controller.attributes
        attributes: dict[str, Any] = {
            "hint": self._hint(),
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
        # What the room publishes is not known until it has started: the keys are
        # there all the same, empty, so the set of attributes never changes.
        attributes["active_sensor"] = published.get("active_sensor")
        attributes["source"] = published.get("source")
        attributes["setpoint_source"] = controller.setpoint_source
        attributes["in_sync"] = controller.in_sync
        attributes["last_error"] = published.get("last_error")
        return attributes

    def _hint(self) -> str | None:
        """Name what restricts the room, in the words of the user's language.

        There is one phrase per affected thermostat or contact, for each reason
        that holds, in the order of the reasons; ``None`` when there is nothing
        to name, and before the room has started.
        """
        controller = self._controller
        if controller.state_since is None:
            return None
        phrases = [
            phrase
            for reason in controller.state_reasons
            for phrase in self._phrases_for(reason)
        ]
        return "; ".join(phrases) or None

    def _phrases_for(self, reason: HeatingState) -> list[str]:
        """Return the phrases that name what is behind one of the reasons."""
        controller = self._controller
        phrases: list[str] = []
        match reason:
            case HeatingState.FAULT:
                phrases = self._thermostats_with("fault", "error")
            case HeatingState.RESTRICTED_UNAVAILABLE:
                phrases = self._thermostats_with("unavailable", "unavailable")
            case HeatingState.RESTRICTED_CONTACT:
                phrases = [
                    self._contact(entity_id) for entity_id in controller.silent_contacts
                ]
            case HeatingState.RESTRICTED_UNSUPPORTED:
                phrases = [
                    self._phrase(
                        "unsupported",
                        name=self._thermostat(entity_id),
                        functions=", ".join(functions),
                    )
                    for entity_id, functions in controller.unsupported_functions.items()
                ]
            case HeatingState.RESTRICTED_SCHEDULE:
                phrases = self._thermostats_noted("schedule", Note.SCHEDULE)
            case HeatingState.RESTRICTED_WINDOW_PHASE:
                phrases = self._thermostats_noted("window_phase", Note.WINDOW_PHASE)
            case _:
                pass
        return phrases

    def _phrase(self, key: str, **values: str) -> str:
        """Return a phrase with its placeholders filled in."""
        return _fill(self._phrases[key], **values)

    def _thermostat(self, entity_id: str) -> str:
        """Return the name of a thermostat for the hint."""
        return thermostat_name(self.hass, entity_id)

    def _thermostats_with(self, key: str, deviation: str) -> list[str]:
        """Return a phrase for each thermostat that deviates in this way."""
        return [
            self._phrase(key, name=self._thermostat(entity_id))
            for entity_id, found in self._controller.deviations.items()
            if found == deviation
        ]

    def _thermostats_noted(self, key: str, note: Note) -> list[str]:
        """Return a phrase for each thermostat that carries this note."""
        return [
            self._phrase(key, name=self._thermostat(entity_id))
            for entity_id, found in self._controller.notes.items()
            if found is note
        ]

    def _contact(self, entity_id: str) -> str:
        """Return the phrase for a window contact that says nothing, and why."""
        state = self.hass.states.get(entity_id)
        if state is None:
            why, name = "why_missing", entity_id
        else:
            why = (
                "why_unavailable" if state.state == STATE_UNAVAILABLE else "why_unknown"
            )
            name = state.name
        return self._phrase("contact", name=name, why=self._phrases[why])

    async def async_added_to_hass(self) -> None:
        """Load the phrases of the hint, then write the state after every event."""
        # Before the first write, which Home Assistant does after this returns.
        self._phrases = {
            **HINT_PHRASES,
            **await async_option_labels(self.hass, "hint"),
        }
        self.async_on_remove(
            self._controller.add_update_listener(self.async_write_ha_state)
        )
