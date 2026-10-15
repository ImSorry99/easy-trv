"""Easy TRV: climate-control integration for Zigbee thermostatic radiator valves."""

import logging
import math
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.components.climate.const import ATTR_MAX_TEMP, ATTR_MIN_TEMP
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.start import async_at_started

from custom_components.easy_trv import issues
from custom_components.easy_trv.adapters.zha_gateway import (
    DeviceRef,
    GatewayError,
    TrvGateway,
    ZhaLibraryGateway,
    ZhaUnavailable,
)
from custom_components.easy_trv.const import (
    CONF_AREA,
    CONF_HYSTERESIS,
    CONF_OUTDOOR_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_TRVS,
    CONF_WINDOW_CONTACTS,
    DEFAULT_SETPOINT_MAX,
    DEFAULT_SETPOINT_MIN,
    PLATFORMS,
    PROFILE_AUTO,
    SETTING_GLOBAL,
    SUBENTRY_ROOM,
)
from custom_components.easy_trv.controller import RoomController
from custom_components.easy_trv.core.model import RoomConfig, RoomParams, TrvConfig
from custom_components.easy_trv.core.settings import RoomSettings, resolve_settings
from custom_components.easy_trv.labels import (
    async_capability_labels,
    localized_number,
)
from custom_components.easy_trv.profiles.loader import LoadResult, async_load_profiles

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from custom_components.easy_trv.profiles.engine import Profile

_LOGGER = logging.getLogger(__name__)

# The hysteresis choice that version 1 entries could store and version 2 retired.
_HYSTERESIS_LEAVE = "leave"

# The way to reach ZHA; tests replace it with a factory returning a fake gateway.
GATEWAY_FACTORY: Callable[[HomeAssistant], TrvGateway] = ZhaLibraryGateway


@dataclass
class EasyTrvRuntimeData:
    """Objects that live as long as a config entry is loaded."""

    gateway: TrvGateway
    profiles: LoadResult
    rooms: dict[str, RoomController]
    settings: dict[str, RoomSettings]  # resolved settings by subentry id
    areas: dict[str, str | None]  # area name by subentry id (None: no area)
    labels: Mapping[str, str]  # plain-language setting name by intent


type EasyTrvConfigEntry = ConfigEntry[EasyTrvRuntimeData]


# The intents a room needs to be a thermostat of its own.
_INTENT_SETPOINT = "set_setpoint"
_INTENT_MODE = "set_hvac_mode"
_DEFAULT_STEP = 0.5


def effective_range(
    profile: Profile,
    settings: RoomSettings,
    thermostat_limits: tuple[float, float] | None = None,
) -> tuple[float, float, float] | None:
    """Return ``(min, max, step)`` of the room setpoint, ``None`` without a climate.

    The room's limits are ``thermostat_limits`` (what the thermostats show) when
    the range is ``auto``, else the room's ``setpoint_min`` and
    ``setpoint_max``. They are cut to the limits the profile declares for the
    thermostats; the narrowest wins. Without any limit the built-in default
    range applies. A profile that cannot write the setpoint or the mode gives
    the room no climate entity.

    Raises:
        ValueError: If the two ranges do not overlap.

    """
    intents = {spec.intent for spec in profile.capabilities.values()}
    if not {_INTENT_SETPOINT, _INTENT_MODE} <= intents:
        return None
    spec = next(
        s for s in profile.capabilities.values() if s.intent == _INTENT_SETPOINT
    )
    # A capability without a declared range leaves the room's limits alone.
    low, high = spec.value_range or (-math.inf, math.inf)
    step = _DEFAULT_STEP if spec.step is None else spec.step
    if settings.setpoint_range == "manual":
        room_low, room_high = settings.setpoint_min, settings.setpoint_max
    else:
        room_low, room_high = thermostat_limits or (-math.inf, math.inf)
    low = max(room_low, low)
    high = min(room_high, high)
    if not math.isfinite(low):
        low = DEFAULT_SETPOINT_MIN
    if not math.isfinite(high):
        high = DEFAULT_SETPOINT_MAX
    if low >= high:
        msg = "setpoint range is empty"
        raise ValueError(msg)
    return low, high, step


def thermostat_limits(
    hass: HomeAssistant, trvs: Sequence[str]
) -> tuple[float, float] | None:
    """Return the narrowest ``min_temp``..``max_temp`` the thermostats show.

    Thermostats whose state shows no numeric limits are left out; ``None`` if
    none shows both.
    """
    lows: list[float] = []
    highs: list[float] = []
    for entity_id in trvs:
        state = hass.states.get(entity_id)
        if state is None:
            continue
        low = _limit(state.attributes.get(ATTR_MIN_TEMP))
        high = _limit(state.attributes.get(ATTR_MAX_TEMP))
        if low is None or high is None:
            continue
        lows.append(low)
        highs.append(high)
    if not lows:
        return None
    return max(lows), min(highs)


def _limit(value: object) -> float | None:
    """Return a temperature limit as a float, ``None`` if it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def room_config_from_subentry(  # noqa: PLR0913 - the stored data and what shapes it
    subentry_id: str,
    title: str,
    data: Mapping[str, Any],
    device_refs: Mapping[str, DeviceRef],
    settings: RoomSettings,
    profile: Profile,
    *,
    thermostat_limits: tuple[float, float] | None = None,
) -> RoomConfig:
    """Build the :class:`RoomConfig` of a ``room`` subentry.

    ``device_refs`` must hold every thermostat listed in ``data``. The
    thermostat settings come from ``settings``, the resolved settings of the
    room; ``profile`` and, for an ``auto`` range, ``thermostat_limits`` limit
    the setpoint range.

    Raises:
        ValueError: If the setpoint ranges of the room and the profile are disjoint.

    """
    return RoomConfig(
        room_id=subentry_id,
        name=title,
        trvs=tuple(
            TrvConfig(
                entity_id,
                device_refs[entity_id].ieee,
                device_refs[entity_id].firmware,
            )
            for entity_id in data[CONF_TRVS]
        ),
        temp_sensors=tuple(data.get(CONF_TEMP_SENSORS, ())),
        window_detection=settings.window_detection,
        hysteresis=settings.hysteresis,
        adaptive_mode=settings.adaptive_mode,
        frost_temperature=settings.frost_temperature,
        setpoint_range=effective_range(profile, settings, thermostat_limits),
        window_contacts=tuple(data.get(CONF_WINDOW_CONTACTS, ())),
        window_warn=(
            timedelta(minutes=settings.window_warn_minutes)
            if settings.window_warn_minutes
            else None
        ),
        window_check=(
            timedelta(minutes=settings.window_check_minutes)
            if settings.window_check_minutes
            else None
        ),
        window_check_drop=settings.window_check_drop_kelvin,
        outdoor_cutoff=settings.outdoor_cutoff_celsius,
        window_open_delay=(
            timedelta(seconds=settings.window_open_delay_seconds)
            if settings.window_open_delay_seconds
            else None
        ),
    )


def select_profile(
    profiles: Mapping[str, Profile],
    choice: str,
    device_refs: Mapping[str, DeviceRef],
) -> Profile:
    """Pick the profile that drives every thermostat of a room.

    ``auto`` takes the first loaded profile that matches every thermostat
    (built-in profiles come first, then user profiles, each in file order); any
    other ``choice`` is a profile id that must exist and match every thermostat.
    The firmware is not checked here: the profile gates single operations by
    firmware when they run.

    Raises:
        ConfigEntryError: If no suitable profile exists.

    """

    def matches_all(profile: Profile) -> bool:
        return all(
            profile.matches(ref.manufacturer, ref.model) for ref in device_refs.values()
        )

    if choice == PROFILE_AUTO:
        for profile in profiles.values():
            if matches_all(profile):
                return profile
        described = ", ".join(
            f"{ref.entity_id} ({ref.manufacturer} {ref.model})"
            for ref in device_refs.values()
        )
        msg = f"no profile matches all thermostats: {described}"
        raise ConfigEntryError(msg)

    selected = profiles.get(choice)
    if selected is None:
        msg = f"profile {choice!r} does not exist"
        raise ConfigEntryError(msg)
    for ref in device_refs.values():
        if not selected.matches(ref.manufacturer, ref.model):
            msg = (
                f"profile {choice!r} does not match {ref.entity_id} "
                f"({ref.manufacturer} {ref.model})"
            )
            raise ConfigEntryError(msg)
    return selected


def _resolve_thermostats(
    gateway: TrvGateway, entity_ids: list[str]
) -> dict[str, DeviceRef]:
    """Resolve every thermostat of a room to its ZHA device.

    Raises:
        ConfigEntryError: If an entity is not a ZHA thermostat. That is a
            permanent configuration problem of the room, which is skipped.

    """
    refs: dict[str, DeviceRef] = {}
    for entity_id in entity_ids:
        try:
            refs[entity_id] = gateway.resolve(entity_id)
        except GatewayError as err:
            msg = f"{entity_id} is not a ZHA thermostat"
            raise ConfigEntryError(msg) from err
    return refs


def _describe_room_error(err: Exception) -> str:
    """Return the text of a room's setup problem for the log and the issue."""
    if isinstance(err, ConfigEntryError):
        return str(err)
    return f"invalid room data: {type(err).__name__}: {err}"


def _build_room(  # noqa: PLR0913 - the collaborators of one room
    hass: HomeAssistant,
    entry: EasyTrvConfigEntry,
    subentry: ConfigSubentry,
    gateway: TrvGateway,
    profiles: Mapping[str, Profile],
    labels: Mapping[str, str],
) -> tuple[RoomController, Profile, RoomSettings]:
    """Create the controller of one ``room`` subentry."""
    settings = resolve_settings(entry.options, subentry.data)
    device_refs = _resolve_thermostats(gateway, list(subentry.data[CONF_TRVS]))
    profile = select_profile(profiles, settings.profile, device_refs)
    config = room_config_from_subentry(
        subentry.subentry_id,
        subentry.title,
        subentry.data,
        device_refs,
        settings,
        profile,
        thermostat_limits=thermostat_limits(hass, subentry.data[CONF_TRVS]),
    )
    params = RoomParams.from_settings(settings)
    controller = RoomController(
        hass,
        config,
        params,
        profile,
        gateway,
        device_refs,
        labels=labels,
        outdoor_sensor=entry.options.get(CONF_OUTDOOR_SENSOR) or None,
    )
    return controller, profile, settings


def _area_name(hass: HomeAssistant, subentry: ConfigSubentry) -> str | None:
    """Return the name of the area a room was created from, if it still exists."""
    area_id = subentry.data.get(CONF_AREA)
    if not area_id:
        return None
    area = ar.async_get(hass).async_get_area(area_id)
    return area.name if area is not None else None


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an entry to the current version.

    Version 2 retires the hysteresis choice "leave unchanged": the hysteresis is
    always written now, so a room that said "leave" follows the global value. The
    rooms that changed are reported in a Repairs issue.
    """
    if entry.version == 1:
        migrated: list[str] = []
        for subentry in list(entry.subentries.values()):
            if (
                subentry.subentry_type != SUBENTRY_ROOM
                or subentry.data.get(CONF_HYSTERESIS) != _HYSTERESIS_LEAVE
            ):
                continue
            hass.config_entries.async_update_subentry(
                entry, subentry, data={**subentry.data, CONF_HYSTERESIS: SETTING_GLOBAL}
            )
            migrated.append(subentry.title)
        hass.config_entries.async_update_entry(entry, version=2)
        if migrated:
            kelvin = f"{resolve_settings(entry.options, {}).hysteresis:.1f}"
            issues.raise_settings_migrated(
                hass, migrated, f"{localized_number(hass, kelvin)} K"
            )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EasyTrvConfigEntry) -> bool:
    """Set up Easy TRV: one controller and one status sensor per room."""
    try:
        gateway = GATEWAY_FACTORY(hass)
    except ZhaUnavailable as err:
        issues.raise_zha_api(hass, str(err))
        msg = f"ZHA is not available: {err}"
        raise ConfigEntryNotReady(msg) from err
    issues.clear_zha_api(hass)

    profiles = await async_load_profiles(hass)
    for error in profiles.errors:
        issues.raise_profile_invalid(hass, error.file, str(error))
    issues.clear_profile_issues(
        hass, keep={Path(error.file).stem for error in profiles.errors}
    )

    labels = await async_capability_labels(hass)
    rooms: dict[str, RoomController] = {}
    resolved: dict[str, RoomSettings] = {}
    areas: dict[str, str | None] = {}
    summaries: dict[str, str] = {}
    invalid: set[str] = set()
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        try:
            controller, profile, settings = _build_room(
                hass, entry, subentry, gateway, profiles.profiles, labels
            )
        except (ConfigEntryError, ValueError, KeyError, TypeError) as err:
            # One room that cannot be set up must not take the others down.
            # The stored data of a room can be corrupt (a missing key, a value
            # of the wrong type): that is the same kind of room problem.
            problem = _describe_room_error(err)
            _LOGGER.warning(
                "Room %s could not be set up: %s",
                subentry.title,
                problem,
                exc_info=not isinstance(err, ConfigEntryError),
            )
            issues.raise_room_invalid(
                hass, subentry.subentry_id, subentry.title, problem
            )
            invalid.add(subentry.subentry_id)
            continue
        rooms[subentry.subentry_id] = controller
        resolved[subentry.subentry_id] = settings
        areas[subentry.subentry_id] = _area_name(hass, subentry)
        summaries[subentry.subentry_id] = (
            f"{subentry.title} ({len(subentry.data[CONF_TRVS])} thermostat(s), "
            f"profile {profile.id})"
        )
    issues.clear_room_issues_except(hass, invalid)
    # A room's own controller clears its window issues when it starts; those of
    # rooms that are gone (or cannot be set up any more) must not stay behind.
    issues.clear_window_issues_except(hass, set(rooms))
    issues.clear_thermostat_issues_except(
        hass, {ieee for controller in rooms.values() for ieee in controller.ieees}
    )
    entry.runtime_data = EasyTrvRuntimeData(
        gateway, profiles, rooms, resolved, areas, labels
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _start_rooms(_hass: HomeAssistant) -> None:
        for subentry_id, controller in rooms.items():
            try:
                await controller.async_start()
            except Exception:
                _LOGGER.exception(
                    "Room %s could not be started", summaries[subentry_id]
                )
                continue
            _LOGGER.info("Started room %s", summaries[subentry_id])

    # The thermostats are only touched once Home Assistant has fully started,
    # so ZHA and the sensors have had their chance to come up.
    entry.async_on_unload(async_at_started(hass, _start_rooms))
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
    return True


async def _async_reload_on_update(
    hass: HomeAssistant, entry: EasyTrvConfigEntry
) -> None:
    """Reload when the options or the rooms (subentries) change."""
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EasyTrvConfigEntry) -> bool:
    """Stop the controllers and unload the platforms."""
    for controller in entry.runtime_data.rooms.values():
        await controller.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
