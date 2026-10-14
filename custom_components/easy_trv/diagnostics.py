"""Diagnostics for Easy TRV."""

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.easy_trv import EasyTrvConfigEntry


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: EasyTrvConfigEntry
) -> dict[str, Any]:
    """Return the options, the loaded profiles and the state of every room.

    Each room also lists its resolved settings (room value, else global option,
    else built-in default), i.e. what the room actually runs with.

    Home Assistant also asks for entries that failed to set up (they have no
    runtime data); those report their options and nothing else.
    """
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return {
            "options": dict(entry.options),
            "profiles": [],
            "profile_errors": [],
            "rooms": {},
        }
    return {
        "options": dict(entry.options),
        "profiles": sorted(runtime.profiles.profiles),
        "profile_errors": [
            {"file": error.file, "error": str(error)}
            for error in runtime.profiles.errors
        ],
        "rooms": {
            subentry_id: {
                **controller.snapshot(),
                "settings": asdict(runtime.settings[subentry_id]),
            }
            for subentry_id, controller in runtime.rooms.items()
        },
    }
