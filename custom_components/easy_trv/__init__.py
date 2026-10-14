"""Easy TRV: climate-control integration for Zigbee thermostatic radiator valves."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
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
    CONF_HYSTERESIS,
    CONF_PROFILE,
    CONF_TEMP_SENSORS,
    CONF_TRVS,
    CONF_WINDOW_DETECTION,
    PLATFORMS,
    PROFILE_AUTO,
    SUBENTRY_ROOM,
)
from custom_components.easy_trv.controller import RoomController
from custom_components.easy_trv.core.model import RoomConfig, RoomParams, TrvConfig
from custom_components.easy_trv.core.settings import resolve_settings
from custom_components.easy_trv.profiles.loader import LoadResult, async_load_profiles

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant

    from custom_components.easy_trv.profiles.engine import Profile

_LOGGER = logging.getLogger(__name__)

# The way to reach ZHA; tests replace it with a factory returning a fake gateway.
GATEWAY_FACTORY: Callable[[HomeAssistant], TrvGateway] = ZhaLibraryGateway

_HYSTERESIS_LEAVE = "leave"


def _hysteresis_kelvin(stored: str) -> float | None:
    """Return the stored hysteresis choice in kelvin (``None``: leave as is).

    The choice is a translation-safe key (``0_4``), not a number. The dotted
    form (``0.4``) is accepted as well. Python's own ``float("0_4")`` would
    read this as 4.0, so the separator is replaced explicitly.
    """
    if stored == _HYSTERESIS_LEAVE:
        return None
    return float(str(stored).replace("_", "."))


@dataclass
class EasyTrvRuntimeData:
    """Objects that live as long as a config entry is loaded."""

    gateway: TrvGateway
    profiles: LoadResult
    rooms: dict[str, RoomController]


type EasyTrvConfigEntry = ConfigEntry[EasyTrvRuntimeData]


def room_config_from_subentry(
    subentry_id: str,
    title: str,
    data: Mapping[str, Any],
    device_refs: Mapping[str, DeviceRef],
) -> RoomConfig:
    """Build the :class:`RoomConfig` of a ``room`` subentry.

    ``device_refs`` must hold every thermostat listed in ``data``. A hysteresis
    of ``"leave"`` means the thermostat's own setting stays untouched (``None``);
    ``"0_4"`` (or the older ``"0.4"``) means 0.4 K.
    """
    hysteresis = data.get(CONF_HYSTERESIS, _HYSTERESIS_LEAVE)
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
        window_detection=data.get(CONF_WINDOW_DETECTION, "on"),
        hysteresis=_hysteresis_kelvin(hysteresis),
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


def _build_room(
    hass: HomeAssistant,
    subentry: ConfigSubentry,
    params: RoomParams,
    gateway: TrvGateway,
    profiles: Mapping[str, Profile],
) -> tuple[RoomController, Profile]:
    """Create the controller of one ``room`` subentry."""
    device_refs = _resolve_thermostats(gateway, list(subentry.data[CONF_TRVS]))
    profile = select_profile(
        profiles, subentry.data.get(CONF_PROFILE, PROFILE_AUTO), device_refs
    )
    config = room_config_from_subentry(
        subentry.subentry_id, subentry.title, subentry.data, device_refs
    )
    controller = RoomController(hass, config, params, profile, gateway, device_refs)
    return controller, profile


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

    params = RoomParams.from_settings(resolve_settings(entry.options, {}))
    rooms: dict[str, RoomController] = {}
    summaries: dict[str, str] = {}
    invalid: set[str] = set()
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        try:
            controller, profile = _build_room(
                hass, subentry, params, gateway, profiles.profiles
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
        summaries[subentry.subentry_id] = (
            f"{subentry.title} ({len(subentry.data[CONF_TRVS])} thermostat(s), "
            f"profile {profile.id})"
        )
    issues.clear_room_issues_except(hass, invalid)
    issues.clear_thermostat_issues_except(
        hass, {ieee for controller in rooms.values() for ieee in controller.ieees}
    )
    entry.runtime_data = EasyTrvRuntimeData(gateway, profiles, rooms)

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
