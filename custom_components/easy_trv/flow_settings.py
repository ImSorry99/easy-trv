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
    BooleanSelector,
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
    CONF_AWAY_ACTION,
    CONF_AWAY_DELAY,
    CONF_AWAY_SETBACK,
    CONF_FROST_TEMPERATURE,
    CONF_HIDE_THERMOSTAT_ENTITIES,
    CONF_HYSTERESIS,
    CONF_MAX_ATTEMPTS,
    CONF_MIN_DELTA,
    CONF_OUTDOOR_CUTOFF,
    CONF_OUTDOOR_CUTOFF_CELSIUS,
    CONF_OUTDOOR_SENSOR,
    CONF_PRESENCE,
    CONF_PRESENCE_ENTITY,
    CONF_PRESENCE_UNKNOWN,
    CONF_PROFILE,
    CONF_REFRESH_MINUTES,
    CONF_RESET_SETTINGS,
    CONF_SCHEDULE_SETBACK,
    CONF_SENSOR_TIMEOUT,
    CONF_SETPOINT_MAX,
    CONF_SETPOINT_MIN,
    CONF_SETPOINT_RANGE,
    CONF_STATISTICS,
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
    from collections.abc import Collection, Mapping, Sequence

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
    CONF_STATISTICS: ("off", "hidden", "visible"),
    CONF_PRESENCE_UNKNOWN: ("away", "home"),
    CONF_AWAY_ACTION: ("setback", "off"),
}
# A choice that only a room has: the global outdoor cut-off threshold is the
# value to keep ("global") or to ignore ("off") in this room.
ROOM_SELECT_VALUES: Final[Mapping[str, tuple[str, ...]]] = {
    **SELECT_VALUES,
    CONF_OUTDOOR_CUTOFF: ("off",),
    # The room's presence: the global entity, none, or its own entity.
    CONF_PRESENCE: ("none", "own"),
}
# The domains a presence entity may have.
PRESENCE_DOMAINS: Final = (
    "person",
    "device_tracker",
    "zone",
    "group",
    "binary_sensor",
    "input_boolean",
)


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
    CONF_AWAY_SETBACK: NumberSpec(0.5, 10.0, 0.5, UnitOfTemperature.KELVIN),
    CONF_AWAY_DELAY: NumberSpec(0, 120, unit=UnitOfTime.MINUTES),
    # Up to 15 K, so a room can go down to frost protection (never below it).
    CONF_SCHEDULE_SETBACK: NumberSpec(0.5, 15.0, 0.5, UnitOfTemperature.KELVIN),
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
_SECTION_PRESENCE: Final = "presence"
_SECTION_SCHEDULE: Final = "schedule"
_SECTION_STATISTICS: Final = "statistics"
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
    _SECTION_PRESENCE: (
        CONF_PRESENCE_ENTITY,
        CONF_PRESENCE_UNKNOWN,
        CONF_AWAY_ACTION,
        CONF_AWAY_SETBACK,
        CONF_AWAY_DELAY,
    ),
    _SECTION_SCHEDULE: (CONF_SCHEDULE_SETBACK,),
    _SECTION_STATISTICS: (CONF_STATISTICS,),
}
# The entity pickers of the options form; stored only when one is chosen.
_OPTION_ENTITIES: Final = (CONF_OUTDOOR_SENSOR, CONF_PRESENCE_ENTITY)
# How the room's settings form groups its fields, in the order of the form:
# what is changed often first, the write tuning last. The profile stands above
# the sections, the reset switch below them; the stored data stays flat.
ROOM_SECTIONS: Final[Mapping[str, tuple[str, ...]]] = {
    _SECTION_PRESENCE: (
        CONF_PRESENCE,
        CONF_PRESENCE_ENTITY,
        CONF_PRESENCE_UNKNOWN,
        CONF_AWAY_ACTION,
        CONF_AWAY_SETBACK,
    ),
    _SECTION_SCHEDULE: (CONF_SCHEDULE_SETBACK,),
    _SECTION_WINDOWS_OUTDOOR: (
        CONF_WINDOW_OPEN_DELAY,
        CONF_WINDOW_WARN_MINUTES,
        CONF_WINDOW_WARN_COLDER,
        CONF_WINDOW_CHECK_MINUTES,
        CONF_WINDOW_CHECK_DROP,
        CONF_OUTDOOR_CUTOFF,
        CONF_OUTDOOR_CUTOFF_CELSIUS,
    ),
    _SECTION_ROOM_THERMOSTAT: (
        CONF_SETPOINT_RANGE,
        CONF_SETPOINT_MIN,
        CONF_SETPOINT_MAX,
    ),
    _SECTION_THERMOSTAT: (
        CONF_WINDOW_DETECTION,
        CONF_HYSTERESIS,
        CONF_ADAPTIVE_MODE,
        CONF_FROST_TEMPERATURE,
        CONF_HIDE_THERMOSTAT_ENTITIES,
    ),
    _SECTION_STATISTICS: (CONF_STATISTICS,),
    _SECTION_WRITES: (
        CONF_REFRESH_MINUTES,
        CONF_VERIFY_DELAY,
        CONF_MAX_ATTEMPTS,
        CONF_MIN_DELTA,
        CONF_SENSOR_TIMEOUT,
    ),
}
# Every setting of a room, in the order of the form.
ROOM_SETTING_KEYS: Final = (
    CONF_PROFILE,
    *(key for keys in ROOM_SECTIONS.values() for key in keys),
)
ROOM_SELECT_KEYS: Final = (
    CONF_PROFILE,
    *(key for key in ROOM_SETTING_KEYS if key in ROOM_SELECT_VALUES),
)
ROOM_NUMBER_KEYS: Final = tuple(key for key in ROOM_SETTING_KEYS if key in NUMBERS)

ERR_TIMEOUT_RANGE: Final = "timeout_range"
ERR_SETPOINT_RANGE: Final = "setpoint_range"
ERR_PRESENCE_ENTITY_REQUIRED: Final = "presence_entity_required"

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


def _presence_selector() -> EntitySelector:
    """Return the picker of a presence entity."""
    return EntitySelector(EntitySelectorConfig(domain=list(PRESENCE_DOMAINS)))


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


def room_settings_schema(
    profiles: Mapping[str, Profile],
    *,
    with_reset: bool = False,
    expanded: Collection[str] = (),
) -> vol.Schema:
    """Return the room's settings form: "global" or a value, empty = global.

    The profile comes first, the other settings sit in collapsed sections;
    the sections named in ``expanded`` are open. ``with_reset`` ends the form
    with the switch that sets everything to global.
    """
    profile_options = [
        SelectOptionDict(value=SETTING_GLOBAL, label="Global"),
        SelectOptionDict(value=PROFILE_AUTO, label="Auto"),
        *(
            SelectOptionDict(value=profile_id, label=profile.name)
            for profile_id, profile in profiles.items()
        ),
    ]

    def field(key: str) -> tuple[Any, Any]:
        if key in ROOM_SELECT_VALUES:
            return vol.Optional(key, default=SETTING_GLOBAL), _select_selector(
                key, [SETTING_GLOBAL, *ROOM_SELECT_VALUES[key]]
            )
        if key == CONF_PRESENCE_ENTITY:
            return vol.Optional(key), _presence_selector()
        return vol.Optional(key), _number_selector(key)

    schema: dict[Any, Any] = {
        vol.Optional(CONF_PROFILE, default=SETTING_GLOBAL): _select_selector(
            CONF_PROFILE, profile_options
        )
    }
    for name, keys in ROOM_SECTIONS.items():
        schema[vol.Required(name)] = section(
            vol.Schema(dict(field(key) for key in keys)),
            {"collapsed": name not in expanded},
        )
    if with_reset:
        schema[vol.Optional(CONF_RESET_SETTINGS, default=False)] = BooleanSelector()
    return vol.Schema(schema)


def room_sections_in_use(data: Mapping[str, Any]) -> set[str]:
    """Return the sections in which a room has a value of its own.

    The form opens them, so a room shows at once what it does differently.
    """
    own = {
        key
        for key, value in data.items()
        if value is not None and value != SETTING_GLOBAL
    }
    return {name for name, keys in ROOM_SECTIONS.items() if own & set(keys)}


def room_form_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return the flat stored data of a room as the sectioned values of its form."""
    values: dict[str, Any] = {
        key: data[key] for key in (CONF_PROFILE, CONF_RESET_SETTINGS) if key in data
    }
    for name, keys in ROOM_SECTIONS.items():
        values[name] = {key: data[key] for key in keys if key in data}
    return values


def flat_room_input(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return the submitted room form without its sections."""
    flat = {key: value for key, value in user_input.items() if key not in ROOM_SECTIONS}
    for name in ROOM_SECTIONS:
        flat.update(user_input.get(name, {}))
    return flat


def section_errors(
    errors: Mapping[str, str], sections: Mapping[str, Sequence[str]]
) -> dict[str, str]:
    """Return ``errors`` with each field inside a section keyed by the section.

    Home Assistant shows the error of a field inside a section nowhere; the
    error of the section shows above it.
    """
    owner = {key: name for name, keys in sections.items() for key in keys}
    return {owner.get(key, key): error for key, error in errors.items()}


def room_settings(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return what a room stores of its settings: the selects and filled numbers.

    ``user_input`` is flat (see ``flat_room_input``). The room's own presence
    entity is kept only with ``presence: own``. The reset switch sets every
    setting to global; where the room's presence comes from is an assignment
    and stays.
    """
    if user_input.get(CONF_RESET_SETTINGS):
        user_input = {
            **dict.fromkeys(ROOM_SELECT_KEYS, SETTING_GLOBAL),
            **{
                key: user_input[key]
                for key in (CONF_PRESENCE, CONF_PRESENCE_ENTITY)
                if key in user_input
            },
        }
    settings = {
        **{key: user_input[key] for key in ROOM_SELECT_KEYS},
        **stored_numbers(user_input, ROOM_NUMBER_KEYS),
    }
    if user_input.get(CONF_PRESENCE) == "own" and user_input.get(CONF_PRESENCE_ENTITY):
        settings[CONF_PRESENCE_ENTITY] = user_input[CONF_PRESENCE_ENTITY]
    return settings


def presence_error(user_input: Mapping[str, Any]) -> dict[str, str]:
    """Return the form error of a room set to its own presence without an entity."""
    if user_input.get(CONF_PRESENCE) == "own" and not user_input.get(
        CONF_PRESENCE_ENTITY
    ):
        return {CONF_PRESENCE_ENTITY: ERR_PRESENCE_ENTITY_REQUIRED}
    return {}


# --- The options form --------------------------------------------------


def options_schema(
    current: Mapping[str, Any], *, expanded: Collection[str] = ()
) -> vol.Schema:
    """Return the form of the global options in collapsed sections.

    The sections named in ``expanded`` (those with an error) are open.

    The selects default to what is saved (else the built-in default). The
    numbers get no default: they are suggested by ``options_suggestions``, and
    an empty number is not stored, so the built-in default applies.
    """
    effective = {**DEFAULTS, **overrides(current)}

    def group(name: str, keys: Sequence[str]) -> section:
        fields: dict[Any, Any] = {}
        for key in keys:
            if key in SELECT_VALUES:
                fields[vol.Required(key, default=effective[key])] = _select_selector(
                    key, list(SELECT_VALUES[key])
                )
            elif key == CONF_OUTDOOR_SENSOR:
                fields[vol.Optional(key)] = _outdoor_sensor_selector()
            elif key == CONF_PRESENCE_ENTITY:
                fields[vol.Optional(key)] = _presence_selector()
            else:
                fields[vol.Optional(key)] = _number_selector(key)
        return section(vol.Schema(fields), {"collapsed": name not in expanded})

    form: dict[Any, Any] = {
        vol.Required(name): group(name, keys) for name, keys in SECTIONS.items()
    }
    form[vol.Optional(CONF_RESET_SETTINGS, default=False)] = BooleanSelector()
    return vol.Schema(form)


def options_suggestions(current: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the numbers and the entities the options form shows.

    A number shows its saved value, else its built-in default; the entities and
    the outdoor cut-off only when they are saved.
    """
    shown = {
        **effective_settings(current, list(NUMBERS)),
        **{key: current.get(key) for key in _OPTION_ENTITIES},
    }
    return {
        name: {
            key: shown[key]
            for key in keys
            if (key in NUMBERS or key in _OPTION_ENTITIES)
            and shown.get(key) is not None
        }
        for name, keys in SECTIONS.items()
    }


def stored_options(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return what the options store of a submitted form.

    The selects, the filled numbers that differ from their built-in default and
    the outdoor sensor if there is one. A number left at its default is not
    stored, so it follows a later change of the default. The sections only lay
    the form out; the options are stored flat. The reset switch keeps only the
    entities: every setting goes back to its built-in default.
    """
    flat = {key: value for name in SECTIONS for key, value in user_input[name].items()}
    if user_input.get(CONF_RESET_SETTINGS):
        return {key: flat[key] for key in _OPTION_ENTITIES if flat.get(key)}
    numbers = stored_numbers(flat, list(NUMBERS))
    options = {
        **{key: flat[key] for key in SELECT_VALUES},
        **{key: value for key, value in numbers.items() if value != DEFAULTS[key]},
    }
    for key in _OPTION_ENTITIES:
        if flat.get(key):
            options[key] = flat[key]
    return options
