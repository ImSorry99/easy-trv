"""Load device profiles from YAML documents."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import voluptuous as vol
import yaml

from custom_components.easy_trv.const import USER_PROFILES_DIR
from custom_components.easy_trv.profiles.engine import Profile
from custom_components.easy_trv.profiles.schema import validate_profile_dict

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

BUILTIN_DIR = Path(__file__).parent / "builtin"


class ProfileLoadError(Exception):
    """A profile file could not be read, validated or built."""

    def __init__(self, file: str, message: str) -> None:
        """Store the offending file name and the reason."""
        super().__init__(message)
        self.file = file


@dataclass(frozen=True)
class LoadResult:
    """Profiles keyed by id plus the files that were skipped."""

    profiles: dict[str, Profile]
    errors: tuple[ProfileLoadError, ...]


def load_profile_file(path: Path) -> dict[str, Any]:
    """Read and validate one profile document.

    Raises:
        ProfileLoadError: If the file cannot be read, is not valid YAML or
            violates the profile schema.

    """
    try:
        return validate_profile_dict(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (
        yaml.YAMLError,
        vol.Invalid,
        OSError,
        ValueError,
        TypeError,
        OverflowError,
    ) as err:
        raise ProfileLoadError(path.name, str(err)) from err


def _load_profile(path: Path) -> Profile:
    """Read, validate and build one profile.

    Raises:
        ProfileLoadError: If any step fails.

    """
    data = load_profile_file(path)
    try:
        return Profile.from_dict(data)
    except ValueError as err:
        raise ProfileLoadError(path.name, str(err)) from err


def _yaml_files(directory: Path | None) -> list[Path]:
    """List the ``*.yaml`` files of a directory in name order."""
    if directory is None or not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.yaml") if path.is_file())


def load_profiles_sync(user_dir: Path | None) -> LoadResult:
    """Load the built-in profiles, then those in ``user_dir`` (blocking I/O).

    A built-in profile that fails to load is a programming error and raises.
    A user file that is invalid, cannot be built or reuses the id of an
    earlier profile is recorded in ``errors`` and skipped.
    """
    profiles: dict[str, Profile] = {}
    for path in _yaml_files(BUILTIN_DIR):
        profile = _load_profile(path)
        profiles[profile.id] = profile
    builtin_ids = frozenset(profiles)

    errors: list[ProfileLoadError] = []
    for path in _yaml_files(user_dir):
        try:
            profile = _load_profile(path)
        except ProfileLoadError as err:
            errors.append(err)
            continue
        if profile.id in profiles:
            origin = "a built-in" if profile.id in builtin_ids else "an earlier"
            msg = f"profile id {profile.id!r} is already used by {origin} profile"
            errors.append(ProfileLoadError(path.name, msg))
            continue
        profiles[profile.id] = profile
    return LoadResult(profiles=profiles, errors=tuple(errors))


async def async_load_profiles(hass: HomeAssistant) -> LoadResult:
    """Load all profiles without blocking the event loop."""
    user_dir = Path(hass.config.path(USER_PROFILES_DIR))
    return await hass.async_add_executor_job(load_profiles_sync, user_dir)
