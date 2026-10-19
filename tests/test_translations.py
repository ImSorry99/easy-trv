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
    "setpoint_range",
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
    assert set(data["options"]["error"]) == {"timeout_range", "setpoint_range"}
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
        "hide_thermostat_entities",
        "refresh_minutes",
        "verify_delay_seconds",
        "max_attempts",
        "min_delta_kelvin",
        "sensor_timeout_minutes",
        "setpoint_range",
        "setpoint_min",
        "setpoint_max",
        "window_open_delay_seconds",
        "window_warn_minutes",
        "window_warn_colder",
        "window_check_minutes",
        "window_check_drop_kelvin",
        "outdoor_cutoff",
        "outdoor_cutoff_celsius",
        "statistics",
    }
    entity_fields = {"trvs", "temp_sensors", "humidity_sensors", "window_contacts"}
    assert set(room["step"]["entities"]["data"]) == entity_fields
    assert set(room["step"]["reconfigure"]["data"]) == {
        "name",
        "area_id",
        *entity_fields,
    }
    assert set(room["step"]["settings"]["data"]) == settings_fields
    assert set(room["step"]["settings"]["data_description"]) == settings_fields
    assert set(room["step"]["from_area"]["data"]) == {"area_id"}
    assert set(room["step"]["custom"]["data"]) == {"name", "area_id"}
    # The optional area field says what it does.
    assert "area_id" in room["step"]["custom"]["data_description"]
    assert "area_id" in room["step"]["reconfigure"]["data_description"]
    assert set(room["step"]["user"]["menu_options"]) == {"from_area", "custom"}
    assert set(room["initiate_flow"]) == {"user", "reconfigure"}
    options = data["options"]["step"]["init"]["sections"]
    assert set(options) == {
        "writes",
        "thermostat",
        "room_thermostat",
        "windows_outdoor",
        "statistics",
    }
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
        "hide_thermostat_entities",
    }
    assert set(options["room_thermostat"]["data"]) == {
        "setpoint_range",
        "setpoint_min",
        "setpoint_max",
    }
    assert set(options["windows_outdoor"]["data"]) == {
        "window_open_delay_seconds",
        "window_warn_minutes",
        "window_warn_colder",
        "window_check_minutes",
        "window_check_drop_kelvin",
        "outdoor_sensor",
        "outdoor_cutoff_celsius",
    }
    assert set(options["statistics"]["data"]) == {"statistics"}
    assert set(data["selector"]["outdoor_cutoff"]["options"]) == {"global", "off"}
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
    assert set(data["selector"]["hide_thermostat_entities"]["options"]) == {
        "global",
        "on",
        "off",
    }
    assert set(data["selector"]["statistics"]["options"]) == {
        "global",
        "off",
        "hidden",
        "visible",
    }
    assert set(data["selector"]["capability"]["options"]) == INTENTS
    assert set(data["selector"]["profile"]["options"]) == {"global", "auto"}
    assert set(data["selector"]["sensor_timeout"]["options"]) == {"off"}
    # The select entity is gone: frost protection is the mode ``auto`` of the
    # room thermostat, and this is its label.
    assert set(data["entity"]) == {"climate", "sensor"}
    assert set(data["entity"]["climate"]) == {"room"}
    assert set(data["entity"]["climate"]["room"]) == {"state"}
    assert set(data["entity"]["climate"]["room"]["state"]) == {"auto"}
    # The status sensor is merged into the heating state sensor; the statistics
    # sensors of 0.6 come next to it.
    assert set(data["entity"]["sensor"]) == {
        "room_state",
        "deviation",
        "heating_time",
        "window_openings",
        "window_open_time",
        "writes",
        "write_failures",
        "dropouts",
    }
    assert set(data["selector"]["hint"]["options"]) == HINT_KEYS
    assert set(data["issues"]) == {
        "trv_unreachable",
        "trv_firmware",
        "zha_api_incompatible",
        "profile_invalid",
        "room_invalid",
        "settings_migrated",
        "window_open_long",
        "window_contact_suspect",
        "window_contact_unavailable",
        "schedule_thermostat_off",
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
    assert {
        "global",
        "leave",
        "0_2",
        "1_0",
        "restricted_window_phase",
        "why_unavailable",
        "trv_unreachable",
    } <= set(keys)
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
    assert de["entity"]["climate"]["room"] == {"state": {"auto": "Frostschutz"}}


def test_frost_protection_is_the_label_of_the_auto_mode() -> None:
    assert load(EN)["entity"]["climate"]["room"]["state"] == {
        "auto": "Frost protection"
    }
    assert load(DE)["entity"]["climate"]["room"]["state"] == {"auto": "Frostschutz"}


def test_the_auto_mode_has_the_frost_protection_icon() -> None:
    icons = json.loads((INTEGRATION / "icons.json").read_text())

    assert icons["entity"]["climate"]["room"]["state"]["auto"] == (
        "mdi:snowflake-thermometer"
    )
    assert "select" not in icons["entity"]


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
    expected_without_setting = {"schedule_thermostat_off": {"room", "thermostat"}}
    for key, allowed in expected_without_setting.items():
        used = {
            name.strip("{}")
            for text in issues[key].values()
            for name in PLACEHOLDER.findall(text)
        }
        assert used == allowed, f"{key}: {used}"
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
        "window_contact_unavailable": ({"room", "contacts"}, {"room", "contacts"}),
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
    # The room is in the title, the silent contacts in the text.
    silent = issues["window_contact_unavailable"]
    assert "{room}" in silent["title"]
    assert "{contacts}" in silent["description"]


def test_the_silent_contact_issue_says_what_stays_as_it_is() -> None:
    de = load(DE)["issues"]["window_contact_unavailable"]
    assert de["title"] == "Fensterkontakt im Raum {room} nicht erreichbar"
    assert de["description"] == (
        "Seit mindestens 10 Minuten meldet im Raum {room} ein Fensterkontakt "
        "keinen Zustand. Gerade ohne Meldung: {contacts}. Easy TRV behandelt "
        "einen solchen Kontakt so, wie er zuletzt war: Ein zuletzt geschlossener "
        "oder nie gesehener Kontakt gilt als geschlossen, ein Öffnen schaltet die "
        "Heizung dann nicht ab. Ein zuletzt offener Kontakt hält die Heizung aus, "
        "bis er sich wieder meldet. Prüfe Batterie und Funkverbindung."
    )
    en = load(EN)["issues"]["window_contact_unavailable"]
    assert en["title"] == "Window contact in {room} is not reachable"
    assert en["description"] == (
        "For at least 10 minutes a window contact in {room} has not reported a "
        "state. Currently silent: {contacts}. Easy TRV treats such a contact as "
        "it was last: one last seen closed, or never seen, counts as closed, so "
        "opening that window does not switch the heating off. One last seen open "
        "keeps the heating off until it reports again. Check the contact's "
        "battery and radio connection."
    )


STATE_NAMES_EN = {
    "fault": "Fault",
    "off_manual": "Off (manual)",
    "off_window": "Off: window/door open",
    "off_outdoor": "Off: outdoor temperature",
    "off_away": "Off: away",
    "window_pending": "Window open, switching off soon",
    "emergency": "Emergency mode",
    "restricted_unavailable": "Restricted: thermostat not reachable",
    "restricted_contact": "Restricted: window contact silent",
    "restricted_unsupported": "Restricted: thermostat lacks a function",
    "restricted_schedule": "Restricted: thermostat on its schedule",
    "restricted_window_phase": "Restricted: thermostat's window detection active",
    "boost": "Manual (boost)",
    "setback_away": "Lowered: away",
    "frost": "Frost protection",
    "heating": "Heating",
}
STATE_NAMES_DE = {
    "fault": "Störung",
    "off_manual": "Aus (manuell)",
    "off_window": "Aus: Fenster/Tür offen",
    "off_outdoor": "Aus: Außentemperatur",
    "off_away": "Aus: Abwesenheit",
    "window_pending": "Fenster offen, schaltet gleich ab",
    "emergency": "Notbetrieb",
    "restricted_unavailable": "Eingeschränkt: TRV nicht erreichbar",
    "restricted_contact": "Eingeschränkt: Fensterkontakt meldet nichts",
    "restricted_unsupported": "Eingeschränkt: TRV-Funktion fehlt",
    "restricted_schedule": "Eingeschränkt: TRV im Wochenplan",
    "restricted_window_phase": "Eingeschränkt: TRV-Fenstererkennung aktiv",
    "boost": "Handbetrieb (Boost)",
    "setback_away": "Abgesenkt: abwesend",
    "frost": "Frostschutz",
    "heating": "Heizbetrieb",
}


ATTRIBUTE_NAMES_EN = {
    "hint": "Hint",
    "reasons": "Reasons",
    "since": "Since",
    "window_off_at": "Switches off at",
    "thermostats": "Deviating thermostats",
    "silent_contacts": "Silent window contacts",
    "active_sensor": "Active sensor",
    "source": "Temperature source",
    "setpoint_source": "Setpoint source",
    "in_sync": "In sync",
    "last_error": "Last error",
}
ATTRIBUTE_NAMES_DE = {
    "hint": "Hinweis",
    "reasons": "Gründe",
    "since": "Seit",
    "window_off_at": "Abschaltung um",
    "thermostats": "Abweichende Thermostate",
    "silent_contacts": "Stumme Fensterkontakte",
    "active_sensor": "Aktiver Sensor",
    "source": "Temperaturquelle",
    "setpoint_source": "Sollwert-Quelle",
    "in_sync": "Synchron",
    "last_error": "Letzter Fehler",
}
HINT_EN = {
    "fault": "{name}: error",
    "unavailable": "{name} not reachable",
    "contact": "{name} reports nothing ({why})",
    "unsupported": "{name}: lacks a function ({functions})",
    "schedule": "{name} runs its own schedule",
    "window_phase": "{name}: own window detection active",
    "away": "{name} away",
    "why_unavailable": "unavailable",
    "why_unknown": "unknown",
    "why_missing": "does not exist",
}
HINT_DE = {
    "fault": "{name}: Fehler",
    "unavailable": "{name} nicht erreichbar",
    "contact": "{name} meldet nichts ({why})",
    "unsupported": "{name}: Funktion fehlt ({functions})",
    "schedule": "{name} läuft im eigenen Wochenplan",
    "window_phase": "{name}: eigene Fenstererkennung aktiv",
    "away": "{name} abwesend",
    "why_unavailable": "nicht verfügbar",
    "why_unknown": "unbekannt",
    "why_missing": "gibt es nicht",
}
HINT_KEYS = set(HINT_EN)


def test_the_heating_state_sensor_is_named_and_its_states_translated() -> None:
    en = load(EN)["entity"]["sensor"]["room_state"]
    de = load(DE)["entity"]["sensor"]["room_state"]
    assert en["name"] == "Heating state"
    assert de["name"] == "Heizungszustand"
    # The states come in the order of the sensor's options.
    assert list(en["state"].items()) == list(STATE_NAMES_EN.items())
    assert de["state"] == STATE_NAMES_DE
    assert list(en["state_attributes"].items()) == [
        (key, {"name": name}) for key, name in ATTRIBUTE_NAMES_EN.items()
    ]
    assert list(de["state_attributes"].items()) == [
        (key, {"name": name}) for key, name in ATTRIBUTE_NAMES_DE.items()
    ]


def test_the_status_sensor_is_gone_from_the_translations() -> None:
    for path in (STRINGS, EN, DE):
        assert "room_status" not in load(path)["entity"]["sensor"]
    icons = json.loads((INTEGRATION / "icons.json").read_text())
    assert "room_status" not in icons["entity"]["sensor"]


@pytest.mark.parametrize(("path", "phrases"), [(EN, HINT_EN), (DE, HINT_DE)])
def test_the_hint_phrases_are_translated(path: Path, phrases: dict[str, str]) -> None:
    assert load(path)["selector"]["hint"]["options"] == phrases


def test_the_window_hold_texts_say_that_all_thermostats_go_off() -> None:
    en = load(EN)["issues"]["window_open_long"]["description"]
    de = load(DE)["issues"]["window_open_long"]["description"]
    assert "all its thermostats stay off" in en
    assert "stay off afterwards" in en
    assert "alle Thermostate bleiben aus" in de
    assert "bleiben danach aus" in de
    off = load(EN)["issues"]["schedule_thermostat_off"]
    assert (
        off["title"] == "{thermostat} in {room} stays off after the window was closed"
    )
    assert "ZHA" in off["description"]
    assert load(DE)["issues"]["schedule_thermostat_off"]["title"] == (
        "{thermostat} in {room} bleibt nach dem Lüften aus"
    )


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


@pytest.mark.parametrize("path", [EN, DE])
def test_room_and_options_forms_explain_window_contacts_and_cutoff(path: Path) -> None:
    data = load(path)
    room = data["config_subentries"]["room"]["step"]
    sections = data["options"]["step"]["init"]["sections"]
    for step in ("entities", "reconfigure"):
        assert room[step]["data_description"]["window_contacts"].strip()
    settings = room["settings"]["data_description"]
    for key in (
        "setpoint_min",
        "setpoint_max",
        "window_open_delay_seconds",
        "window_warn_minutes",
        "window_check_minutes",
        "window_check_drop_kelvin",
        "outdoor_cutoff",
        "outdoor_cutoff_celsius",
    ):
        assert settings[key].strip()
    # Every number says what Global currently is (rooms) or what empty means (options).
    for key in ("setpoint_min", "window_check_drop_kelvin", "outdoor_cutoff_celsius"):
        assert f"{{global_{key}}}" in settings[key]
    windows = sections["windows_outdoor"]["data_description"]
    assert "{default_outdoor_cutoff_celsius}" in windows["outdoor_cutoff_celsius"]
    assert "{default_window_warn_minutes}" in windows["window_warn_minutes"]
    assert "{default_window_open_delay_seconds}" in windows["window_open_delay_seconds"]
    assert "{global_window_open_delay_seconds}" in settings["window_open_delay_seconds"]
    assert room["settings"]["data_description"]["setpoint_max"]


def test_window_contact_texts_name_another_area_en() -> None:
    en = load(EN)["config_subentries"]["room"]["step"]
    text = en["entities"]["data_description"]["window_contacts"]
    assert "another area" in text
    assert "kitchen" in text.lower()
    assert en["reconfigure"]["data_description"]["window_contacts"] == text


def test_window_contact_texts_name_another_area_de() -> None:
    de = load(DE)["config_subentries"]["room"]["step"]
    text = de["entities"]["data_description"]["window_contacts"]
    assert "anderen Bereich" in text
    assert "die Küche, wenn sie mit dem Wohnzimmer verbunden ist" in text
    assert de["reconfigure"]["data_description"]["window_contacts"] == text


def test_setpoint_range_error_texts() -> None:
    en, de = load(EN), load(DE)
    for data, text in (
        (en, "The minimum target temperature must be below the maximum."),
        (de, "Die minimale Solltemperatur muss unter der maximalen liegen."),
    ):
        assert data["config_subentries"]["room"]["error"]["setpoint_range"] == text
        assert data["options"]["error"]["setpoint_range"] == text


def test_window_and_outdoor_field_texts_en() -> None:
    en = load(EN)
    settings = en["config_subentries"]["room"]["step"]["settings"]["data_description"]
    assert "0 = never" in settings["window_warn_minutes"]
    assert "Repairs" in settings["window_warn_minutes"]
    assert "0 = off" in settings["window_check_minutes"]
    assert "0.5 K below" in settings["outdoor_cutoff_celsius"]
    assert "switches off" in settings["outdoor_cutoff_celsius"]


def test_window_and_outdoor_field_texts_de() -> None:
    de = load(DE)
    settings = de["config_subentries"]["room"]["step"]["settings"]["data_description"]
    assert "0 = nie" in settings["window_warn_minutes"]
    assert "Reparaturen" in settings["window_warn_minutes"]
    assert "0 = aus" in settings["window_check_minutes"]
    assert "0,5 K unter" in settings["outdoor_cutoff_celsius"]
    assert "schaltet" in settings["outdoor_cutoff_celsius"]


def test_window_open_delay_texts() -> None:
    en, de = load(EN), load(DE)
    en_room = en["config_subentries"]["room"]["step"]["settings"]
    de_room = de["config_subentries"]["room"]["step"]["settings"]
    label = "Delay before a window counts as open (seconds, 0 = immediately)"
    assert en_room["data"]["window_open_delay_seconds"] == label
    assert de_room["data"]["window_open_delay_seconds"] == (
        "Verzögerung, bis ein Fenster als offen gilt (Sekunden, 0 = sofort)"
    )
    for data in (en, de):
        section = data["options"]["step"]["init"]["sections"]["windows_outdoor"]
        assert (
            section["data"]["window_open_delay_seconds"]
            == (
                data["config_subentries"]["room"]["step"]["settings"]["data"][
                    "window_open_delay_seconds"
                ]
            )
        )


def test_field_references_use_the_name_of_the_field() -> None:
    en, de = load(EN), load(DE)
    for data, name, below in (
        (en, "Cooling expected by the check (K)", "the cooling below"),
        (de, "Erwartete Abkühlung bei der Prüfung (K)", "Abkühlung unten"),
    ):
        room = data["config_subentries"]["room"]["step"]["settings"]
        windows = data["options"]["step"]["init"]["sections"]["windows_outdoor"]
        assert room["data"]["window_check_drop_kelvin"] == name
        for texts in (room["data_description"], windows["data_description"]):
            assert name in texts["window_check_minutes"]
            assert below not in texts["window_check_minutes"]


def test_the_room_cutoff_text_shows_the_global_value_and_the_own_threshold() -> None:
    for path, own in ((EN, "unless you enter"), (DE, "solange du unten keine")):
        text = load(path)["config_subentries"]["room"]["step"]["settings"][
            "data_description"
        ]["outdoor_cutoff"]
        assert "{global_outdoor_cutoff_celsius}" in text
        assert own in text
        assert not text.startswith(("Global uses", "Global verwendet"))


def test_german_setpoint_labels() -> None:
    de = load(DE)
    settings = de["config_subentries"]["room"]["step"]["settings"]["data"]
    section = de["options"]["step"]["init"]["sections"]["room_thermostat"]["data"]
    for texts in (settings, section):
        assert texts["setpoint_min"] == "Minimale Solltemperatur (°C)"
        assert texts["setpoint_max"] == "Maximale Solltemperatur (°C)"


def test_hide_setting_texts_en() -> None:
    en = load(EN)
    room = en["config_subentries"]["room"]["step"]["settings"]
    section = en["options"]["step"]["init"]["sections"]["thermostat"]
    assert room["data"]["hide_thermostat_entities"] == "Hide thermostat entities"
    assert section["data"]["hide_thermostat_entities"] == "Hide thermostat entities"
    text = section["data_description"]["hide_thermostat_entities"]
    assert "no longer appear on automatic dashboards" in text
    assert "shows them again" in text
    # The room form also says what Global is.
    assert room["data_description"]["hide_thermostat_entities"] == (
        f"{text} Global is currently {{global_hide_thermostat_entities}}."
    )
    assert en["selector"]["hide_thermostat_entities"]["options"] == {
        "global": "Global",
        "on": "Hidden",
        "off": "Shown",
    }


def test_hide_setting_texts_de() -> None:
    de = load(DE)
    room = de["config_subentries"]["room"]["step"]["settings"]
    section = de["options"]["step"]["init"]["sections"]["thermostat"]
    assert room["data"]["hide_thermostat_entities"] == "TRV-Entitäten ausblenden"
    assert section["data"]["hide_thermostat_entities"] == "TRV-Entitäten ausblenden"
    text = (
        "Blendet die Klima-Entität der TRVs und die Einstellungen aus, die Easy TRV "
        "steuert. Sie arbeiten weiter, stehen aber nicht mehr auf automatischen "
        "Dashboards. Ausschalten blendet sie wieder ein."
    )
    assert section["data_description"]["hide_thermostat_entities"] == text
    assert room["data_description"]["hide_thermostat_entities"] == (
        f"{text} Global ist derzeit {{global_hide_thermostat_entities}}."
    )
    assert de["selector"]["hide_thermostat_entities"]["options"] == {
        "global": "Global",
        "on": "Ausgeblendet",
        "off": "Eingeblendet",
    }


STATISTICS_TEXT_EN = (
    "Control deviation, heating time, windows and Zigbee counters as long-term "
    "statistics. Hidden collects without filling lists and dashboards. Off "
    "creates no statistics sensors and removes existing ones."
)
STATISTICS_TEXT_DE = (
    "Regelabweichung, Heizzeit, Fenster und Zigbee-Zähler als Langzeit-Statistik. "
    "Ausgeblendet sammelt, ohne Listen und Dashboards zu füllen. Aus legt keine "
    "Statistik-Sensoren an und entfernt vorhandene."
)


def test_statistics_setting_texts_en() -> None:
    en = load(EN)
    room = en["config_subentries"]["room"]["step"]["settings"]
    section = en["options"]["step"]["init"]["sections"]["statistics"]
    assert room["data"]["statistics"] == "Collect statistics"
    assert section["data"]["statistics"] == "Collect statistics"
    assert section["data_description"]["statistics"] == STATISTICS_TEXT_EN
    # The room form also says what Global is.
    assert room["data_description"]["statistics"] == (
        f"{STATISTICS_TEXT_EN} Global is currently {{global_statistics}}."
    )
    assert section["name"] == "Statistics"
    assert section["description"].strip()
    assert en["selector"]["statistics"]["options"] == {
        "global": "Global",
        "off": "Off",
        "hidden": "Hidden (keeps collecting)",
        "visible": "Visible",
    }


def test_statistics_setting_texts_de() -> None:
    de = load(DE)
    room = de["config_subentries"]["room"]["step"]["settings"]
    section = de["options"]["step"]["init"]["sections"]["statistics"]
    assert room["data"]["statistics"] == "Statistik erfassen"
    assert section["data"]["statistics"] == "Statistik erfassen"
    assert section["data_description"]["statistics"] == STATISTICS_TEXT_DE
    assert room["data_description"]["statistics"] == (
        f"{STATISTICS_TEXT_DE} Global ist derzeit {{global_statistics}}."
    )
    assert section["name"] == "Statistik"
    assert section["description"].strip()
    assert de["selector"]["statistics"]["options"] == {
        "global": "Global",
        "off": "Aus",
        "hidden": "Ausgeblendet (sammelt weiter)",
        "visible": "Sichtbar",
    }


def test_thermostat_section_says_hiding_is_not_written() -> None:
    en, de = load(EN), load(DE)
    sections = "options", "step", "init", "sections", "thermostat"

    def description(data: dict[str, Any]) -> str:
        node: Any = data
        for key in sections:
            node = node[key]
        return str(node["description"])

    assert description(en) == (
        "These settings concern the thermostats. "
        "All of them except hiding are written to the thermostats."
    )
    assert description(de) == (
        "Diese Einstellungen betreffen die Thermostate. "
        "Alle außer dem Ausblenden werden in die Thermostate geschrieben."
    )
