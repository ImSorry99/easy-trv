"""Load device profiles from YAML documents."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import voluptuous as vol
import yaml

from custom_components.easy_trv.profiles.engine import Profile
from custom_components.easy_trv.profiles.schema import validate_profile_dict

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


def load_profiles_sync(user_dir: Path | None) -> LoadResult:
    """Load the built-in profiles (blocking file access)."""
    del user_dir  # user profile directories are scanned from the next commit on
    profiles: dict[str, Profile] = {}
    for path in sorted(BUILTIN_DIR.glob("*.yaml")):
        profile = Profile.from_dict(load_profile_file(path))
        profiles[profile.id] = profile
    return LoadResult(profiles=profiles, errors=())
