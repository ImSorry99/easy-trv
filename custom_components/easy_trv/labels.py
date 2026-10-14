"""Plain-language names: translated option labels and thermostat names."""

from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.translation import async_get_translations

from custom_components.easy_trv.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def async_option_labels(hass: HomeAssistant, key: str) -> dict[str, str]:
    """Return the translated option labels of the selector ``key``, by option value."""
    translations = await async_get_translations(
        hass, hass.config.language, "selector", {DOMAIN}
    )
    prefix = f"component.{DOMAIN}.selector.{key}.options."
    return {
        name.removeprefix(prefix): text
        for name, text in translations.items()
        if name.startswith(prefix)
    }


def localized_number(hass: HomeAssistant, text: str) -> str:
    """Return a number written with ``.`` with a decimal comma in German."""
    return text.replace(".", ",") if hass.config.language.startswith("de") else text


async def async_capability_labels(hass: HomeAssistant) -> dict[str, str]:
    """Return the plain-language setting name of every intent, by intent name.

    The intents are the fixed vocabulary behind all profiles. An intent without
    a translation is simply missing: callers fall back to the capability key.
    """
    return await async_option_labels(hass, "capability")


def thermostat_name(hass: HomeAssistant, entity_id: str) -> str:
    """Return the name of a thermostat's device, or its entity id if it has none."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is not None and entity.device_id is not None:
        device = dr.async_get(hass).async_get(entity.device_id)
        if device is not None and (name := device.name_by_user or device.name):
            return name
    return entity_id
