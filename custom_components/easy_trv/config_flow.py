"""Config flow of Easy TRV: the entry, its room subentries and its options."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    AreaSelector,
    AreaSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from custom_components.easy_trv.adapters.zha_gateway import (
    ZHA_DOMAIN,
    DeviceRef,
    NotZhaEntity,
    resolve_device,
)
from custom_components.easy_trv.const import (
    CONF_ADAPTIVE_MODE,
    CONF_AREA,
    CONF_FROST_TEMPERATURE,
    CONF_HUMIDITY_SENSORS,
    CONF_HYSTERESIS,
    CONF_MAX_ATTEMPTS,
    CONF_MIN_DELTA,
    CONF_NAME,
    CONF_PROFILE,
    CONF_REFRESH_MINUTES,
    CONF_SENSOR_TIMEOUT,
    CONF_TEMP_SENSORS,
    CONF_TRVS,
    CONF_VERIFY_DELAY,
    CONF_WINDOW_DETECTION,
    DOMAIN,
    PROFILE_AUTO,
    SETTING_GLOBAL,
    SUBENTRY_ROOM,
)
from custom_components.easy_trv.core.settings import (
    DEFAULTS,
    overrides,
    resolve_settings,
)
from custom_components.easy_trv.profiles.loader import async_load_profiles

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import HomeAssistant

    from custom_components.easy_trv.profiles.engine import Profile

CLIMATE_DOMAIN: Final = "climate"

# The values double as translation keys, which must not contain a dot: 0_4 = 0.4 K.
_SELECT_VALUES: Final[Mapping[str, tuple[str, ...]]] = {
    CONF_WINDOW_DETECTION: ("on", "off", "leave"),
    CONF_HYSTERESIS: ("0_2", "0_4", "0_6", "0_8", "1_0"),
    CONF_ADAPTIVE_MODE: ("on", "off", "leave"),
}


@dataclass(frozen=True)
class _NumberSpec:
    """The bounds and unit of a numeric setting."""

    minimum: float
    maximum: float
    step: float = 1
    unit: str | None = None


_NUMBERS: Final[Mapping[str, _NumberSpec]] = {
    CONF_REFRESH_MINUTES: _NumberSpec(1, 120, unit=UnitOfTime.MINUTES),
    CONF_VERIFY_DELAY: _NumberSpec(2, 60, unit=UnitOfTime.SECONDS),
    CONF_MAX_ATTEMPTS: _NumberSpec(1, 10),
    CONF_MIN_DELTA: _NumberSpec(0.0, 1.0, 0.1, UnitOfTemperature.KELVIN),
    CONF_SENSOR_TIMEOUT: _NumberSpec(0, 1440, unit=UnitOfTime.MINUTES),
    CONF_FROST_TEMPERATURE: _NumberSpec(4.0, 16.0, 0.5, UnitOfTemperature.CELSIUS),
}

# How the options form groups its fields (the stored options stay flat).
_SECTION_WRITES: Final = "writes"
_SECTION_THERMOSTAT: Final = "thermostat"
_SECTIONS: Final[Mapping[str, tuple[str, ...]]] = {
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
    ),
}
# The order of the room's settings form.
_ROOM_SETTING_KEYS: Final = (
    CONF_PROFILE,
    CONF_WINDOW_DETECTION,
    CONF_HYSTERESIS,
    CONF_ADAPTIVE_MODE,
    CONF_FROST_TEMPERATURE,
    CONF_REFRESH_MINUTES,
    CONF_VERIFY_DELAY,
    CONF_MAX_ATTEMPTS,
    CONF_MIN_DELTA,
    CONF_SENSOR_TIMEOUT,
)
_ROOM_SELECT_KEYS: Final = (
    CONF_PROFILE,
    *(key for key in _ROOM_SETTING_KEYS if key in _SELECT_VALUES),
)
_ROOM_NUMBER_KEYS: Final = tuple(key for key in _ROOM_SETTING_KEYS if key in _NUMBERS)

_STEP_USER: Final = "user"
_STEP_FROM_AREA: Final = "from_area"
_STEP_CUSTOM: Final = "custom"
_STEP_ENTITIES: Final = "entities"
_STEP_SETTINGS: Final = "settings"
_STEP_RECONFIGURE: Final = "reconfigure"

_ERR_NO_TRV: Final = "no_trv"
_ERR_NOT_ZHA: Final = "not_zha"
_ERR_NO_PROFILE: Final = "no_profile"
_ERR_PROFILE_MISMATCH: Final = "profile_mismatch"
_ERR_TRV_IN_OTHER_ROOM: Final = "trv_in_other_room"
_ERR_NAME_EXISTS: Final = "name_exists"
_ERR_NAME_REQUIRED: Final = "name_required"
_ERR_TIMEOUT_RANGE: Final = "timeout_range"

# The sensor timeout is either off (0) or within these bounds (minutes).
_TIMEOUT_MIN: Final = 10
_TIMEOUT_MAX: Final = 1440

# What the room's entities form submits, besides the name.
_ENTITY_KEYS: Final = (CONF_TRVS, CONF_TEMP_SENSORS, CONF_HUMIDITY_SENSORS)


# --- Room helpers -----------------------------------------------------------


def prefill_from_area(hass: HomeAssistant, area_id: str) -> dict[str, list[str]]:
    """Suggest the entities of a room from the entities of an area.

    An entity is in an area through its own area or, if it has none, through
    its device. ``trvs`` are the ZHA climate entities. ``temp_sensors`` and
    ``humidity_sensors`` are the enabled sensors of the matching device class,
    except those of a thermostat's own device (its built-in readings are not
    room sensors). Every list is sorted by entity id.
    """
    entity_registry = er.async_get(hass)
    entries = {
        entry.entity_id: entry
        for entry in er.async_entries_for_area(entity_registry, area_id)
        if entry.disabled_by is None
    }
    for device in dr.async_entries_for_area(dr.async_get(hass), area_id):
        for entry in er.async_entries_for_device(entity_registry, device.id):
            # An entity can sit in another area than its device.
            if entry.area_id in (None, area_id):
                entries.setdefault(entry.entity_id, entry)

    trvs = [
        entry
        for entry in entries.values()
        if entry.domain == CLIMATE_DOMAIN and entry.platform == ZHA_DOMAIN
    ]
    trv_devices = {entry.device_id for entry in trvs if entry.device_id is not None}

    def sensors(device_class: str) -> list[str]:
        return sorted(
            entry.entity_id
            for entry in entries.values()
            if entry.domain == "sensor"
            and entry.device_id not in trv_devices
            and (entry.device_class or entry.original_device_class) == device_class
        )

    return {
        CONF_TRVS: sorted(entry.entity_id for entry in trvs),
        CONF_TEMP_SENSORS: sensors("temperature"),
        CONF_HUMIDITY_SENSORS: sensors("humidity"),
    }


def _profile_error(
    profiles: Mapping[str, Profile],
    choice: str,
    refs: list[DeviceRef],
) -> dict[str, str]:
    """Check the profile choice against the resolved thermostats."""

    def matches_all(profile: Profile) -> bool:
        return all(profile.matches(ref.manufacturer, ref.model) for ref in refs)

    if choice == PROFILE_AUTO:
        if any(matches_all(profile) for profile in profiles.values()):
            return {}
        return {CONF_PROFILE: _ERR_NO_PROFILE}
    selected = profiles.get(choice)
    if selected is None or not matches_all(selected):
        return {CONF_PROFILE: _ERR_PROFILE_MISMATCH}
    return {}


def validate_room(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry_id: str | None,
    user_input: Mapping[str, Any],
    profiles: Mapping[str, Profile],
) -> dict[str, str]:
    """Return the form errors of a room, keyed by field (empty if it is valid).

    Thermostat problems are reported on ``trvs`` and checked in this order: none
    chosen, not a ZHA thermostat, already part of another room. Only if the
    thermostats are fine does the profile get checked (field ``profile``).
    ``subentry_id`` is the room being edited, which does not count as "another
    room".
    """
    trvs: list[str] = list(user_input.get(CONF_TRVS, ()))
    if not trvs:
        return {CONF_TRVS: _ERR_NO_TRV}

    refs: list[DeviceRef] = []
    for entity_id in trvs:
        # The entity picker only filters in the browser, so a posted entity can
        # be any ZHA entity (a switch of a thermostat, for instance).
        if entity_id.partition(".")[0] != CLIMATE_DOMAIN:
            return {CONF_TRVS: _ERR_NOT_ZHA}
        try:
            refs.append(resolve_device(hass, entity_id))
        except NotZhaEntity:
            return {CONF_TRVS: _ERR_NOT_ZHA}

    taken = {
        entity_id
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_ROOM
        and subentry.subentry_id != subentry_id
        for entity_id in subentry.data.get(CONF_TRVS, ())
    }
    if taken.intersection(trvs):
        return {CONF_TRVS: _ERR_TRV_IN_OTHER_ROOM}

    return _profile_error(profiles, user_input.get(CONF_PROFILE, PROFILE_AUTO), refs)


def _entity_fields() -> dict[Any, Any]:
    """Return the schema fields of a room's thermostats and sensors."""
    return {
        vol.Required(CONF_TRVS): EntitySelector(
            EntitySelectorConfig(
                multiple=True,
                filter=[{"integration": ZHA_DOMAIN, "domain": CLIMATE_DOMAIN}],
            )
        ),
        vol.Optional(CONF_TEMP_SENSORS, default=[]): EntitySelector(
            EntitySelectorConfig(
                multiple=True,
                reorder=True,
                filter=[{"domain": "sensor", "device_class": "temperature"}],
            )
        ),
        vol.Optional(CONF_HUMIDITY_SENSORS, default=[]): EntitySelector(
            EntitySelectorConfig(
                multiple=True,
                filter=[{"domain": "sensor", "device_class": "humidity"}],
            )
        ),
    }


# --- Settings fields (shared by the options flow and the room's settings step) ---


def _number_selector(key: str) -> NumberSelector:
    """Return the box input of a numeric setting."""
    spec = _NUMBERS[key]
    config = NumberSelectorConfig(
        min=spec.minimum,
        max=spec.maximum,
        step=spec.step,
        mode=NumberSelectorMode.BOX,
    )
    if spec.unit is not None:
        config["unit_of_measurement"] = spec.unit
    return NumberSelector(config)


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


def _stored_numbers(
    user_input: Mapping[str, Any], keys: Sequence[str]
) -> dict[str, Any]:
    """Return the filled numbers among ``keys``; an empty field is not stored."""
    return {
        key: _stored_number(key, user_input[key])
        for key in keys
        if user_input.get(key) is not None
    }


def _timeout_error(values: Mapping[str, Any]) -> dict[str, str]:
    """Return the form error of a sensor timeout that is neither 0 nor in bounds."""
    timeout = values.get(CONF_SENSOR_TIMEOUT)
    if timeout and not _TIMEOUT_MIN <= timeout <= _TIMEOUT_MAX:
        return {CONF_SENSOR_TIMEOUT: _ERR_TIMEOUT_RANGE}
    return {}


def _global_placeholders(options: Mapping[str, Any]) -> dict[str, str]:
    """Return the current global value of every setting as plain text."""
    settings = resolve_settings(options, {})
    texts: dict[str, str] = {
        CONF_PROFILE: settings.profile,
        CONF_WINDOW_DETECTION: settings.window_detection,
        CONF_ADAPTIVE_MODE: settings.adaptive_mode,
        CONF_HYSTERESIS: f"{settings.hysteresis:.1f} {UnitOfTemperature.KELVIN}",
    }
    for key, spec in _NUMBERS.items():
        value = getattr(settings, key)
        text = f"{value:.1f}" if isinstance(value, float) else str(value)
        texts[key] = text if spec.unit is None else f"{text} {spec.unit}"
    return {f"global_{key}": text for key, text in texts.items()}


def _room_settings_schema(profiles: Mapping[str, Profile]) -> vol.Schema:
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
    for key in _ROOM_SETTING_KEYS:
        if key == CONF_PROFILE:
            schema[vol.Optional(key, default=SETTING_GLOBAL)] = _select_selector(
                key, profile_options
            )
        elif key in _SELECT_VALUES:
            schema[vol.Optional(key, default=SETTING_GLOBAL)] = _select_selector(
                key, [SETTING_GLOBAL, *_SELECT_VALUES[key]]
            )
        else:
            schema[vol.Optional(key)] = _number_selector(key)
    return vol.Schema(schema)


def _room_settings(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return what a room stores of its settings: the selects and filled numbers."""
    return {
        **{key: user_input[key] for key in _ROOM_SELECT_KEYS},
        **_stored_numbers(user_input, _ROOM_NUMBER_KEYS),
    }


# --- Config flow ------------------------------------------------------------


class EasyTrvConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the one Easy TRV entry; rooms are added as subentries."""

    VERSION = 2

    async def async_step_user(
        self, _user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry straight away: there is nothing to ask."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Easy TRV", data={})

    @staticmethod
    @callback
    def async_get_options_flow(_config_entry: ConfigEntry) -> EasyTrvOptionsFlow:
        """Return the options flow."""
        return EasyTrvOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, _config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry flows: a room."""
        return {SUBENTRY_ROOM: RoomSubentryFlow}


# --- Room subentry flow -----------------------------------------------------


class RoomSubentryFlow(ConfigSubentryFlow):
    """Add a room (from an area or custom) and edit it later."""

    def __init__(self) -> None:
        """Start without a name, an area or loaded profiles."""
        self._name: str = ""
        self._area_id: str | None = None
        self._suggested: dict[str, list[str]] = {}
        self._entities: dict[str, Any] = {}
        self._profiles: Mapping[str, Profile] | None = None

    async def async_step_user(
        self, _user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask whether the room comes from an area or is custom."""
        return self.async_show_menu(
            step_id=_STEP_USER, menu_options=[_STEP_FROM_AREA, _STEP_CUSTOM]
        )

    async def async_step_from_area(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the area: the room takes its name and suggests its entities."""
        errors: dict[str, str] = {}
        if user_input is not None:
            area = ar.async_get(self.hass).async_get_area(user_input[CONF_AREA])
            if area is not None:
                if self._name_taken(area.name, None):
                    errors[CONF_AREA] = _ERR_NAME_EXISTS
                else:
                    self._name = area.name
                    self._area_id = area.id
                    self._suggested = prefill_from_area(self.hass, area.id)
                    return await self.async_step_entities()
        schema = vol.Schema(
            {
                vol.Required(CONF_AREA): AreaSelector(
                    AreaSelectorConfig(
                        entity=[{"integration": ZHA_DOMAIN, "domain": CLIMATE_DOMAIN}]
                    )
                )
            }
        )
        return self.async_show_form(
            step_id=_STEP_FROM_AREA, data_schema=schema, errors=errors
        )

    async def async_step_custom(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask for the name of a room that does not come from an area."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = _ERR_NAME_REQUIRED
            elif self._name_taken(name, None):
                errors[CONF_NAME] = _ERR_NAME_EXISTS
            else:
                self._name = name
                return await self.async_step_entities()
        schema = vol.Schema({vol.Required(CONF_NAME): TextSelector()})
        return self.async_show_form(
            step_id=_STEP_CUSTOM,
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose the thermostats and sensors of the new room."""
        profiles = await self._async_profiles()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_room(
                self.hass, self._get_entry(), None, user_input, profiles
            )
            if not errors:
                self._entities = {key: user_input[key] for key in _ENTITY_KEYS}
                return await self.async_step_settings()
        schema = vol.Schema(_entity_fields())
        return self.async_show_form(
            step_id=_STEP_ENTITIES,
            data_schema=self.add_suggested_values_to_schema(
                schema, self._suggested if user_input is None else user_input
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit the name, thermostats and sensors of a room, then its settings."""
        subentry = self._get_reconfigure_subentry()
        entry = self._get_entry()
        profiles = await self._async_profiles()
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name:
                errors[CONF_NAME] = _ERR_NAME_REQUIRED
            elif self._name_taken(name, subentry.subentry_id):
                errors[CONF_NAME] = _ERR_NAME_EXISTS
            else:
                errors = validate_room(
                    self.hass, entry, subentry.subentry_id, user_input, profiles
                )
            if not errors:
                self._name = name
                self._entities = {key: user_input[key] for key in _ENTITY_KEYS}
                return await self.async_step_settings()
        schema = vol.Schema(
            {vol.Required(CONF_NAME): TextSelector(), **_entity_fields()}
        )
        return self.async_show_form(
            step_id=_STEP_RECONFIGURE,
            data_schema=self.add_suggested_values_to_schema(
                schema, dict(subentry.data) if user_input is None else user_input
            ),
            errors=errors,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Override the global settings for this room; ``global`` keeps them."""
        entry = self._get_entry()
        reconfiguring = self.source == SOURCE_RECONFIGURE
        subentry = self._get_reconfigure_subentry() if reconfiguring else None
        profiles = await self._async_profiles()
        errors: dict[str, str] = {}
        if user_input is not None:
            settings = _room_settings(user_input)
            errors = _timeout_error(settings) or self._profile_errors(
                entry, subentry, settings[CONF_PROFILE], profiles
            )
            if not errors:
                data = {
                    CONF_NAME: self._name,
                    CONF_AREA: subentry.data.get(CONF_AREA)
                    if subentry
                    else self._area_id,
                    **self._entities,
                    **settings,
                }
                if subentry is None:
                    return self.async_create_entry(
                        title=self._name,
                        data=data,
                        unique_id=self._name.casefold(),
                    )
                # The update listener of the entry reloads it. The data is
                # replaced, not merged: a number the user emptied must go.
                return self.async_update_and_abort(
                    entry,
                    subentry,
                    data=data,
                    title=self._name,
                    unique_id=self._name.casefold(),
                )
        suggested = user_input
        if user_input is None and subentry is not None:
            suggested = dict(subentry.data)
        return self.async_show_form(
            step_id=_STEP_SETTINGS,
            data_schema=self.add_suggested_values_to_schema(
                _room_settings_schema(profiles), suggested
            ),
            errors=errors,
            description_placeholders=_global_placeholders(entry.options),
        )

    def _profile_errors(
        self,
        entry: ConfigEntry,
        subentry: ConfigSubentry | None,
        choice: str,
        profiles: Mapping[str, Profile],
    ) -> dict[str, str]:
        """Check the chosen profile against the thermostats of the room.

        ``global`` stands for the profile of the options. A problem with the
        thermostats themselves (they changed while the form was open) is shown
        as a general error, because their field is on another page.
        """
        if choice == SETTING_GLOBAL:
            choice = resolve_settings(entry.options, {}).profile
        errors = validate_room(
            self.hass,
            entry,
            subentry.subentry_id if subentry else None,
            {**self._entities, CONF_PROFILE: choice},
            profiles,
        )
        return {
            (key if key == CONF_PROFILE else "base"): error
            for key, error in errors.items()
        }

    def _name_taken(self, name: str, own_subentry_id: str | None) -> bool:
        """Return whether another room already has this name (any letter case)."""
        wanted = name.casefold()
        return any(
            subentry.subentry_type == SUBENTRY_ROOM
            and subentry.subentry_id != own_subentry_id
            and subentry.unique_id == wanted
            for subentry in self._get_entry().subentries.values()
        )

    async def _async_profiles(self) -> Mapping[str, Profile]:
        """Return the loaded profiles, from the running entry if there is one."""
        if self._profiles is None:
            entry = self._get_entry()
            if entry.state is ConfigEntryState.LOADED:
                self._profiles = entry.runtime_data.profiles.profiles
            else:
                self._profiles = (await async_load_profiles(self.hass)).profiles
        return self._profiles


# --- Options flow -----------------------------------------------------------


def _options_schema(current: Mapping[str, Any]) -> vol.Schema:
    """Return the form of the global options in two sections.

    The selects default to what is saved (else the built-in default). The
    numbers have no default: an empty number is not stored.
    """
    effective = {**DEFAULTS, **overrides(current)}

    def group(keys: Sequence[str]) -> section:
        fields: dict[Any, Any] = {}
        for key in keys:
            if key in _SELECT_VALUES:
                fields[vol.Required(key, default=effective[key])] = _select_selector(
                    key, list(_SELECT_VALUES[key])
                )
            else:
                fields[vol.Optional(key)] = _number_selector(key)
        return section(vol.Schema(fields), {"collapsed": False})

    return vol.Schema(
        {vol.Required(name): group(keys) for name, keys in _SECTIONS.items()}
    )


def _options_suggestions(current: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the numbers the options form shows: saved, else built-in default."""
    effective = {**DEFAULTS, **overrides(current)}
    return {
        name: {key: effective[key] for key in keys if key in _NUMBERS}
        for name, keys in _SECTIONS.items()
    }


class EasyTrvOptionsFlow(OptionsFlow):
    """Edit the options that apply to all rooms."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the global options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # The sections only lay the form out; the options are stored flat.
            flat = {
                key: value
                for name in _SECTIONS
                for key, value in user_input[name].items()
            }
            options = {
                **{key: flat[key] for key in _SELECT_VALUES},
                **_stored_numbers(flat, list(_NUMBERS)),
            }
            errors = _timeout_error(options)
            if not errors:
                return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _options_schema(self.config_entry.options),
                _options_suggestions(self.config_entry.options)
                if user_input is None
                else user_input,
            ),
            errors=errors,
        )
