import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.setup import async_setup_component

from custom_components.easy_trv import frontend

CARD = Path(frontend.__file__).parent / "www" / "easy-trv-card.js"
MANIFEST = Path(frontend.__file__).parent / "manifest.json"


def _http(hass) -> AsyncMock:
    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    return hass.http.async_register_static_paths


async def test_card_static_path_and_module_url(hass) -> None:
    register = _http(hass)
    with patch.object(frontend, "add_extra_js_url") as add:
        await frontend.async_register_card(hass, "0.9.0")

    assert frontend.CARD_URL == "/easy_trv/easy-trv-card.js"
    (configs,) = register.call_args.args
    assert [(c.url_path, c.path) for c in configs] == [(frontend.CARD_URL, str(CARD))]
    add.assert_called_once_with(hass, f"{frontend.CARD_URL}?v=0.9.0")
    assert CARD.is_file()


async def test_card_registered_once_across_reloads(hass) -> None:
    register = _http(hass)
    with patch.object(frontend, "add_extra_js_url") as add:
        await frontend.async_register_card(hass, "0.9.0")
        await frontend.async_register_card(hass, "0.9.0")

    register.assert_awaited_once()
    add.assert_called_once()


def test_manifest_depends_on_http_and_frontend() -> None:
    manifest = json.loads(MANIFEST.read_text())
    assert {"http", "frontend"} <= set(manifest["dependencies"])
    # ZHA is optional since entity profiles; it is set up first when present.
    assert manifest["after_dependencies"] == ["zha"]


async def test_card_is_loaded_at_start_without_any_entry(hass) -> None:
    """The card is registered by the component itself, not by the entry."""
    hass.config.components.add("frontend")
    with patch(
        "custom_components.easy_trv.async_register_card", AsyncMock()
    ) as register:
        assert await async_setup_component(hass, "easy_trv", {})
        await hass.async_block_till_done()
    register.assert_awaited_once()
    assert register.await_args.args[1] == json.loads(MANIFEST.read_text())["version"]
