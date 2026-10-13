"""Room-logic data model (pure Python)."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SetRoomTemperature:
    """Tell the thermostat the room temperature measured by an external sensor."""

    celsius: float


@dataclass(frozen=True)
class SetSource:
    """Select which temperature source the thermostat regulates against."""

    source: Literal["external", "internal"]


@dataclass(frozen=True)
class SetWindowDetection:
    """Enable or disable the thermostat's open-window detection."""

    enabled: bool


@dataclass(frozen=True)
class SetHysteresis:
    """Set the thermostat's temperature hysteresis."""

    kelvin: float


Intent = SetRoomTemperature | SetSource | SetWindowDetection | SetHysteresis

INTENT_NAMES: dict[type, str] = {
    SetRoomTemperature: "set_room_temperature",
    SetSource: "set_source",
    SetWindowDetection: "set_window_detection",
    SetHysteresis: "set_hysteresis",
}
