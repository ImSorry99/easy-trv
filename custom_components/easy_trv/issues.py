"""Repairs issues raised by the integration."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import issue_registry as ir

from custom_components.easy_trv.const import (
    DOMAIN,
    ISSUE_SETTINGS_MIGRATED,
    ISSUE_ZHA_API,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from homeassistant.core import HomeAssistant

_UNREACHABLE_PREFIX = "trv_unreachable_"
_FIRMWARE_PREFIX = "trv_firmware_"
_SCHEDULE_OFF_PREFIX = "schedule_thermostat_off_"
_PROFILE_INVALID_PREFIX = "profile_invalid_"
_ROOM_INVALID_PREFIX = "room_invalid_"
_WINDOW_OPEN_LONG_PREFIX = "window_open_long_"
_WINDOW_CONTACT_SUSPECT_PREFIX = "window_contact_suspect_"


def issue_id_unreachable(ieee: str) -> str:
    """Return the id of the "thermostat does not accept writes" issue."""
    return f"{_UNREACHABLE_PREFIX}{ieee.replace(':', '')}"


def issue_id_firmware(ieee: str) -> str:
    """Return the id of the "firmware lacks a capability" issue."""
    return f"{_FIRMWARE_PREFIX}{ieee.replace(':', '')}"


def issue_id_schedule_thermostat_off(ieee: str) -> str:
    """Return the id of the "thermostat stays off after a hold" issue."""
    return f"{_SCHEDULE_OFF_PREFIX}{ieee.replace(':', '')}"


def issue_id_room_invalid(subentry_id: str) -> str:
    """Return the id of the "room could not be set up" issue."""
    return f"{_ROOM_INVALID_PREFIX}{subentry_id}"


def issue_id_window_open_long(room_id: str) -> str:
    """Return the id of the "window open for a long time" issue of a room."""
    return f"{_WINDOW_OPEN_LONG_PREFIX}{room_id}"


def issue_id_window_contact_suspect(room_id: str) -> str:
    """Return the id of the "window contact looks stuck" issue of a room."""
    return f"{_WINDOW_CONTACT_SUSPECT_PREFIX}{room_id}"


def _issue_id_profile_invalid(file: str) -> str:
    """Return the id of the "profile file is invalid" issue for a file name."""
    return f"{_PROFILE_INVALID_PREFIX}{Path(file).stem}"


def _create(
    hass: HomeAssistant,
    issue_id: str,
    severity: ir.IssueSeverity,
    translation_key: str,
    placeholders: Mapping[str, Any],
) -> None:
    """Create (or update) a non-fixable, non-persistent issue of this domain."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=False,
        severity=severity,
        translation_key=translation_key,
        translation_placeholders={k: str(v) for k, v in placeholders.items()},
    )


def raise_unreachable(  # noqa: PLR0913 - one argument per placeholder of the text
    hass: HomeAssistant,
    ieee: str,
    *,
    room: str,
    setting: str,
    thermostat: str,
    attempts: str,
    error: str,
) -> None:
    """Report a thermostat that keeps failing to accept writes.

    ``setting`` and ``thermostat`` are plain-language names, not keys.
    """
    _create(
        hass,
        issue_id_unreachable(ieee),
        ir.IssueSeverity.ERROR,
        "trv_unreachable",
        {
            "room": room,
            "setting": setting,
            "thermostat": thermostat,
            "attempts": attempts,
            "error": error,
        },
    )


def clear_unreachable(hass: HomeAssistant, ieee: str) -> None:
    """Remove the "thermostat does not accept writes" issue."""
    ir.async_delete_issue(hass, DOMAIN, issue_id_unreachable(ieee))


def raise_firmware(  # noqa: PLR0913 - one argument per placeholder of the text
    hass: HomeAssistant,
    ieee: str,
    *,
    room: str,
    setting: str,
    thermostat: str,
    required: str,
    current: str,
) -> None:
    """Report a thermostat whose firmware lacks a capability.

    ``setting`` and ``thermostat`` are plain-language names, not keys.
    """
    _create(
        hass,
        issue_id_firmware(ieee),
        ir.IssueSeverity.WARNING,
        "trv_firmware",
        {
            "room": room,
            "setting": setting,
            "thermostat": thermostat,
            "required": required,
            "current": current,
        },
    )


def clear_firmware(hass: HomeAssistant, ieee: str) -> None:
    """Remove the "firmware lacks a capability" issue."""
    ir.async_delete_issue(hass, DOMAIN, issue_id_firmware(ieee))


def raise_schedule_thermostat_off(
    hass: HomeAssistant, ieee: str, *, room: str, thermostat: str
) -> None:
    """Report a schedule thermostat a hold switched off and that stays off.

    Easy TRV never writes ``auto``, so the person has to switch it back.
    """
    _create(
        hass,
        issue_id_schedule_thermostat_off(ieee),
        ir.IssueSeverity.WARNING,
        "schedule_thermostat_off",
        {"room": room, "thermostat": thermostat},
    )


def clear_schedule_thermostat_off(hass: HomeAssistant, ieee: str) -> None:
    """Remove the "thermostat stays off after a hold" issue."""
    ir.async_delete_issue(hass, DOMAIN, issue_id_schedule_thermostat_off(ieee))


def raise_zha_api(hass: HomeAssistant, error: str) -> None:
    """Report that the installed ZHA no longer offers the API we rely on."""
    _create(
        hass,
        ISSUE_ZHA_API,
        ir.IssueSeverity.ERROR,
        "zha_api_incompatible",
        {"error": error},
    )


def clear_zha_api(hass: HomeAssistant) -> None:
    """Remove the "ZHA API incompatible" issue."""
    ir.async_delete_issue(hass, DOMAIN, ISSUE_ZHA_API)


def raise_settings_migrated(
    hass: HomeAssistant, rooms: Iterable[str], hysteresis: str
) -> None:
    """Report rooms whose "leave unchanged" hysteresis now follows the global value.

    The migration runs only once, so this notice is persistent: it survives a
    restart until the user dismisses it.
    """
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_SETTINGS_MIGRATED,
        is_fixable=False,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="settings_migrated",
        translation_placeholders={"rooms": ", ".join(rooms), "hysteresis": hysteresis},
    )


def raise_profile_invalid(hass: HomeAssistant, file: str, error: str) -> None:
    """Report a user profile file that could not be loaded."""
    _create(
        hass,
        _issue_id_profile_invalid(file),
        ir.IssueSeverity.WARNING,
        "profile_invalid",
        {"file": file, "error": error},
    )


def clear_profile_issues(hass: HomeAssistant, keep: set[str]) -> None:
    """Remove every profile issue whose file stem is not in ``keep``.

    ``keep`` holds the stems (file names without extension) of the files that
    are still invalid, so repaired or deleted files lose their issue.
    """
    wanted = {f"{_PROFILE_INVALID_PREFIX}{stem}" for stem in keep}
    for domain, issue_id in list(ir.async_get(hass).issues):
        if (
            domain == DOMAIN
            and issue_id.startswith(_PROFILE_INVALID_PREFIX)
            and issue_id not in wanted
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def clear_thermostat_issues_except(hass: HomeAssistant, keep_ieees: set[str]) -> None:
    """Remove the thermostat issues of every thermostat not in ``keep_ieees``.

    Covers ``trv_unreachable_*``, ``trv_firmware_*`` and
    ``schedule_thermostat_off_*``. ``keep_ieees`` holds
    the IEEE addresses of the thermostats of all configured rooms, so the issues
    of a thermostat that was removed from its room (or whose room was deleted)
    do not outlive it.
    """
    wanted = (
        {issue_id_unreachable(ieee) for ieee in keep_ieees}
        | {issue_id_firmware(ieee) for ieee in keep_ieees}
        | {issue_id_schedule_thermostat_off(ieee) for ieee in keep_ieees}
    )
    for domain, issue_id in list(ir.async_get(hass).issues):
        if (
            domain == DOMAIN
            and issue_id.startswith(
                (_UNREACHABLE_PREFIX, _FIRMWARE_PREFIX, _SCHEDULE_OFF_PREFIX)
            )
            and issue_id not in wanted
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def raise_room_invalid(
    hass: HomeAssistant, subentry_id: str, room: str, error: str
) -> None:
    """Report a room that could not be set up and is skipped."""
    _create(
        hass,
        issue_id_room_invalid(subentry_id),
        ir.IssueSeverity.WARNING,
        "room_invalid",
        {"room": room, "error": error},
    )


def clear_room_issues_except(hass: HomeAssistant, keep_subentry_ids: set[str]) -> None:
    """Remove every room issue whose subentry id is not in ``keep_subentry_ids``.

    ``keep_subentry_ids`` holds the rooms that are still invalid, so rooms that
    set up fine now and rooms that were deleted lose their issue.
    """
    wanted = {issue_id_room_invalid(subentry_id) for subentry_id in keep_subentry_ids}
    for domain, issue_id in list(ir.async_get(hass).issues):
        if (
            domain == DOMAIN
            and issue_id.startswith(_ROOM_INVALID_PREFIX)
            and issue_id not in wanted
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)


def raise_window_open_long(
    hass: HomeAssistant, room_id: str, room: str, minutes: str | int
) -> None:
    """Report a window that has been open for a long time."""
    _create(
        hass,
        issue_id_window_open_long(room_id),
        ir.IssueSeverity.ERROR,
        "window_open_long",
        {"room": room, "minutes": minutes},
    )


def clear_window_open_long(hass: HomeAssistant, room_id: str) -> None:
    """Remove the "window open for a long time" issue of a room."""
    ir.async_delete_issue(hass, DOMAIN, issue_id_window_open_long(room_id))


def raise_window_contact_suspect(
    hass: HomeAssistant,
    room_id: str,
    room: str,
    minutes: str | int,
    drop: str | float,
) -> None:
    """Report a window contact that says open while the room does not cool down.

    ``drop`` is the fall in kelvin the room should have shown by now.
    """
    _create(
        hass,
        issue_id_window_contact_suspect(room_id),
        ir.IssueSeverity.WARNING,
        "window_contact_suspect",
        {"room": room, "minutes": minutes, "drop": drop},
    )


def clear_window_contact_suspect(hass: HomeAssistant, room_id: str) -> None:
    """Remove the "window contact looks stuck" issue of a room."""
    ir.async_delete_issue(hass, DOMAIN, issue_id_window_contact_suspect(room_id))


def clear_window_issues_except(hass: HomeAssistant, keep_room_ids: set[str]) -> None:
    """Remove the window issues of every room not in ``keep_room_ids``.

    Covers ``window_open_long_*`` and ``window_contact_suspect_*``. The rooms
    that are kept are those that still have a controller; the issues of a room
    that was deleted or can no longer be set up do not outlive it.
    """
    wanted = {issue_id_window_open_long(room_id) for room_id in keep_room_ids} | {
        issue_id_window_contact_suspect(room_id) for room_id in keep_room_ids
    }
    for domain, issue_id in list(ir.async_get(hass).issues):
        if (
            domain == DOMAIN
            and issue_id.startswith(
                (_WINDOW_OPEN_LONG_PREFIX, _WINDOW_CONTACT_SUSPECT_PREFIX)
            )
            and issue_id not in wanted
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
