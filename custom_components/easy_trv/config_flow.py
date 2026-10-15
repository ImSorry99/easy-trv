"""Config flow of Easy TRV: the entry, its room subentries and its options."""

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
from homeassistant.core import callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    AreaSelector,
    AreaSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
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
    CONF_NAME,
    CONF_PROFILE,
    CONF_TEMP_SENSORS,
    CONF_TRVS,
    CONF_WINDOW_CONTACTS,
    DOMAIN,
    PROFILE_AUTO,
    SETTING_GLOBAL,
    SUBENTRY_ROOM,
)
from custom_components.easy_trv.core.settings import (
    DEFAULTS,
    resolve_settings,
)
from custom_components.easy_trv.flow_settings import (
    NUMBERS,
    ROOM_SETTING_KEYS,
    effective_settings,
    options_schema,
    options_suggestions,
    room_settings,
    room_settings_schema,
    setpoint_range_error,
    setting_texts,
    stored_options,
    timeout_error,
)
from custom_components.easy_trv.profiles.loader import async_load_profiles

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry, ConfigSubentry
    from homeassistant.core import HomeAssistant

    from custom_components.easy_trv.profiles.engine import Profile

CLIMATE_DOMAIN: Final = "climate"

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


# What the room's entities form submits, besides the name.
_ENTITY_KEYS: Final = (
    CONF_TRVS,
    CONF_TEMP_SENSORS,
    CONF_HUMIDITY_SENSORS,
    CONF_WINDOW_CONTACTS,
)

# The device classes of a binary sensor that can be a window contact.
_CONTACT_CLASSES: Final = ("window", "door", "opening", "garage_door")


# --- Room helpers -----------------------------------------------------------


def prefill_from_area(hass: HomeAssistant, area_id: str) -> dict[str, list[str]]:
    """Suggest the entities of a room from the entities of an area.

    An entity is in an area through its own area or, if it has none, through
    its device. ``trvs`` are the ZHA climate entities. ``temp_sensors`` and
    ``humidity_sensors`` are the enabled sensors of the matching device class,
    except those of a thermostat's own device (its built-in readings are not
    room sensors). ``window_contacts`` are the enabled binary sensors of a
    window, door, opening or garage door class, again without those of a
    thermostat's device. Every list is sorted by entity id. The user may add
    contacts of other areas on the form.
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

    def of_class(domain: str, *device_classes: str) -> list[str]:
        return sorted(
            entry.entity_id
            for entry in entries.values()
            if entry.domain == domain
            and entry.device_id not in trv_devices
            and (entry.device_class or entry.original_device_class) in device_classes
        )

    return {
        CONF_TRVS: sorted(entry.entity_id for entry in trvs),
        CONF_TEMP_SENSORS: of_class("sensor", "temperature"),
        CONF_HUMIDITY_SENSORS: of_class("sensor", "humidity"),
        CONF_WINDOW_CONTACTS: of_class("binary_sensor", *_CONTACT_CLASSES),
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
    """Return the schema fields of a room's thermostats, sensors and contacts."""
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
        # Any area: a window of the kitchen can belong to the living room.
        vol.Optional(CONF_WINDOW_CONTACTS, default=[]): EntitySelector(
            EntitySelectorConfig(
                domain="binary_sensor",
                device_class=list(_CONTACT_CLASSES),
                multiple=True,
            )
        ),
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
            settings = room_settings(user_input)
            errors = (
                timeout_error(settings)
                or setpoint_range_error(entry.options, settings)
                or self._profile_errors(
                    entry, subentry, settings[CONF_PROFILE], profiles
                )
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
        global_texts = await setting_texts(
            self.hass,
            effective_settings(
                entry.options, [key for key in ROOM_SETTING_KEYS if key in DEFAULTS]
            ),
        )
        suggested = user_input
        if user_input is None and subentry is not None:
            suggested = dict(subentry.data)
        return self.async_show_form(
            step_id=_STEP_SETTINGS,
            data_schema=self.add_suggested_values_to_schema(
                room_settings_schema(profiles), suggested
            ),
            errors=errors,
            description_placeholders={
                f"global_{key}": text for key, text in global_texts.items()
            },
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


class EasyTrvOptionsFlow(OptionsFlow):
    """Edit the options that apply to all rooms."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the global options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options = stored_options(user_input)
            errors = timeout_error(options) or setpoint_range_error({}, options)
            if not errors:
                return self.async_create_entry(data=options)
        # Each number says what an empty field means: the built-in default.
        default_texts = await setting_texts(
            self.hass, effective_settings({}, list(NUMBERS))
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                options_schema(self.config_entry.options),
                options_suggestions(self.config_entry.options)
                if user_input is None
                else user_input,
            ),
            errors=errors,
            description_placeholders={
                f"default_{key}": text for key, text in default_texts.items()
            },
        )
