"""The index of the community profiles in the repository.

Pure Python: the index is a JSON document that ``scripts/build_community_index.py``
writes from the profile files, and the profile flow reads it from GitHub.
"""

import re
from dataclasses import dataclass
from typing import Any

import voluptuous as vol

INDEX_SCHEMA_VERSION = 1

_FILE = vol.Match(re.compile(r"^[a-z][a-z0-9_]*\.yaml\Z", re.ASCII))
_TEXT = vol.All(str, vol.Length(min=1, max=200))

_ENTRY = vol.Schema(
    {
        vol.Required("id"): _TEXT,
        vol.Required("name"): _TEXT,
        vol.Required("manufacturer"): _TEXT,
        vol.Required("model"): _TEXT,
        vol.Required("file"): _FILE,
        vol.Optional("backend", default="zha"): vol.In(("zha", "entity")),
        vol.Optional("contributor", default=""): str,
    },
    extra=vol.REMOVE_EXTRA,
)

_INDEX = vol.Schema(
    {
        vol.Required("schema"): INDEX_SCHEMA_VERSION,
        vol.Required("profiles"): [_ENTRY],
    },
    extra=vol.REMOVE_EXTRA,
)


@dataclass(frozen=True)
class CommunityEntry:
    """One shared profile as the index lists it."""

    id: str
    name: str
    manufacturer: str
    model: str
    file: str
    backend: str
    contributor: str

    @property
    def label(self) -> str:
        """Return the text shown in the list."""
        label = f"{self.name} ({self.manufacturer} {self.model})"
        if self.backend == "entity":
            label += " [Beta]"
        if self.contributor:
            label += f" by {self.contributor}"
        return label


def parse_index(data: Any) -> tuple[CommunityEntry, ...]:
    """Validate a parsed index document and return its entries by name.

    Raises:
        vol.Invalid: If the document is not a valid index.

    """
    validated = _INDEX(data)
    entries = (CommunityEntry(**entry) for entry in validated["profiles"])
    return tuple(sorted(entries, key=lambda entry: entry.name.casefold()))
