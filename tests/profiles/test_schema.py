from pathlib import Path
from typing import Any

import pytest
import voluptuous as vol
import yaml

from custom_components.easy_trv.profiles import schema

BUILTIN = Path("custom_components/easy_trv/profiles/builtin/sonoff_trvzb_native.yaml")


def _load_builtin() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(BUILTIN.read_text())
    return data


def test_builtin_profile_validates() -> None:
    data = schema.validate_profile_dict(_load_builtin())
    assert data["id"] == "sonoff_trvzb_native"
    assert data["capabilities"]["external_temperature"]["write"]["cluster"] == 0xFC11
    assert data["capabilities"]["source_select"]["signals"] == {2: "failsafe"}
    assert data["capabilities"]["hysteresis"]["min_firmware"] == "1.3.0"


def test_unknown_encoder_rejected() -> None:
    data = _load_builtin()
    data["capabilities"]["external_temperature"]["write"]["encode"] = "float"
    with pytest.raises(vol.Invalid):
        schema.validate_profile_dict(data)


def test_duplicate_intent_rejected() -> None:
    data = _load_builtin()
    data["capabilities"]["copy"] = dict(data["capabilities"]["external_temperature"])
    with pytest.raises(vol.Invalid, match="duplicate intent"):
        schema.validate_profile_dict(data)
