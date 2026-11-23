"""The community profiles in the repository and their index."""

import json
from typing import TYPE_CHECKING

import pytest
import voluptuous as vol

from custom_components.easy_trv.profiles.community import parse_index
from scripts import profile_from_issue
from scripts.build_community_index import FOLDER, INDEX, build

if TYPE_CHECKING:
    from pathlib import Path


def test_index_is_up_to_date() -> None:
    # Run scripts/build_community_index.py after changing a community profile.
    assert INDEX.read_text(encoding="utf-8") == build()


def test_index_is_readable_by_the_flow() -> None:
    entries = parse_index(json.loads(INDEX.read_text(encoding="utf-8")))
    files = {path.name for path in FOLDER.glob("*.yaml")}
    assert {entry.file for entry in entries} == files


def test_index_rejects_bad_documents() -> None:
    with pytest.raises(vol.Invalid):
        parse_index({"schema": 2, "profiles": []})
    with pytest.raises(vol.Invalid):
        parse_index({"schema": 1, "profiles": [{"id": "x", "file": "../x.yaml"}]})


def test_entries_sort_by_name_and_mark_beta() -> None:
    entries = parse_index(
        {
            "schema": 1,
            "profiles": [
                {
                    "id": "b",
                    "name": "Zeta",
                    "manufacturer": "M",
                    "model": "1",
                    "file": "b.yaml",
                },
                {
                    "id": "a",
                    "name": "alpha",
                    "manufacturer": "M",
                    "model": "2",
                    "file": "a.yaml",
                    "backend": "entity",
                },
            ],
        }
    )
    assert [entry.label for entry in entries] == ["alpha (M 2) [Beta]", "Zeta (M 1)"]


BODY = """### Profile (YAML)

```yaml
{text}
```

### Tested with

Zigbee2MQTT, two weeks
"""
ENTITY_PROFILE = """schema: 1
id: acme_z2m
name: Acme (Zigbee2MQTT)
backend: entity
match: { manufacturer: Acme, model: TRV-1 }
capabilities:
  external_temperature:
    intent: set_room_temperature
    entity: { domain: number, key: external_temperature_input }"""


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_from_issue, "FOLDER", tmp_path)
    monkeypatch.setattr(profile_from_issue, "INDEX", tmp_path / "index.json")
    monkeypatch.setattr(
        profile_from_issue, "build", lambda: '{"schema": 1, "profiles": []}\n'
    )
    return tmp_path


def run(monkeypatch, capsys, body: str) -> tuple[int, str]:
    monkeypatch.setenv("ISSUE_BODY", body)
    monkeypatch.setenv("ISSUE_AUTHOR", "someone")
    code = profile_from_issue.main()
    return code, capsys.readouterr().out


def test_issue_becomes_a_profile_file(folder: Path, monkeypatch, capsys) -> None:
    code, out = run(monkeypatch, capsys, BODY.format(text=ENTITY_PROFILE))
    assert code == 0
    assert out.splitlines() == ["id=acme_z2m", "name=Acme (Zigbee2MQTT)"]
    written = (folder / "acme_z2m.yaml").read_text()
    assert "contributor: someone" in written


def test_invalid_issue_reports_the_error(folder: Path, monkeypatch, capsys) -> None:
    code, out = run(monkeypatch, capsys, BODY.format(text="schema: 1"))
    assert code == 1
    assert out.startswith("error=")
    assert not list(folder.glob("*.yaml"))


@pytest.mark.usefixtures("folder")
def test_issue_without_text_is_reported(monkeypatch, capsys) -> None:
    code, out = run(monkeypatch, capsys, "nothing here")
    assert code == 1
    assert "No profile text" in out


@pytest.mark.usefixtures("folder")
def test_built_in_id_is_refused(monkeypatch, capsys) -> None:
    text = ENTITY_PROFILE.replace("id: acme_z2m", "id: sonoff_trvzb_native")
    code, out = run(monkeypatch, capsys, BODY.format(text=text))
    assert code == 1
    assert "built-in" in out
