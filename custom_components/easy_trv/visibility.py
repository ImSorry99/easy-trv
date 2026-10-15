"""Hide the thermostat entities Easy TRV controls, shown again when it lets go.

Home Assistant's own helpers do the same for what they take over (the group
helper's "hide members", ``switch_as_x``): the entity gets
``hidden_by = integration``. Easy TRV also puts a marker into the options of
the entity registry entry, so it later knows which entities it hid itself and
never touches one that someone else hid.
"""

from typing import TYPE_CHECKING, Final

from homeassistant.helpers import entity_registry as er

from custom_components.easy_trv.const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Iterable

    from homeassistant.core import HomeAssistant

    from custom_components.easy_trv.profiles.engine import Profile

# The options (under the domain of Easy TRV) of an entity Easy TRV has hidden.
MARKER: Final = {"hidden": True}

# The platform of the thermostat entities that a profile names.
_THERMOSTAT_PLATFORM: Final = "zha"


def _is_marked(entry: er.RegistryEntry) -> bool:
    """Return whether Easy TRV has hidden this entity."""
    options = entry.options.get(DOMAIN)
    return options is not None and options.get("hidden") is True


def thermostat_devices(hass: HomeAssistant, climates: Iterable[str]) -> set[str]:
    """Return the ids of the devices of the given climate entities.

    A climate entity that is not registered or has no device adds nothing.
    """
    registry = er.async_get(hass)
    devices: set[str] = set()
    for climate in climates:
        entry = registry.async_get(climate)
        if entry is not None and entry.device_id is not None:
            devices.add(entry.device_id)
    return devices


def controlled_entities(
    hass: HomeAssistant, climates: Iterable[str], profile: Profile
) -> set[str]:
    """Return the entities Easy TRV controls on the given thermostats.

    Per climate entity that is its own id and, on the same device, every ZHA
    entity whose domain and translation key a capability of ``profile`` names.
    An entity without a translation key never matches. A climate entity that is
    not registered adds nothing; one without a device adds only itself.
    """
    registry = er.async_get(hass)
    named = {
        (spec.entity.domain, spec.entity.translation_key)
        for spec in profile.capabilities.values()
        if spec.entity is not None
    }
    controlled: set[str] = set()
    for climate in climates:
        entry = registry.async_get(climate)
        if entry is None:
            continue
        controlled.add(climate)
        if entry.device_id is None:
            continue
        for sibling in er.async_entries_for_device(
            registry, entry.device_id, include_disabled_entities=True
        ):
            if (
                sibling.platform == _THERMOSTAT_PLATFORM
                and sibling.translation_key is not None
                and (sibling.domain, sibling.translation_key) in named
            ):
                controlled.add(sibling.entity_id)
    return controlled


def reconcile_hidden(
    hass: HomeAssistant, wanted: set[str], keep_devices: set[str]
) -> None:
    """Hide what ``wanted`` names and show again what Easy TRV hid but no longer wants.

    - A wanted entity that is shown and not marked is hidden (``integration``)
      and marked. One that is marked stays as it is, also when the person
      showed it again: that choice stays. One that is hidden stays, and gets no
      marker: it is not Easy TRV's to show later.
    - A marked entity that is not wanted any more is shown again if
      ``hidden_by`` is still ``integration``, and loses its marker. One the
      person has hidden by hand since stays hidden.
    - Entities on a device of ``keep_devices`` (the thermostats of rooms that
      could not be set up) are left as they are.
    """
    registry = er.async_get(hass)
    for entity_id in wanted:
        entry = registry.async_get(entity_id)
        if entry is None or entry.hidden_by is not None or _is_marked(entry):
            continue
        registry.async_update_entity(
            entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
        )
        registry.async_update_entity_options(entity_id, DOMAIN, dict(MARKER))
    # A copy: showing an entity replaces its entry in the registry.
    for entry in list(registry.entities.values()):
        if (
            entry.entity_id not in wanted
            and entry.device_id not in keep_devices
            and _is_marked(entry)
        ):
            _let_go(registry, entry)


def show_all(hass: HomeAssistant) -> None:
    """Show every entity Easy TRV hid, as if no thermostat were controlled."""
    registry = er.async_get(hass)
    for entry in list(registry.entities.values()):
        if _is_marked(entry):
            _let_go(registry, entry)


def _let_go(registry: er.EntityRegistry, entry: er.RegistryEntry) -> None:
    """Show an entity again if Easy TRV hid it, and remove the marker."""
    if entry.hidden_by is er.RegistryEntryHider.INTEGRATION:
        registry.async_update_entity(entry.entity_id, hidden_by=None)
    registry.async_update_entity_options(entry.entity_id, DOMAIN, None)
