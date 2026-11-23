"""Build an entity profile (Beta) from the entities a person picked in the wizard.

The wizard shows the entities of one thermostat; the person picks which entity
does what. This module turns those picks into the profile's YAML text, with
keys that find the same entities on other thermostats of the model.
"""

from typing import TYPE_CHECKING, Any, Final

import yaml
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

#: Wizard field -> (capability name, intent, entity domains it accepts).
FIELDS: Final = {
    "external_temperature": (
        "external_temperature",
        "set_room_temperature",
        ("number",),
    ),
    "source": ("source_select", "set_source", ("select", "switch")),
    "window_detection": ("window_detection", "set_window_detection", ("switch",)),
    "frost_temperature": ("frost_protection", "set_frost_temperature", ("number",)),
    "hysteresis": ("hysteresis", "set_hysteresis", ("number",)),
    "adaptive_mode": ("adaptive_mode", "set_adaptive_mode", ("switch",)),
}
REQUIRED_FIELD: Final = "external_temperature"


def device_entities(
    hass: HomeAssistant, climate_entity_id: str, domains: tuple[str, ...]
) -> list[str]:
    """Return the enabled entities of the climate entity's device in ``domains``."""
    registry = er.async_get(hass)
    climate = registry.async_get(climate_entity_id)
    if climate is None or climate.device_id is None:
        return []
    return sorted(
        entry.entity_id
        for entry in er.async_entries_for_device(registry, climate.device_id)
        if entry.domain in domains
    )


def entity_key(hass: HomeAssistant, entity_id: str) -> str:
    """Return the key that finds this entity on any device of the same model.

    The translation key if the entity has one; otherwise the object id without
    the device name in front (``acme_open_window`` on device "Acme" ->
    ``open_window``).
    """
    entry = er.async_get(hass).async_get(entity_id)
    object_id = entity_id.split(".", 1)[1]
    if entry is None:
        return object_id
    if entry.translation_key:
        return str(entry.translation_key)
    device = (
        None
        if entry.device_id is None
        else dr.async_get(hass).async_get(entry.device_id)
    )
    if device is not None:
        for name in (device.name_by_user, device.name):
            prefix = f"{slugify(name)}_" if name else ""
            if prefix and object_id.startswith(prefix) and object_id != prefix:
                return object_id[len(prefix) :]
    return object_id


def profile_id(name: str) -> str:
    """Return a profile id made from a name (lowercase, starts with a letter)."""
    if not any(char.isalnum() for char in name):
        return "my_thermostat"
    slug = slugify(name)
    return slug if slug[0].isalpha() else f"profile_{slug}"


def build_profile_yaml(  # noqa: PLR0913 - the parts of one profile
    hass: HomeAssistant,
    name: str,
    climate_entity_id: str,
    picks: Mapping[str, str],
    source_options: Mapping[str, str] | None = None,
    contributor: str | None = None,
) -> str:
    """Return the YAML text of an entity profile for the picked entities.

    ``picks`` maps wizard fields to entity ids; ``source_options`` maps
    ``external``/``internal`` to the options of a select used as the source.
    """
    registry = er.async_get(hass)
    climate = registry.async_get(climate_entity_id)
    device = (
        None
        if climate is None or climate.device_id is None
        else dr.async_get(hass).async_get(climate.device_id)
    )
    capabilities: dict[str, Any] = {}
    for field, (capability, intent, _domains) in FIELDS.items():
        entity_id = picks.get(field)
        if not entity_id:
            continue
        domain = entity_id.split(".", 1)[0]
        spec: dict[str, Any] = {
            "intent": intent,
            "entity": {"domain": domain, "key": entity_key(hass, entity_id)},
        }
        if domain == "select" and source_options:
            spec["options"] = dict(source_options)
        capabilities[capability] = spec
    document: dict[str, Any] = {
        "schema": 1,
        "id": profile_id(name),
        "name": name,
        "backend": "entity",
        "match": {
            "manufacturer": getattr(device, "manufacturer", None) or "unknown",
            "model": getattr(device, "model", None) or "unknown",
        },
    }
    if contributor:
        document["contributor"] = contributor
    document["capabilities"] = capabilities
    text: str = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    return text
