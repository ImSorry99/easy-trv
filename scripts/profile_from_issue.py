"""Turn a "Share a thermostat profile" issue into a community profile file.

Used by ``.github/workflows/community-profile.yml``. Reads the issue body from
``ISSUE_BODY`` and the author from ``ISSUE_AUTHOR``, checks the profile, writes
``community/profiles/<id>.yaml``, rebuilds the index and prints ``id`` and
``name`` for the workflow (``key=value`` lines). Exits with 1 and an error text
on stdout if the profile is not valid.
"""

import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.easy_trv.profiles.loader import (  # noqa: E402
    BUILTIN_DIR,
    ProfileLoadError,
    parse_profile_text,
)
from scripts.build_community_index import FOLDER, INDEX, build  # noqa: E402

_BLOCK = re.compile(r"```(?:yaml)?\s*\n(.*?)```", re.DOTALL)


def extract(body: str) -> str:
    """Return the YAML of the profile field of an issue form body."""
    match = _BLOCK.search(body)
    if match is None:
        msg = "No profile text found. Paste the profile into the form field."
        raise ValueError(msg)
    return match.group(1).strip() + "\n"


def main() -> int:
    """Write the profile file and the index, or report why not."""
    author = os.environ.get("ISSUE_AUTHOR", "")
    try:
        text = extract(os.environ.get("ISSUE_BODY", ""))
        data, profile = parse_profile_text(text, "issue")
    except (ValueError, ProfileLoadError) as err:
        print("error=" + " ".join(str(err).split()))  # noqa: T201
        return 1
    builtin = {path.stem for path in BUILTIN_DIR.glob("*.yaml")}
    if profile.id in builtin:
        print(f"error=The id {profile.id} belongs to a built-in profile.")  # noqa: T201
        return 1
    if "contributor" not in data and author:
        document = yaml.safe_load(text)
        document["contributor"] = author
        text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    (FOLDER / f"{profile.id}.yaml").write_text(text, encoding="utf-8")
    INDEX.write_text(build(), encoding="utf-8")
    print(f"id={profile.id}")  # noqa: T201
    print(f"name={profile.name}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
