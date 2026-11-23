"""Subentry flow for thermostat profiles: upload, community download, edit.

A profile added here is stored in the entry as YAML text (a subentry of type
``profile``), next to the built-in profiles and the files in
``<config>/easy_trv/profiles``. Rooms choose it like any other profile.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlencode

import aiohttp
import voluptuous as vol
from homeassistant.components.file_upload import process_uploaded_file
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    FileSelector,
    FileSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from custom_components.easy_trv.const import (
    COMMUNITY_BASE_URL,
    COMMUNITY_INDEX_FILE,
    CONF_PROFILE_YAML,
    SHARE_BASE_URL,
    SHARE_TEMPLATE,
    SUBENTRY_PROFILE,
)
from custom_components.easy_trv.profile_wizard import (
    FIELDS,
    REQUIRED_FIELD,
    build_profile_yaml,
    device_entities,
    profile_id,
)
from custom_components.easy_trv.profiles.community import CommunityEntry, parse_index
from custom_components.easy_trv.profiles.loader import (
    ProfileLoadError,
    add_documents,
    async_load_profiles,
    parse_profile_text,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry, SubentryFlowResult
    from homeassistant.core import HomeAssistant

    from custom_components.easy_trv.profiles.engine import Profile

_LOGGER = logging.getLogger(__name__)

_STEP_USER: Final = "user"
_STEP_CREATE: Final = "create"
_STEP_UPLOAD: Final = "upload"
_STEP_COMMUNITY: Final = "community"
_STEP_RECONFIGURE: Final = "reconfigure"

_FIELD_FILE: Final = "file"
_FIELD_YAML: Final = "yaml"
_FIELD_ENTRY: Final = "profile"
_FIELD_NAME: Final = "name"
_FIELD_THERMOSTAT: Final = "thermostat"
_ERR_NAME_REQUIRED: Final = "name_required"
_ERR_NO_ENTITIES: Final = "no_entities"

_ERR_NO_INPUT: Final = "no_input"
_ERR_INVALID: Final = "invalid_profile"
_ERR_ID_TAKEN: Final = "profile_id_taken"
_ERR_DOWNLOAD: Final = "download_failed"

_HTTP_TIMEOUT: Final = 15
# A profile is a few kilobytes; anything far larger is not one.
_MAX_TEXT: Final = 64 * 1024


def profile_documents(entry: ConfigEntry) -> dict[str, str]:
    """Return the YAML text of every profile subentry, keyed by its title."""
    return {
        subentry.title: subentry.data[CONF_PROFILE_YAML]
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_PROFILE
        and isinstance(subentry.data.get(CONF_PROFILE_YAML), str)
    }


def _schema(fields: dict[Any, Any]) -> Any:
    """Return a form schema (typed loosely: Home Assistant types its own)."""
    return vol.Schema(fields)


def share_url(title: str, text: str) -> str:
    """Return the link that opens the "share a profile" form on GitHub, filled in."""
    query = urlencode(
        {"template": SHARE_TEMPLATE, "title": f"Profile: {title}", "profile": text}
    )
    return f"{SHARE_BASE_URL}?{query}"


def _details(text: str) -> dict[str, str]:
    """Return the placeholder that shows what is wrong below the description."""
    return {"error": f"\n\n**{text}**"}


def _read_upload(hass: HomeAssistant, file_id: str) -> str:
    """Read an uploaded file (blocking)."""
    with (
        process_uploaded_file(hass, file_id) as path,
        Path(path).open(encoding="utf-8") as file,
    ):
        return file.read(_MAX_TEXT + 1)


class ProfileSubentryFlow(ConfigSubentryFlow):
    """Add a thermostat profile (upload or community) and edit it later."""

    def __init__(self) -> None:
        """Start without a community index or wizard picks."""
        self._entries: dict[str, CommunityEntry] = {}
        self._name = ""
        self._climate = ""
        self._picks: dict[str, str] = {}

    # --- Steps --------------------------------------------------------------

    async def async_step_user(
        self, _user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Ask where the profile comes from."""
        return self.async_show_menu(
            step_id=_STEP_USER,
            menu_options=[_STEP_CREATE, _STEP_UPLOAD, _STEP_COMMUNITY],
        )

    async def async_step_create(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Wizard (Beta), step 1: name the profile and pick a sample thermostat."""
        errors: dict[str, str] = {}
        if user_input is not None:
            name = str(user_input.get(_FIELD_NAME, "")).strip()
            climate = str(user_input.get(_FIELD_THERMOSTAT, ""))
            if not name:
                errors[_FIELD_NAME] = _ERR_NAME_REQUIRED
            elif not device_entities(self.hass, climate, ("number",)):
                errors[_FIELD_THERMOSTAT] = _ERR_NO_ENTITIES
            elif profile_id(name) in await self._async_profiles():
                errors[_FIELD_NAME] = _ERR_ID_TAKEN
            else:
                self._name, self._climate = name, climate
                return await self.async_step_create_map()
        return self.async_show_form(
            step_id=_STEP_CREATE,
            data_schema=self.add_suggested_values_to_schema(
                _schema(
                    {
                        vol.Required(_FIELD_NAME): TextSelector(),
                        vol.Required(_FIELD_THERMOSTAT): EntitySelector(
                            EntitySelectorConfig(domain="climate")
                        ),
                    }
                ),
                user_input or {},
            ),
            errors=errors,
            description_placeholders={"error": ""},
        )

    async def async_step_create_map(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Wizard (Beta), step 2: pick which entity of the thermostat does what."""
        if user_input is not None and user_input.get(REQUIRED_FIELD):
            self._picks = {key: value for key, value in user_input.items() if value}
            source = self._picks.get("source", "")
            if source.startswith("select."):
                return await self.async_step_create_source()
            return self._create_from_picks(None)
        schema: dict[Any, Any] = {}
        for field, (_capability, _intent, domains) in FIELDS.items():
            include = device_entities(self.hass, self._climate, domains)
            if not include:
                continue
            marker = vol.Required if field == REQUIRED_FIELD else vol.Optional
            schema[marker(field)] = EntitySelector(
                EntitySelectorConfig(include_entities=include)
            )
        return self.async_show_form(
            step_id="create_map",
            data_schema=_schema(schema),
            description_placeholders={"thermostat": self._climate},
        )

    async def async_step_create_source(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Wizard (Beta), step 3: which option of the select is which sensor."""
        if user_input is not None:
            return self._create_from_picks(
                {"external": user_input["external"], "internal": user_input["internal"]}
            )
        state = self.hass.states.get(self._picks["source"])
        options = (
            [str(option) for option in (state.attributes.get("options") or [])]
            if state
            else []
        )
        selector = SelectSelector(
            SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
        )
        return self.async_show_form(
            step_id="create_source",
            data_schema=_schema(
                {vol.Required("external"): selector, vol.Required("internal"): selector}
            ),
        )

    def _create_from_picks(
        self, source_options: dict[str, str] | None
    ) -> SubentryFlowResult:
        """Store the profile the wizard built."""
        text = build_profile_yaml(
            self.hass, self._name, self._climate, self._picks, source_options
        )
        try:
            _data, profile = parse_profile_text(text, self._name)
        except ProfileLoadError as err:
            _LOGGER.warning("The wizard built an invalid profile: %s", err)
            return self.async_abort(reason="wizard_failed")
        return self._create(profile.name, text)

    async def async_step_upload(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Take a profile as an uploaded file or as pasted YAML."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            text = await self._uploaded_text(user_input)
            if not text:
                errors["base"] = _ERR_NO_INPUT
            else:
                result = await self._async_check(text, "upload", None)
                if isinstance(result, str):
                    return self._create(result, text)
                errors, placeholders = result
        return self.async_show_form(
            step_id=_STEP_UPLOAD,
            data_schema=_schema(
                {
                    vol.Optional(_FIELD_FILE): FileSelector(
                        FileSelectorConfig(accept=".yaml,.yml,text/yaml")
                    ),
                    vol.Optional(_FIELD_YAML): TextSelector(
                        TextSelectorConfig(multiline=True)
                    ),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_community(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """List the shared profiles of the community and install one."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if not self._entries:
            entries = await self._async_fetch_index()
            if entries is None:
                return self.async_abort(reason="community_unreachable")
            installed = set(await self._async_profiles())
            self._entries = {
                entry.id: entry for entry in entries if entry.id not in installed
            }
            if not self._entries:
                return self.async_abort(reason="community_empty")
        if user_input is not None:
            entry = self._entries[user_input[_FIELD_ENTRY]]
            text = await self._async_download(COMMUNITY_BASE_URL + entry.file)
            if text is None:
                errors["base"] = _ERR_DOWNLOAD
            else:
                result = await self._async_check(text, entry.file, None)
                if isinstance(result, str):
                    return self._create(result, text)
                errors, placeholders = result
        options = [
            SelectOptionDict(value=entry.id, label=entry.label)
            for entry in self._entries.values()
        ]
        return self.async_show_form(
            step_id=_STEP_COMMUNITY,
            data_schema=_schema(
                {
                    vol.Required(_FIELD_ENTRY): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show the profile's YAML to copy or share, and take an edited one."""
        subentry = self._get_reconfigure_subentry()
        current = str(subentry.data.get(CONF_PROFILE_YAML, ""))
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        share = {"share_url": share_url(subentry.title, current)}
        if user_input is not None:
            text = str(user_input.get(_FIELD_YAML, "")).strip()
            if not text:
                errors["base"] = _ERR_NO_INPUT
            else:
                result = await self._async_check(text, subentry.title, current)
                if isinstance(result, str):
                    return self.async_update_and_abort(
                        self._get_entry(),
                        subentry,
                        title=result,
                        data={CONF_PROFILE_YAML: text},
                    )
                errors, placeholders = result
        return self.async_show_form(
            step_id=_STEP_RECONFIGURE,
            data_schema=self.add_suggested_values_to_schema(
                _schema(
                    {
                        vol.Required(_FIELD_YAML): TextSelector(
                            TextSelectorConfig(multiline=True)
                        )
                    }
                ),
                {_FIELD_YAML: current} if user_input is None else user_input,
            ),
            errors=errors,
            description_placeholders={**placeholders, **share},
        )

    # --- Helpers ------------------------------------------------------------

    def _create(self, title: str, text: str) -> SubentryFlowResult:
        """Store the checked profile as a new subentry."""
        return self.async_create_entry(title=title, data={CONF_PROFILE_YAML: text})

    async def _uploaded_text(self, user_input: dict[str, Any]) -> str:
        """Return the text of the uploaded file, else the pasted text."""
        file_id = user_input.get(_FIELD_FILE)
        if file_id:
            try:
                text = await self.hass.async_add_executor_job(
                    _read_upload, self.hass, file_id
                )
            except (OSError, ValueError, UnicodeDecodeError) as err:
                _LOGGER.warning("Uploaded profile could not be read: %s", err)
                return ""
            return text.strip()
        return str(user_input.get(_FIELD_YAML, "")).strip()

    async def _async_check(
        self, text: str, name: str, own_text: str | None
    ) -> str | tuple[dict[str, str], dict[str, str]]:
        """Validate a profile text: its name on success, else errors and details.

        ``own_text`` is the stored text of the profile being edited; its id
        may be kept.
        """
        if len(text) > _MAX_TEXT:
            return {"base": _ERR_INVALID}, _details("the text is too long")
        try:
            _data, profile = parse_profile_text(text, name)
        except ProfileLoadError as err:
            return {"base": _ERR_INVALID}, _details(str(err))
        own_id: str | None = None
        if own_text is not None:
            try:
                own_id = parse_profile_text(own_text, name)[1].id
            except ProfileLoadError:
                own_id = None
        taken = set(await self._async_profiles()) - {own_id}
        if profile.id in taken:
            return {"base": _ERR_ID_TAKEN}, _details(profile.id)
        return profile.name

    async def _async_profiles(self) -> dict[str, Profile]:
        """Return every profile known now, built-in, files and subentries."""
        entry = self._get_entry()
        if entry.state is ConfigEntryState.LOADED:
            return dict(entry.runtime_data.profiles.profiles)
        loaded = await async_load_profiles(self.hass)
        return dict(add_documents(loaded, profile_documents(entry)).profiles)

    async def _async_download(self, url: str) -> str | None:
        """Fetch a text document, or ``None`` if that fails."""
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(_HTTP_TIMEOUT):
                response = await session.get(url)
                response.raise_for_status()
                return await response.text()
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.warning("Could not download %s: %s", url, err)
            return None

    async def _async_fetch_index(self) -> tuple[CommunityEntry, ...] | None:
        """Fetch and parse the community index, or ``None`` if that fails."""
        text = await self._async_download(COMMUNITY_BASE_URL + COMMUNITY_INDEX_FILE)
        if text is None:
            return None
        try:
            return parse_index(json.loads(text))
        except (ValueError, vol.Invalid) as err:
            _LOGGER.warning("The community index is not valid: %s", err)
            return None
