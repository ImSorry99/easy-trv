"""The settings forms of Easy TRV: the options and the settings step of a room.

The tables below describe every setting that is a choice or a number. The
schema builders turn them into the two forms; the stored options stay flat.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from custom_components.easy_trv.const import (
    CONF_ADAPTIVE_MODE,
    CONF_FROST_TEMPERATURE,
    CONF_HIDE_THERMOSTAT_ENTITIES,
    CONF_HYSTERESIS,
    CONF_MAX_ATTEMPTS,
    CONF_MIN_DELTA,
    CONF_OUTDOOR_CUTOFF,
    CONF_OUTDOOR_CUTOFF_CELSIUS,
    CONF_OUTDOOR_SENSOR,
    CONF_PROFILE,
    CONF_REFRESH_MINUTES,
    CONF_SENSOR_TIMEOUT,
    CONF_SETPOINT_MAX,
    CONF_SETPOINT_MIN,
    CONF_SETPOINT_RANGE,
    CONF_VERIFY_DELAY,
    CONF_WINDOW_CHECK_DROP,
    CONF_WINDOW_CHECK_MINUTES,
    CONF_WINDOW_DETECTION,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOW_WARN_COLDER,
    CONF_WINDOW_WARN_MINUTES,
    PROFILE_AUTO,
    SETTING_GLOBAL,
)
from custom_components.easy_trv.core.settings import DEFAULTS, overrides
from custom_components.easy_trv.labels import async_option_labels, localized_number

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from homeassistant.core import HomeAssistant

    from custom_components.easy_trv.profiles.engine import Profile

# The values double as translation keys, which must not contain a dot: 0_4 = 0.4 K.
SELECT_VALUES: Final[Mapping[str, tuple[str, ...]]] = {
    CONF_WINDOW_DETECTION: ("on", "off", "leave"),
    CONF_HYSTERESIS: ("0_2", "0_4", "0_6", "0_8", "1_0"),
    CONF_ADAPTIVE_MODE: ("on", "off", "leave"),
    CONF_SETPOINT_RANGE: ("auto", "manual"),
    CONF_WINDOW_WARN_COLDER: ("on", "off"),
    CONF_HIDE_THERMOSTAT_ENTITIES: ("on", "off"),
}
# A choice that only a room has: the global outdoor cut-off threshold is the
# value to keep ("global") or to ignore ("off") in this room.
ROOM_SELECT_VALUES: Final[Mapping[str, tuple[str, ...]]] = {
    **SELECT_VALUES,
    CONF_OUTDOOR_CUTOFF: ("off",),
}


@dataclass(frozen=True)
class NumberSpec:
    """The bounds and unit of a numeric setting."""

    minimum: float
    maximum: float
    step: float = 1
    unit: str | None = None


NUMBERS: Final[Mapping[str, NumberSpec]] = {
    CONF_REFRESH_MINUTES: NumberSpec(1, 120, unit=UnitOfTime.MINUTES),
    CONF_VERIFY_DELAY: NumberSpec(2, 60, unit=UnitOfTime.SECONDS),
    CONF_MAX_ATTEMPTS: NumberSpec(1, 10),
    CONF_MIN_DELTA: NumberSpec(0.0, 1.0, 0.1, UnitOfTemperature.KELVIN),
    CONF_SENSOR_TIMEOUT: NumberSpec(0, 1440, unit=UnitOfTime.MINUTES),
    CONF_FROST_TEMPERATURE: NumberSpec(4.0, 16.0, 0.5, UnitOfTemperature.CELSIUS),
    CONF_SETPOINT_MIN: NumberSpec(4.0, 35.0, 0.5, UnitOfTemperature.CELSIUS),
    CONF_SETPOINT_MAX: NumberSpec(4.0, 35.0, 0.5, UnitOfTemperature.CELSIUS),
    CONF_WINDOW_OPEN_DELAY: NumberSpec(0, 600, unit=UnitOfTime.SECONDS),
    CONF_WINDOW_WARN_MINUTES: NumberSpec(0, 1440, unit=UnitOfTime.MINUTES),
    CONF_WINDOW_CHECK_MINUTES: NumberSpec(0, 240, unit=UnitOfTime.MINUTES),
    CONF_WINDOW_CHECK_DROP: NumberSpec(0.1, 3.0, 0.1, UnitOfTemperature.KELVIN),
    CONF_OUTDOOR_CUTOFF_CELSIUS: NumberSpec(
        -10.0, 30.0, 0.5, UnitOfTemperature.CELSIUS
    ),
}
# Numbers whose value 0 (or, for the cut-off, no value) reads as "off".
_OFF_AT_ZERO: Final = frozenset(
    {CONF_SENSOR_TIMEOUT, CONF_WINDOW_WARN_MINUTES, CONF_WINDOW_CHECK_MINUTES}
)

# How the options form groups its fields (the stored options stay flat).
_SECTION_WRITES: Final = "writes"
_SECTION_THERMOSTAT: Final = "thermostat"
_SECTION_ROOM_THERMOSTAT: Final = "room_thermostat"
_SECTION_WINDOWS_OUTDOOR: Final = "windows_outdoor"
SECTIONS: Final[Mapping[str, tuple[str, ...]]] = {
    _SECTION_WRITES: (
        CONF_REFRESH_MINUTES,
        CONF_VERIFY_DELAY,
        CONF_MAX_ATTEMPTS,
        CONF_MIN_DELTA,
        CONF_SENSOR_TIMEOUT,
    ),
    _SECTION_THERMOSTAT: (
        CONF_WINDOW_DETECTION,
        CONF_HYSTERESIS,
        CONF_ADAPTIVE_MODE,
        CONF_FROST_TEMPERATURE,
        CONF_HIDE_THERMOSTAT_ENTITIES,
    ),
    _SECTION_ROOM_THERMOSTAT: (
        CONF_SETPOINT_RANGE,
        CONF_SETPOINT_MIN,
        CONF_SETPOINT_MAX,
    ),
    _SECTION_WINDOWS_OUTDOOR: (
        CONF_WINDOW_OPEN_DELAY,
        CONF_WINDOW_WARN_MINUTES,
        CONF_WINDOW_WARN_COLDER,
        CONF_WINDOW_CHECK_MINUTES,
        CONF_WINDOW_CHECK_DROP,
        CONF_OUTDOOR_SENSOR,
        CONF_OUTDOOR_CUTOFF_CELSIUS,
    ),
}
# The order of the room's settings form.
ROOM_SETTING_KEYS: Final = (
    CONF_PROFILE,
    CONF_WINDOW_DETECTION,
    CONF_HYSTERESIS,
    CONF_ADAPTIVE_MODE,
    CONF_FROST_TEMPERATURE,
    CONF_HIDE_THERMOSTAT_ENTITIES,
    CONF_REFRESH_MINUTES,
    CONF_VERIFY_DELAY,
    CONF_MAX_ATTEMPTS,
    CONF_MIN_DELTA,
    CONF_SENSOR_TIMEOUT,
    CONF_SETPOINT_RANGE,
    CONF_SETPOINT_MIN,
    CONF_SETPOINT_MAX,
    CONF_WINDOW_OPEN_DELAY,
    CONF_WINDOW_WARN_MINUTES,
    CONF_WINDOW_WARN_COLDER,
    CONF_WINDOW_CHECK_MINUTES,
    CONF_WINDOW_CHECK_DROP,
    CONF_OUTDOOR_CUTOFF,
    CONF_OUTDOOR_CUTOFF_CELSIUS,
)
ROOM_SELECT_KEYS: Final = (
    CONF_PROFILE,
    *(key for key in ROOM_SETTING_KEYS if key in ROOM_SELECT_VALUES),
)
ROOM_NUMBER_KEYS: Final = tuple(key for key in ROOM_SETTING_KEYS if key in NUMBERS)

ERR_TIMEOUT_RANGE: Final = "timeout_range"
ERR_SETPOINT_RANGE: Final = "setpoint_range"

# The sensor timeout is either off (0) or within these bounds (minutes).
_TIMEOUT_MIN: Final = 10
_TIMEOUT_MAX: Final = 1440


# --- Settings fields (shared by the options flow and the room's settings step) ---


def _number_selector(key: str) -> NumberSelector:
    """Return the box input of a numeric setting."""
    spec = NUMBERS[key]
    config = NumberSelectorConfig(
        min=spec.minimum,
        max=spec.maximum,
        step=spec.step,
        mode=NumberSelectorMode.BOX,
    )
    if spec.unit is not None:
        config["unit_of_measurement"] = spec.unit
    return NumberSelector(config)


def _outdoor_sensor_selector() -> EntitySelector:
    """Return the picker of the outdoor temperature sensor."""
    return EntitySelector(
        EntitySelectorConfig(domain="sensor", device_class="temperature")
    )


def _select_selector(
    key: str, options: list[str] | list[SelectOptionDict]
) -> SelectSelector:
    """Return the dropdown of a choice setting, translated by the setting's key."""
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key=key,
        )
    )


def _stored_number(key: str, value: float) -> float:
    """Return a number as it is stored: the selector's floats become ints if whole."""
    if isinstance(DEFAULTS[key], int):
        return round(value)
    return round(value, 1)


def stored_numbers(
    user_input: Mapping[str, Any], keys: Sequence[str]
) -> dict[str, Any]:
    """Return the filled numbers among ``keys``; an empty field is not stored."""
    return {
        key: _stored_number(key, user_input[key])
        for key in keys
        if user_input.get(key) is not None
    }


def timeout_error(values: Mapping[str, Any]) -> dict[str, str]:
    """Return the form error of a sensor timeout that is neither 0 nor in bounds."""
    timeout = values.get(CONF_SENSOR_TIMEOUT)
    if timeout and not _TIMEOUT_MIN <= timeout <= _TIMEOUT_MAX:
        return {CONF_SENSOR_TIMEOUT: ERR_TIMEOUT_RANGE}
    return {}


def setpoint_range_error(
    options: Mapping[str, Any], room: Mapping[str, Any]
) -> dict[str, str]:
    """Return the form error of a setpoint range whose minimum is not below its maximum.

    The range is resolved like the room does: its own value, else the global
    one, else the built-in default. Pass the room's values as ``room`` and the
    global options as ``options``; to check the global pair alone, pass ``{}``
    as ``options`` and the submitted options as ``room``. The error is not tied
    to a field, because either bound may come from another layer.
    """
    merged = {**DEFAULTS, **overrides(options), **overrides(room)}
    if merged[CONF_SETPOINT_MIN] >= merged[CONF_SETPOINT_MAX]:
        return {"base": ERR_SETPOINT_RANGE}
    return {}


async def setting_texts(
    hass: HomeAssistant, values: Mapping[str, Any]
) -> dict[str, str]:
    """Render setting values as plain text in the language of Home Assistant.

    Selects use the labels of their selector, numbers get their unit and, in
    German, a decimal comma. A timeout or window time of 0 and an outdoor cut-off
    that is not set read as "off".
    """
    texts: dict[str, str] = {}
    for key, value in values.items():
        not_set = value is None
        is_off = not_set or (key in _OFF_AT_ZERO and value == 0)
        if key in NUMBERS and not is_off:
            text = f"{value:.1f}" if isinstance(value, float) else str(value)
            text = localized_number(hass, text)
            unit = NUMBERS[key].unit
            texts[key] = text if unit is None else f"{text} {unit}"
        else:
            name, option = (key, str(value))
            if is_off:
                name, option = (
                    "outdoor_cutoff" if not_set else "sensor_timeout",
                    "off",
                )
            texts[key] = (await async_option_labels(hass, name)).get(option, option)
    return texts


def effective_settings(
    options: Mapping[str, Any], keys: Sequence[str]
) -> dict[str, Any]:
    """Return the saved global value of each setting, else its built-in default."""
    values = {**DEFAULTS, **overrides(options)}
    return {
        key: _stored_number(key, values[key])
        if key in NUMBERS and values[key] is not None
        else values[key]
        for key in keys
    }


def room_settings_schema(profiles: Mapping[str, Profile]) -> vol.Schema:
    """Return the room's settings form: "global" or a value, empty = global."""
    profile_options = [
        SelectOptionDict(value=SETTING_GLOBAL, label="Global"),
        SelectOptionDict(value=PROFILE_AUTO, label="Auto"),
        *(
            SelectOptionDict(value=profile_id, label=profile.name)
            for profile_id, profile in profiles.items()
        ),
    ]
    schema: dict[Any, Any] = {}
    for key in ROOM_SETTING_KEYS:
        if key == CONF_PROFILE:
            schema[vol.Optional(key, default=SETTING_GLOBAL)] = _select_selector(
                key, profile_options
            )
        elif key in ROOM_SELECT_VALUES:
            schema[vol.Optional(key, default=SETTING_GLOBAL)] = _select_selector(
                key, [SETTING_GLOBAL, *ROOM_SELECT_VALUES[key]]
            )
        else:
            schema[vol.Optional(key)] = _number_selector(key)
    return vol.Schema(schema)


def room_settings(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return what a room stores of its settings: the selects and filled numbers."""
    return {
        **{key: user_input[key] for key in ROOM_SELECT_KEYS},
        **stored_numbers(user_input, ROOM_NUMBER_KEYS),
    }


# --- The options form --------------------------------------------------


def options_schema(current: Mapping[str, Any]) -> vol.Schema:
    """Return the form of the global options in four collapsed sections.

    The selects default to what is saved (else the built-in default). The
    numbers get no default: they are suggested by ``options_suggestions``, and
    an empty number is not stored, so the built-in default applies.
    """
    effective = {**DEFAULTS, **overrides(current)}

    def group(keys: Sequence[str]) -> section:
        fields: dict[Any, Any] = {}
        for key in keys:
            if key in SELECT_VALUES:
                fields[vol.Required(key, default=effective[key])] = _select_selector(
                    key, list(SELECT_VALUES[key])
                )
            elif key == CONF_OUTDOOR_SENSOR:
                fields[vol.Optional(key)] = _outdoor_sensor_selector()
            else:
                fields[vol.Optional(key)] = _number_selector(key)
        return section(vol.Schema(fields), {"collapsed": True})

    return vol.Schema(
        {vol.Required(name): group(keys) for name, keys in SECTIONS.items()}
    )


def options_suggestions(current: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the numbers and the sensor the options form shows.

    A number shows its saved value, else its built-in default; the sensor and
    the outdoor cut-off only when they are saved.
    """
    shown = {
        **effective_settings(current, list(NUMBERS)),
        CONF_OUTDOOR_SENSOR: current.get(CONF_OUTDOOR_SENSOR),
    }
    return {
        name: {
            key: shown[key]
            for key in keys
            if (key in NUMBERS or key == CONF_OUTDOOR_SENSOR)
            and shown.get(key) is not None
        }
        for name, keys in SECTIONS.items()
    }


def stored_options(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return what the options store of a submitted form.

    The selects, the filled numbers that differ from their built-in default and
    the outdoor sensor if there is one. A number left at its default is not
    stored, so it follows a later change of the default. The sections only lay
    the form out; the options are stored flat.
    """
    flat = {key: value for name in SECTIONS for key, value in user_input[name].items()}
    numbers = stored_numbers(flat, list(NUMBERS))
    options = {
        **{key: flat[key] for key in SELECT_VALUES},
        **{key: value for key, value in numbers.items() if value != DEFAULTS[key]},
    }
    if flat.get(CONF_OUTDOOR_SENSOR):
        options[CONF_OUTDOOR_SENSOR] = flat[CONF_OUTDOOR_SENSOR]
    return options
