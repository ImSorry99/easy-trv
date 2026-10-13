"""Repairs issues raised by the integration."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import issue_registry as ir

from custom_components.easy_trv.const import DOMAIN, ISSUE_ZHA_API

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

_UNREACHABLE_PREFIX = "trv_unreachable_"
_FIRMWARE_PREFIX = "trv_firmware_"
_PROFILE_INVALID_PREFIX = "profile_invalid_"


def issue_id_unreachable(ieee: str) -> str:
    """Return the id of the "thermostat does not accept writes" issue."""
    return f"{_UNREACHABLE_PREFIX}{ieee.replace(':', '')}"


def issue_id_firmware(ieee: str) -> str:
    """Return the id of the "firmware lacks a capability" issue."""
    return f"{_FIRMWARE_PREFIX}{ieee.replace(':', '')}"


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


def raise_unreachable(
    hass: HomeAssistant, ieee: str, placeholders: Mapping[str, Any]
) -> None:
    """Report a thermostat that keeps failing to accept writes."""
    _create(
        hass,
        issue_id_unreachable(ieee),
        ir.IssueSeverity.ERROR,
        "trv_unreachable",
        placeholders,
    )


def clear_unreachable(hass: HomeAssistant, ieee: str) -> None:
    """Remove the "thermostat does not accept writes" issue."""
    ir.async_delete_issue(hass, DOMAIN, issue_id_unreachable(ieee))


def raise_firmware(
    hass: HomeAssistant, ieee: str, placeholders: Mapping[str, Any]
) -> None:
    """Report a thermostat whose firmware lacks a capability."""
    _create(
        hass,
        issue_id_firmware(ieee),
        ir.IssueSeverity.WARNING,
        "trv_firmware",
        placeholders,
    )


def clear_firmware(hass: HomeAssistant, ieee: str) -> None:
    """Remove the "firmware lacks a capability" issue."""
    ir.async_delete_issue(hass, DOMAIN, issue_id_firmware(ieee))


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

    Covers ``trv_unreachable_*`` and ``trv_firmware_*``. ``keep_ieees`` holds
    the IEEE addresses of the thermostats of all configured rooms, so the issues
    of a thermostat that was removed from its room (or whose room was deleted)
    do not outlive it.
    """
    wanted = {issue_id_unreachable(ieee) for ieee in keep_ieees} | {
        issue_id_firmware(ieee) for ieee in keep_ieees
    }
    for domain, issue_id in list(ir.async_get(hass).issues):
        if (
            domain == DOMAIN
            and issue_id.startswith((_UNREACHABLE_PREFIX, _FIRMWARE_PREFIX))
            and issue_id not in wanted
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
