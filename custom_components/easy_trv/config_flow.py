"""Config flow of Easy TRV: the entry, its room subentries and its options."""

from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import callback
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
    CONF_AREA,
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
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MIN_DELTA,
    DEFAULT_REFRESH_MINUTES,
    DEFAULT_SENSOR_TIMEOUT,
    DEFAULT_VERIFY_DELAY,
    DOMAIN,
    PROFILE_AUTO,
    SUBENTRY_ROOM,
)
from custom_components.easy_trv.profiles.loader import async_load_profiles

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.easy_trv.profiles.engine import Profile

WINDOW_DETECTION_OPTIONS: Final = ("on", "off", "leave")
# The values double as translation keys, which must not contain a dot: 0_4 = 0.4 K.
HYSTERESIS_OPTIONS: Final = ("leave", "0_2", "0_4", "0_6", "0_8", "1_0")

_STEP_USER: Final = "user"
_STEP_FROM_AREA: Final = "from_area"
_STEP_CUSTOM: Final = "custom"
_STEP_ENTITIES: Final = "entities"
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
_TIMEOUT_MAX: Final = 720

# What a room form submits, besides the name.
_ENTITY_KEYS: Final = (
    CONF_TRVS,
    CONF_TEMP_SENSORS,
    CONF_HUMIDITY_SENSORS,
    CONF_PROFILE,
    CONF_WINDOW_DETECTION,
    CONF_HYSTERESIS,
)


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
        if entry.domain == "climate" and entry.platform == ZHA_DOMAIN
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


def _entity_fields(profiles: Mapping[str, Profile]) -> dict[Any, Any]:
    """Return the schema fields of a room's devices and behaviour."""
    profile_options = [SelectOptionDict(value=PROFILE_AUTO, label="Auto")]
    profile_options.extend(
        SelectOptionDict(value=profile_id, label=profile.name)
        for profile_id, profile in profiles.items()
    )
    return {
        vol.Required(CONF_TRVS): EntitySelector(
            EntitySelectorConfig(
                multiple=True,
                filter=[{"integration": ZHA_DOMAIN, "domain": "climate"}],
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
        vol.Optional(CONF_PROFILE, default=PROFILE_AUTO): SelectSelector(
            SelectSelectorConfig(
                options=profile_options, mode=SelectSelectorMode.DROPDOWN
            )
        ),
        vol.Optional(CONF_WINDOW_DETECTION, default="on"): SelectSelector(
            SelectSelectorConfig(
                options=list(WINDOW_DETECTION_OPTIONS),
                translation_key=CONF_WINDOW_DETECTION,
            )
        ),
        vol.Optional(CONF_HYSTERESIS, default="leave"): SelectSelector(
            SelectSelectorConfig(
                options=list(HYSTERESIS_OPTIONS),
                mode=SelectSelectorMode.DROPDOWN,
                translation_key=CONF_HYSTERESIS,
            )
        ),
    }


# --- Config flow ------------------------------------------------------------


class EasyTrvConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the one Easy TRV entry; rooms are added as subentries."""

    VERSION = 1

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
                        entity=[{"integration": ZHA_DOMAIN, "domain": "climate"}]
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
        """Choose the thermostats, sensors and behaviour of the new room."""
        profiles = await self._async_profiles()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = validate_room(
                self.hass, self._get_entry(), None, user_input, profiles
            )
            if not errors:
                return self.async_create_entry(
                    title=self._name,
                    data={
                        CONF_NAME: self._name,
                        CONF_AREA: self._area_id,
                        **{key: user_input[key] for key in _ENTITY_KEYS},
                    },
                    unique_id=self._name.casefold(),
                )
        schema = vol.Schema(_entity_fields(profiles))
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
        """Edit the name, thermostats, sensors and behaviour of a room."""
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
                # The update listener of the entry reloads it.
                return self.async_update_and_abort(
                    entry,
                    subentry,
                    data_updates={**user_input, CONF_NAME: name},
                    title=name,
                    unique_id=name.casefold(),
                )
        schema = vol.Schema(
            {vol.Required(CONF_NAME): TextSelector(), **_entity_fields(profiles)}
        )
        return self.async_show_form(
            step_id=_STEP_RECONFIGURE,
            data_schema=self.add_suggested_values_to_schema(
                schema, dict(subentry.data) if user_input is None else user_input
            ),
            errors=errors,
        )

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
    """Return the form of the global options, defaulting to what is saved."""

    def number(  # noqa: PLR0913 - one call per option, all keyword options
        key: str,
        default: float,
        *,
        minimum: float,
        maximum: float,
        step: float,
        unit: str | None = None,
    ) -> dict[Any, Any]:
        config = NumberSelectorConfig(
            min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX
        )
        if unit is not None:
            config["unit_of_measurement"] = unit
        return {
            vol.Required(key, default=current.get(key, default)): NumberSelector(config)
        }

    return vol.Schema(
        {
            **number(
                CONF_REFRESH_MINUTES,
                DEFAULT_REFRESH_MINUTES,
                minimum=5,
                maximum=90,
                step=1,
                unit=UnitOfTime.MINUTES,
            ),
            **number(
                CONF_VERIFY_DELAY,
                DEFAULT_VERIFY_DELAY,
                minimum=2,
                maximum=60,
                step=1,
                unit=UnitOfTime.SECONDS,
            ),
            **number(
                CONF_MAX_ATTEMPTS,
                DEFAULT_MAX_ATTEMPTS,
                minimum=1,
                maximum=10,
                step=1,
            ),
            **number(
                CONF_MIN_DELTA,
                DEFAULT_MIN_DELTA,
                minimum=0.1,
                maximum=1.0,
                step=0.1,
                unit=UnitOfTemperature.KELVIN,
            ),
            **number(
                CONF_SENSOR_TIMEOUT,
                DEFAULT_SENSOR_TIMEOUT,
                minimum=0,
                maximum=_TIMEOUT_MAX,
                step=1,
                unit=UnitOfTime.MINUTES,
            ),
        }
    )


class EasyTrvOptionsFlow(OptionsFlow):
    """Edit the options that apply to all rooms."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the global options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # The selectors return floats; whole-number options are stored as ints.
            options: dict[str, float] = {
                CONF_REFRESH_MINUTES: round(user_input[CONF_REFRESH_MINUTES]),
                CONF_VERIFY_DELAY: round(user_input[CONF_VERIFY_DELAY]),
                CONF_MAX_ATTEMPTS: round(user_input[CONF_MAX_ATTEMPTS]),
                CONF_MIN_DELTA: round(user_input[CONF_MIN_DELTA], 1),
                CONF_SENSOR_TIMEOUT: round(user_input[CONF_SENSOR_TIMEOUT]),
            }
            timeout = options[CONF_SENSOR_TIMEOUT]
            if timeout and not _TIMEOUT_MIN <= timeout <= _TIMEOUT_MAX:
                errors[CONF_SENSOR_TIMEOUT] = _ERR_TIMEOUT_RANGE
            else:
                return self.async_create_entry(data=options)
        schema = _options_schema(self.config_entry.options)
        return self.async_show_form(
            step_id="init",
            data_schema=(
                schema
                if user_input is None
                else self.add_suggested_values_to_schema(schema, user_input)
            ),
            errors=errors,
        )
