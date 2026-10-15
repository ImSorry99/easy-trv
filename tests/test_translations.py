import json
import re
from pathlib import Path
from typing import Any

import pytest

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "easy_trv"
STRINGS = INTEGRATION / "strings.json"
EN = INTEGRATION / "translations" / "en.json"
DE = INTEGRATION / "translations" / "de.json"
PLACEHOLDER = re.compile(r"\{[^{}]*\}")
# hassfest's rule for the keys of selector options, entity states and issues.
TRANSLATION_KEY = re.compile(r"^(?!.+[_-]{2})(?![_-])[a-z0-9-_]+(?<![_-])$")

# The labels are keyed by the fixed vocabulary of intents, not by the names a
# profile gives its capabilities.
INTENTS = {
    "set_room_temperature",
    "set_source",
    "set_window_detection",
    "set_hysteresis",
    "set_adaptive_mode",
    "set_frost_temperature",
    "set_setpoint",
    "set_hvac_mode",
}

ROOM_ERRORS = {
    "no_trv",
    "not_zha",
    "no_profile",
    "profile_mismatch",
    "trv_in_other_room",
    "name_exists",
    "name_required",
    "timeout_range",
}


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def leaves(node: Any, prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], str]:
    """Flatten nested dicts to ``{path: text}``."""
    if isinstance(node, dict):
        flat: dict[tuple[str, ...], str] = {}
        for key, value in node.items():
            flat.update(leaves(value, (*prefix, key)))
        return flat
    assert isinstance(node, str), f"{'.'.join(prefix)} is not a string"
    return {prefix: node}


def test_translation_files_consistent() -> None:
    strings, en, de = load(STRINGS), load(EN), load(DE)
    assert strings == en
    assert leaves(strings).keys() == leaves(de).keys()


def test_strings_and_english_translation_are_byte_identical() -> None:
    assert STRINGS.read_bytes() == EN.read_bytes()


def test_german_keeps_the_placeholders_of_english() -> None:
    en, de = leaves(load(EN)), leaves(load(DE))
    for path, text in en.items():
        assert sorted(PLACEHOLDER.findall(de[path])) == sorted(
            PLACEHOLDER.findall(text)
        ), ".".join(path)


def test_no_translation_is_empty() -> None:
    for path in (EN, DE):
        for key, text in leaves(load(path)).items():
            assert text.strip(), f"{path.name}: {'.'.join(key)} is empty"


@pytest.mark.parametrize("path", [EN, DE])
def test_every_flow_error_and_abort_is_translated(path: Path) -> None:
    data = load(path)
    room = data["config_subentries"]["room"]
    assert set(room["error"]) == ROOM_ERRORS
    assert set(room["abort"]) == {"reconfigure_successful", "already_configured"}
    assert set(data["options"]["error"]) == {"timeout_range"}
    assert set(data["config"]["abort"]) == {"single_instance_allowed"}


@pytest.mark.parametrize("path", [STRINGS, EN, DE])
def test_room_subentry_has_no_title_string(path: Path) -> None:
    # hassfest only tolerates this key for custom integrations, with a warning.
    room = load(path)["config_subentries"]["room"]
    assert "title" not in room
    assert "entry_type" in room


@pytest.mark.parametrize("path", [EN, DE])
def test_flow_steps_cover_all_form_fields(path: Path) -> None:
    data = load(path)
    room = data["config_subentries"]["room"]
    settings_fields = {
        "profile",
        "window_detection",
        "hysteresis",
        "adaptive_mode",
        "frost_temperature",
        "refresh_minutes",
        "verify_delay_seconds",
        "max_attempts",
        "min_delta_kelvin",
        "sensor_timeout_minutes",
    }
    entity_fields = {"trvs", "temp_sensors", "humidity_sensors"}
    assert set(room["step"]["entities"]["data"]) == entity_fields
    assert set(room["step"]["reconfigure"]["data"]) == {"name", *entity_fields}
    assert set(room["step"]["settings"]["data"]) == settings_fields
    assert set(room["step"]["settings"]["data_description"]) == settings_fields
    assert set(room["step"]["from_area"]["data"]) == {"area_id"}
    assert set(room["step"]["custom"]["data"]) == {"name"}
    assert set(room["step"]["user"]["menu_options"]) == {"from_area", "custom"}
    assert set(room["initiate_flow"]) == {"user", "reconfigure"}
    options = data["options"]["step"]["init"]["sections"]
    assert set(options) == {"writes", "thermostat"}
    for section in options.values():
        assert set(section) == {"name", "description", "data", "data_description"}
        assert set(section["data"]) == set(section["data_description"])
    assert set(options["writes"]["data"]) == {
        "refresh_minutes",
        "verify_delay_seconds",
        "max_attempts",
        "min_delta_kelvin",
        "sensor_timeout_minutes",
    }
    assert set(options["thermostat"]["data"]) == {
        "window_detection",
        "hysteresis",
        "adaptive_mode",
        "frost_temperature",
    }
    assert set(data["selector"]["window_detection"]["options"]) == {
        "global",
        "on",
        "off",
        "leave",
    }
    assert set(data["selector"]["adaptive_mode"]["options"]) == {
        "global",
        "on",
        "off",
        "leave",
    }
    assert set(data["selector"]["hysteresis"]["options"]) == {
        "global",
        "0_2",
        "0_4",
        "0_6",
        "0_8",
        "1_0",
    }
    assert set(data["selector"]["capability"]["options"]) == INTENTS
    assert set(data["selector"]["profile"]["options"]) == {"global", "auto"}
    assert set(data["selector"]["sensor_timeout"]["options"]) == {"off"}
    # Existing sections of the earlier tasks stay in place.
    assert set(data["entity"]["sensor"]["room_status"]["state"]) == {
        "ok",
        "degraded",
        "error",
    }
    assert set(data["issues"]) == {
        "trv_unreachable",
        "trv_firmware",
        "zha_api_incompatible",
        "profile_invalid",
        "room_invalid",
        "settings_migrated",
        "window_open_long",
        "window_contact_suspect",
    }


@pytest.mark.parametrize("path", [STRINGS, EN, DE])
def test_translation_keys_pass_the_hassfest_rule(path: Path) -> None:
    data = load(path)
    keys = list(data["issues"])
    for selector in data["selector"].values():
        keys.extend(selector["options"])
    for platform in data["entity"].values():
        for entity in platform.values():
            keys.extend(entity.get("state", {}))
    assert {"global", "leave", "0_2", "1_0", "ok", "trv_unreachable"} <= set(keys)
    assert [key for key in keys if not TRANSLATION_KEY.match(key)] == []


def test_hassfest_rule_rejects_dotted_keys() -> None:
    assert TRANSLATION_KEY.match("0_4")
    assert not TRANSLATION_KEY.match("0.4")
    assert not TRANSLATION_KEY.match("_leave")
    assert not TRANSLATION_KEY.match("a__b")


def test_german_translation_is_actually_german() -> None:
    de = load(DE)
    room = de["config_subentries"]["room"]
    assert room["initiate_flow"] == {
        "user": "Raum hinzufügen",
        "reconfigure": "Raum bearbeiten",
    }
    assert room["step"]["user"]["menu_options"] == {
        "from_area": "Aus Bereich",
        "custom": "Eigener Raum",
    }
    assert de["entity"]["sensor"]["room_status"]["state"] == {
        "ok": "OK",
        "degraded": "Eingeschränkt",
        "error": "Fehler",
    }


def test_room_device_name_is_translated() -> None:
    assert load(EN)["device"]["room"]["name"] == "{room} heating"
    assert load(DE)["device"]["room"]["name"] == "Heizung {room}"


@pytest.mark.parametrize("path", [STRINGS, EN, DE])
def test_thermostat_issues_name_the_setting_and_the_thermostat(path: Path) -> None:
    issues = load(path)["issues"]
    expected = {
        "trv_unreachable": {
            "room",
            "setting",
            "thermostat",
            "attempts",
            "error",
        },
        "trv_firmware": {"room", "setting", "thermostat", "required", "current"},
    }
    for key, allowed in expected.items():
        used = {
            name.strip("{}")
            for text in issues[key].values()
            for name in PLACEHOLDER.findall(text)
        }
        assert used <= allowed, f"{key}: {used - allowed}"
        assert {"setting", "thermostat"} <= used


@pytest.mark.parametrize("path", [STRINGS, EN, DE])
def test_window_issues_name_the_room_and_the_numbers(path: Path) -> None:
    issues = load(path)["issues"]
    expected = {
        "window_open_long": ({"room", "minutes"}, {"room", "minutes"}),
        "window_contact_suspect": ({"room", "minutes", "drop"}, {"room"}),
    }
    for key, (allowed, required) in expected.items():
        assert set(issues[key]) == {"title", "description"}
        used = {
            name.strip("{}")
            for text in issues[key].values()
            for name in PLACEHOLDER.findall(text)
        }
        assert used <= allowed, f"{key}: {used - allowed}"
        assert required <= used, f"{key}: missing {required - used}"
    # The suspect issue also says by how much the room should have cooled.
    suspect = " ".join(issues["window_contact_suspect"].values())
    assert "{drop}" in suspect


def test_window_issue_titles() -> None:
    en, de = load(EN)["issues"], load(DE)["issues"]
    assert en["window_open_long"]["title"] == (
        "Window open in {room} for {minutes} minutes"
    )
    assert en["window_contact_suspect"]["title"] == (
        "The window contact in {room} reports open, but the room is not cooling"
    )
    assert de["window_open_long"]["title"] == (
        "Fenster in {room} seit {minutes} Minuten offen"
    )
    assert de["window_contact_suspect"]["title"] == (
        "Fensterkontakt in {room} meldet offen, aber der Raum kühlt nicht ab"
    )


def test_capability_labels_en() -> None:
    assert load(EN)["selector"]["capability"]["options"] == {
        "set_room_temperature": "Room temperature",
        "set_source": "Temperature source",
        "set_window_detection": "Thermostat's open-window detection",
        "set_hysteresis": "Hysteresis",
        "set_adaptive_mode": "Adaptive mode",
        "set_frost_temperature": "Frost protection temperature",
        "set_setpoint": "Target temperature",
        "set_hvac_mode": "Mode",
    }


def test_capability_labels_de() -> None:
    assert load(DE)["selector"]["capability"]["options"] == {
        "set_room_temperature": "Raumtemperatur",
        "set_source": "Temperaturquelle",
        "set_window_detection": "Fenster-offen-Erkennung des Thermostats",
        "set_hysteresis": "Hysterese",
        "set_adaptive_mode": "Adaptiver Modus",
        "set_frost_temperature": "Frostschutztemperatur",
        "set_setpoint": "Solltemperatur",
        "set_hvac_mode": "Modus",
    }


def test_window_detection_option_texts_de() -> None:
    options = load(DE)["selector"]["window_detection"]["options"]
    assert options == {
        "global": "Global",
        "on": "Erkennung an",
        "off": "Erkennung aus",
        "leave": "Im Thermostat unverändert lassen",
    }


def test_adaptive_mode_option_texts_are_state_like() -> None:
    assert load(EN)["selector"]["adaptive_mode"]["options"] == {
        "global": "Global",
        "on": "Adaptive on",
        "off": "Adaptive off",
        "leave": "Leave as set in the thermostat",
    }
    assert load(DE)["selector"]["adaptive_mode"]["options"] == {
        "global": "Global",
        "on": "Adaptiv an",
        "off": "Adaptiv aus",
        "leave": "Im Thermostat unverändert lassen",
    }
