"""Serve the Easy TRV card and load it in the dashboards."""

from pathlib import Path
from typing import TYPE_CHECKING, Final

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http.server import StaticPathConfig

from custom_components.easy_trv.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

CARD_URL: Final = "/easy_trv/easy-trv-card.js"
CARD_FILE: Final = Path(__file__).parent / "frontend" / "easy-trv-card.js"
_REGISTERED: Final = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant, version: str) -> None:
    """Serve the card file and load it as a module; once per Home Assistant run.

    ``version`` goes into the URL so a new release is not served from a cache.
    """
    if hass.data.get(_REGISTERED):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_FILE), cache_headers=False)]
    )
    add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    hass.data[_REGISTERED] = True
