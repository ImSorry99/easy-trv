"""The thermostat profile subentry flow: upload, community download, edit."""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easy_trv.const import (
    COMMUNITY_BASE_URL,
    COMMUNITY_INDEX_FILE,
    CONF_PROFILE_YAML,
    DOMAIN,
    SUBENTRY_PROFILE,
)
from custom_components.easy_trv.profile_flow import profile_documents
from custom_components.easy_trv.profiles.loader import (
    BUILTIN_DIR,
    add_documents,
    load_profiles_sync,
)
from tests.helpers_entry import make_entry, use_config_dir

BUILTIN_TEXT = (BUILTIN_DIR / "sonoff_trvzb_native.yaml").read_text()
OWN_TEXT = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: my_trv", 1).replace(
    "name: Sonoff TRVZB (native)", "name: My TRV", 1
)
INDEX_URL = COMMUNITY_BASE_URL + COMMUNITY_INDEX_FILE


@pytest.fixture
def entry(hass, tmp_path):
    """An entry without rooms, not set up, with an empty profiles folder."""
    use_config_dir(hass, tmp_path)
    return make_entry(hass, [], {})


def profile_subentries(entry):
    return [
        sub
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_PROFILE
    ]


async def open_flow(hass, entry, choice):
    flows = hass.config_entries.subentries
    result = await flows.async_init(
        (entry.entry_id, SUBENTRY_PROFILE), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["create", "upload", "community"]
    return await flows.async_configure(result["flow_id"], {"next_step_id": choice})


async def submit(hass, result, data):
    return await hass.config_entries.subentries.async_configure(result["flow_id"], data)


# --- Upload ------------------------------------------------------------------


async def test_pasted_profile_is_stored_as_subentry(hass, entry) -> None:
    result = await open_flow(hass, entry, "upload")
    assert result["step_id"] == "upload"

    result = await submit(hass, result, {"yaml": OWN_TEXT})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My TRV"
    (sub,) = profile_subentries(entry)
    assert sub.data == {CONF_PROFILE_YAML: OWN_TEXT.strip()}


async def test_uploaded_file_is_read(hass, entry, tmp_path) -> None:
    upload = tmp_path / "upload.yaml"
    upload.write_text(OWN_TEXT)

    @contextmanager
    def fake_upload(_hass, file_id):
        assert file_id == "0d7f3c3e-0b4b-4d39-9e1f-1a2b3c4d5e6f"
        yield upload

    result = await open_flow(hass, entry, "upload")
    with patch(
        "custom_components.easy_trv.profile_flow.process_uploaded_file", fake_upload
    ):
        result = await submit(
            hass, result, {"file": "0d7f3c3e-0b4b-4d39-9e1f-1a2b3c4d5e6f"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My TRV"


async def test_empty_upload_asks_again(hass, entry) -> None:
    result = await open_flow(hass, entry, "upload")
    result = await submit(hass, result, {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_input"}


async def test_invalid_profile_shows_the_reason(hass, entry) -> None:
    result = await open_flow(hass, entry, "upload")
    result = await submit(hass, result, {"yaml": "schema: 1\nid: x\n"})
    assert result["errors"] == {"base": "invalid_profile"}
    assert "required key not provided" in result["description_placeholders"]["error"]


async def test_built_in_id_is_taken(hass, entry) -> None:
    result = await open_flow(hass, entry, "upload")
    result = await submit(hass, result, {"yaml": BUILTIN_TEXT})
    assert result["errors"] == {"base": "profile_id_taken"}


async def test_id_of_a_stored_profile_is_taken(hass, entry) -> None:
    result = await open_flow(hass, entry, "upload")
    await submit(hass, result, {"yaml": OWN_TEXT})

    result = await open_flow(hass, entry, "upload")
    result = await submit(hass, result, {"yaml": OWN_TEXT})
    assert result["errors"] == {"base": "profile_id_taken"}


# --- Community ---------------------------------------------------------------


def index(*entries):
    return json.dumps({"schema": 1, "profiles": list(entries)})


MY_ENTRY = {
    "id": "my_trv",
    "name": "My TRV",
    "manufacturer": "SONOFF",
    "model": "TRVZB",
    "file": "my_trv.yaml",
    "contributor": "someone",
}


async def test_community_profile_is_installed(hass, entry, aioclient_mock) -> None:
    aioclient_mock.get(INDEX_URL, text=index(MY_ENTRY))
    aioclient_mock.get(COMMUNITY_BASE_URL + "my_trv.yaml", text=OWN_TEXT)

    result = await open_flow(hass, entry, "community")
    assert result["step_id"] == "community"
    options = result["data_schema"].schema["profile"].config["options"]
    assert options == [{"value": "my_trv", "label": "My TRV (SONOFF TRVZB) by someone"}]

    result = await submit(hass, result, {"profile": "my_trv"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    (sub,) = profile_subentries(entry)
    assert sub.data[CONF_PROFILE_YAML] == OWN_TEXT


async def test_community_unreachable_aborts(hass, entry, aioclient_mock) -> None:
    aioclient_mock.get(INDEX_URL, status=500)
    result = await open_flow(hass, entry, "community")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "community_unreachable"


async def test_community_invalid_index_aborts(hass, entry, aioclient_mock) -> None:
    aioclient_mock.get(INDEX_URL, text="{}")
    result = await open_flow(hass, entry, "community")
    assert result["reason"] == "community_unreachable"


async def test_installed_community_profiles_are_not_offered(
    hass, entry, aioclient_mock
) -> None:
    builtin = {**MY_ENTRY, "id": "sonoff_trvzb_native", "file": "sonoff.yaml"}
    aioclient_mock.get(INDEX_URL, text=index(builtin))
    result = await open_flow(hass, entry, "community")
    assert result["reason"] == "community_empty"


async def test_failed_download_asks_again(hass, entry, aioclient_mock) -> None:
    aioclient_mock.get(INDEX_URL, text=index(MY_ENTRY))
    aioclient_mock.get(COMMUNITY_BASE_URL + "my_trv.yaml", status=404)
    result = await open_flow(hass, entry, "community")
    result = await submit(hass, result, {"profile": "my_trv"})
    assert result["errors"] == {"base": "download_failed"}


# --- Reconfigure -------------------------------------------------------------


def entry_with_profile(hass, tmp_path):
    use_config_dir(hass, tmp_path)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        version=3,
        title="Easy TRV",
        subentries_data=[
            ConfigSubentryData(
                data={CONF_PROFILE_YAML: OWN_TEXT},
                subentry_type=SUBENTRY_PROFILE,
                title="My TRV",
                unique_id=None,
            )
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def test_reconfigure_shows_and_updates_the_text(hass, tmp_path) -> None:
    entry = entry_with_profile(hass, tmp_path)
    (sub,) = profile_subentries(entry)
    flows = hass.config_entries.subentries
    result = await flows.async_init(
        (entry.entry_id, SUBENTRY_PROFILE),
        context={"source": "reconfigure", "subentry_id": sub.subentry_id},
    )
    assert result["step_id"] == "reconfigure"
    field = next(iter(result["data_schema"].schema))
    assert field.description == {"suggested_value": OWN_TEXT}

    # Keeping its own id is fine.
    renamed = OWN_TEXT.replace("name: My TRV", "name: My TRV v2", 1)
    result = await submit(hass, result, {"yaml": renamed})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    (sub,) = profile_subentries(entry)
    assert sub.title == "My TRV v2"
    assert sub.data[CONF_PROFILE_YAML] == renamed.strip()


async def test_profile_subentry_is_loaded_for_rooms(hass, tmp_path) -> None:
    # The loader takes the stored text next to the files.
    entry = entry_with_profile(hass, tmp_path)
    result = add_documents(load_profiles_sync(Path(tmp_path)), profile_documents(entry))
    assert set(result.profiles) == {"sonoff_trvzb_native", "my_trv"}
    assert result.errors == ()


def test_broken_or_duplicate_documents_are_reported() -> None:
    result = add_documents(
        load_profiles_sync(None), {"Broken": "schema: [", "Copy": BUILTIN_TEXT}
    )
    assert set(result.profiles) == {"sonoff_trvzb_native"}
    assert [error.file for error in result.errors] == ["Broken", "Copy"]
