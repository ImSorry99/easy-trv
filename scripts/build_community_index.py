"""Write community/profiles/index.json from the profile files next to it.

Run from the repository root: ``uv run python scripts/build_community_index.py``.
With ``--check`` it only reports whether the index is up to date (for CI).
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.easy_trv.profiles.loader import (  # noqa: E402
    parse_profile_text,
)

FOLDER = ROOT / "community" / "profiles"
INDEX = FOLDER / "index.json"


def build() -> str:
    """Return the index text for the profile files in the folder.

    Raises:
        ProfileLoadError: If a file is not a valid profile.

    """
    entries = []
    for path in sorted(FOLDER.glob("*.yaml")):
        data, profile = parse_profile_text(path.read_text(encoding="utf-8"), path.name)
        entries.append(
            {
                "id": profile.id,
                "name": profile.name,
                "manufacturer": data["match"]["manufacturer"],
                "model": data["match"]["model"],
                "file": path.name,
                "backend": data.get("backend", "zha"),
                "contributor": str(data.get("contributor", "")),
            }
        )
    return json.dumps({"schema": 1, "profiles": entries}, indent=2) + "\n"


def main() -> int:
    """Write the index, or with ``--check`` compare it."""
    text = build()
    if "--check" in sys.argv:
        if INDEX.read_text(encoding="utf-8") != text:
            print("community/profiles/index.json is out of date")  # noqa: T201
            return 1
        return 0
    INDEX.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
