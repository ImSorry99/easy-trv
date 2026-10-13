from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueSeverity

from custom_components.easy_trv import issues
from custom_components.easy_trv.const import DOMAIN

IEEE = "00:12:4b:00:aa:bb"


def get(hass, issue_id):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


def test_issue_ids_drop_the_colons_of_the_ieee() -> None:
    assert issues.issue_id_unreachable(IEEE) == "trv_unreachable_00124b00aabb"
    assert issues.issue_id_firmware(IEEE) == "trv_firmware_00124b00aabb"


async def test_unreachable_issue_is_raised_and_cleared(hass) -> None:
    issues.raise_unreachable(hass, IEEE, {"room": "Living", "attempts": 3})
    raised = get(hass, "trv_unreachable_00124b00aabb")
    assert raised is not None
    assert raised.severity == IssueSeverity.ERROR
    assert raised.translation_key == "trv_unreachable"
    assert raised.translation_placeholders == {"room": "Living", "attempts": "3"}
    assert raised.is_fixable is False
    assert raised.is_persistent is False
    issues.clear_unreachable(hass, IEEE)
    assert get(hass, "trv_unreachable_00124b00aabb") is None


async def test_firmware_issue_is_raised_and_cleared(hass) -> None:
    issues.raise_firmware(hass, IEEE, {"trv": "climate.a", "required": "1.3.0"})
    raised = get(hass, "trv_firmware_00124b00aabb")
    assert raised is not None
    assert raised.severity == IssueSeverity.WARNING
    assert raised.translation_key == "trv_firmware"
    assert raised.translation_placeholders == {
        "trv": "climate.a",
        "required": "1.3.0",
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
    issues.raise_unreachable(hass, IEEE, {})
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
