from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueSeverity

from custom_components.easy_trv import issues
from custom_components.easy_trv.const import DOMAIN

IEEE = "00:12:4b:00:aa:bb"


def get(hass, issue_id):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


def raise_unreachable(hass, ieee) -> None:
    issues.raise_unreachable(
        hass, ieee, room="Old", setting="x", thermostat="y", attempts=3, error="z"
    )


def raise_firmware(hass, ieee) -> None:
    issues.raise_firmware(
        hass, ieee, room="Old", setting="x", thermostat="y", required="1", current="0"
    )


def test_issue_ids_drop_the_colons_of_the_ieee() -> None:
    assert issues.issue_id_unreachable(IEEE) == "trv_unreachable_00124b00aabb"
    assert issues.issue_id_firmware(IEEE) == "trv_firmware_00124b00aabb"
    assert issues.issue_id_room_invalid("01ABC") == "room_invalid_01ABC"


async def test_unreachable_issue_is_raised_and_cleared(hass) -> None:
    issues.raise_unreachable(
        hass,
        IEEE,
        room="Living",
        setting="Hysteresis",
        thermostat="Living TRV",
        attempts=3,
        error="radio busy",
    )
    raised = get(hass, "trv_unreachable_00124b00aabb")
    assert raised is not None
    assert raised.severity == IssueSeverity.ERROR
    assert raised.translation_key == "trv_unreachable"
    assert raised.translation_placeholders == {
        "room": "Living",
        "setting": "Hysteresis",
        "thermostat": "Living TRV",
        "attempts": "3",
        "error": "radio busy",
    }
    assert raised.is_fixable is False
    assert raised.is_persistent is False
    issues.clear_unreachable(hass, IEEE)
    assert get(hass, "trv_unreachable_00124b00aabb") is None


async def test_firmware_issue_is_raised_and_cleared(hass) -> None:
    issues.raise_firmware(
        hass,
        IEEE,
        room="Living",
        setting="Hysteresis",
        thermostat="Living TRV",
        required="1.3.0",
        current="1.2.1",
    )
    raised = get(hass, "trv_firmware_00124b00aabb")
    assert raised is not None
    assert raised.severity == IssueSeverity.WARNING
    assert raised.translation_key == "trv_firmware"
    assert raised.translation_placeholders == {
        "room": "Living",
        "setting": "Hysteresis",
        "thermostat": "Living TRV",
        "required": "1.3.0",
        "current": "1.2.1",
    }
    assert raised.is_fixable is False
    assert raised.is_persistent is False
    issues.clear_firmware(hass, IEEE)
    assert get(hass, "trv_firmware_00124b00aabb") is None


async def test_zha_api_issue_is_raised_and_cleared(hass) -> None:
    issues.raise_zha_api(hass, "no get_zha_gateway")
    raised = get(hass, "zha_api_incompatible")
    assert raised is not None
    assert raised.severity == IssueSeverity.ERROR
    assert raised.translation_key == "zha_api_incompatible"
    assert raised.translation_placeholders == {"error": "no get_zha_gateway"}
    assert raised.is_fixable is False
    assert raised.is_persistent is False
    issues.clear_zha_api(hass)
    assert get(hass, "zha_api_incompatible") is None


async def test_profile_invalid_issue_is_named_after_the_file_stem(hass) -> None:
    issues.raise_profile_invalid(hass, "my_trv.yaml", "bad endpoint")
    raised = get(hass, "profile_invalid_my_trv")
    assert raised is not None
    assert raised.severity == IssueSeverity.WARNING
    assert raised.translation_key == "profile_invalid"
    assert raised.translation_placeholders == {
        "file": "my_trv.yaml",
        "error": "bad endpoint",
    }
    assert raised.is_fixable is False
    assert raised.is_persistent is False


async def test_clear_profile_issues_keeps_only_the_named_stems(hass) -> None:
    issues.raise_profile_invalid(hass, "keep.yaml", "still broken")
    issues.raise_profile_invalid(hass, "fixed.yaml", "was broken")
    raise_unreachable(hass, IEEE)
    ir.async_create_issue(
        hass,
        "other_domain",
        "profile_invalid_fixed",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key="x",
    )
    issues.clear_profile_issues(hass, {"keep"})
    assert get(hass, "profile_invalid_keep") is not None
    assert get(hass, "profile_invalid_fixed") is None
    # Neither other issues of the domain nor issues of other domains are touched.
    assert get(hass, "trv_unreachable_00124b00aabb") is not None
    assert ir.async_get(hass).async_get_issue("other_domain", "profile_invalid_fixed")
    issues.clear_profile_issues(hass, set())
    assert get(hass, "profile_invalid_keep") is None


async def test_clear_thermostat_issues_except_keeps_only_the_named_thermostats(
    hass,
) -> None:
    other = "00:12:4b:00:cc:dd"
    for ieee in (IEEE, other):
        raise_unreachable(hass, ieee)
        raise_firmware(hass, ieee)
    issues.raise_profile_invalid(hass, "my_trv.yaml", "bad")
    issues.raise_zha_api(hass, "broken")
    ir.async_create_issue(
        hass,
        "other_domain",
        "trv_unreachable_00124b00ccdd",
        is_fixable=False,
        severity=IssueSeverity.ERROR,
        translation_key="x",
    )

    issues.clear_thermostat_issues_except(hass, {IEEE})

    assert get(hass, "trv_unreachable_00124b00aabb") is not None
    assert get(hass, "trv_firmware_00124b00aabb") is not None
    assert get(hass, "trv_unreachable_00124b00ccdd") is None
    assert get(hass, "trv_firmware_00124b00ccdd") is None
    # Other issues of the domain and issues of other domains are not touched.
    assert get(hass, "profile_invalid_my_trv") is not None
    assert get(hass, "zha_api_incompatible") is not None
    assert ir.async_get(hass).async_get_issue(
        "other_domain", "trv_unreachable_00124b00ccdd"
    )
    issues.clear_thermostat_issues_except(hass, set())
    assert get(hass, "trv_unreachable_00124b00aabb") is None
    assert get(hass, "trv_firmware_00124b00aabb") is None


async def test_room_invalid_issue_names_the_room_and_the_problem(hass) -> None:
    issues.raise_room_invalid(hass, "sub1", "Bedroom", "climate.x is not a ZHA one")
    raised = get(hass, "room_invalid_sub1")
    assert raised is not None
    assert raised.severity == IssueSeverity.WARNING
    assert raised.translation_key == "room_invalid"
    assert raised.translation_placeholders == {
        "room": "Bedroom",
        "error": "climate.x is not a ZHA one",
    }
    assert raised.is_fixable is False
    assert raised.is_persistent is False


async def test_clear_room_issues_except_keeps_only_the_named_rooms(hass) -> None:
    issues.raise_room_invalid(hass, "keep", "Still broken", "x")
    issues.raise_room_invalid(hass, "fixed", "Was broken", "y")
    raise_unreachable(hass, IEEE)
    ir.async_create_issue(
        hass,
        "other_domain",
        "room_invalid_fixed",
        is_fixable=False,
        severity=IssueSeverity.WARNING,
        translation_key="x",
    )

    issues.clear_room_issues_except(hass, {"keep"})

    assert get(hass, "room_invalid_keep") is not None
    assert get(hass, "room_invalid_fixed") is None
    # Other issues of the domain and issues of other domains are not touched.
    assert get(hass, "trv_unreachable_00124b00aabb") is not None
    assert ir.async_get(hass).async_get_issue("other_domain", "room_invalid_fixed")
    issues.clear_room_issues_except(hass, set())
    assert get(hass, "room_invalid_keep") is None


async def test_settings_migrated_issue_is_a_dismissible_warning(hass) -> None:
    issues.raise_settings_migrated(hass, ["Living", "Bed"], "0.4 K")
    raised = get(hass, "settings_migrated")
    assert raised is not None
    assert raised.severity == IssueSeverity.WARNING
    assert raised.translation_key == "settings_migrated"
    assert raised.translation_placeholders == {
        "rooms": "Living, Bed",
        "hysteresis": "0.4 K",
    }
    assert raised.is_fixable is False
    assert raised.is_persistent is True
