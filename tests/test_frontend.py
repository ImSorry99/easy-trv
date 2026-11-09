import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    dependencies = json.loads(MANIFEST.read_text())["dependencies"]
    assert {"zha", "http", "frontend"} <= set(dependencies)
