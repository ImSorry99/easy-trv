"""Entity gateway (Beta): drives thermostats through their Home Assistant entities.

Rooms with an entity profile talk to this gateway instead of ZHA. It offers
the same :class:`TrvGateway` interface: an "attribute" is the synthetic number
the entity profile gave a capability, and its raw value is what the profile's
encoder produced (hundredths of a degree, 0/1, or an index into the options).
A :class:`RoutingGateway` sends each device to ZHA or to this gateway.
"""

import logging
import re
from typing import TYPE_CHECKING, Any, Final

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, Event, EventStateChangedData, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from custom_components.easy_trv.adapters.zha_gateway import (
    DeviceRef,
    GatewayError,
    NotZhaEntity,
    TrvGateway,
)
from custom_components.easy_trv.core.link import LinkInfo
from custom_components.easy_trv.profiles.entity_profile import (
    KIND_CLIMATE_MODE,
    KIND_CLIMATE_TEMPERATURE,
    KIND_NUMBER,
    KIND_SELECT,
    KIND_SWITCH,
    EntityBinding,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from homeassistant.core import HomeAssistant

    from custom_components.easy_trv.adapters.zha_gateway import ObservationCallback
    from custom_components.easy_trv.core.values import Version
    from custom_components.easy_trv.profiles.engine import Profile

_LOGGER = logging.getLogger(__name__)

#: Devices driven through entities get this prefix instead of an IEEE address.
ENTITY_PREFIX: Final = "entity:"
_CENTI: Final = 100
_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_SILENT: Final = frozenset({STATE_UNAVAILABLE, STATE_UNKNOWN})


def parse_any_version(text: str | None) -> Version | None:
    """Read ``major.minor[.patch]`` anywhere in a firmware text, else ``None``."""
    if not text or (match := _VERSION.search(text)) is None:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch or 0))


def resolve_entity_device(hass: HomeAssistant, climate_entity_id: str) -> DeviceRef:
    """Resolve any climate entity with a device to a :class:`DeviceRef`.

    Raises:
        NotZhaEntity: If the entity is unknown or has no device.

    """
    entry = er.async_get(hass).async_get(climate_entity_id)
    if entry is None or entry.device_id is None:
        raise NotZhaEntity(climate_entity_id)
    device = dr.async_get(hass).async_get(entry.device_id)
    if not isinstance(device, dr.DeviceEntry):
        raise NotZhaEntity(climate_entity_id)
    return DeviceRef(
        entity_id=climate_entity_id,
        device_id=device.id,
        ieee=f"{ENTITY_PREFIX}{device.id}",
        manufacturer=device.manufacturer or "",
        model=device.model or "",
        firmware=parse_any_version(device.sw_version),
    )


def find_device_entity(
    hass: HomeAssistant, device_id: str, domain: str, key: str
) -> str | None:
    """Return the entity of a device that a profile key names, if there is one.

    The translation key wins, then an entity id or unique id that ends in the
    key (Zigbee2MQTT names entities ``<device>_<key>``).
    """
    entries = [
        entry
        for entry in er.async_entries_for_device(
            er.async_get(hass), device_id, include_disabled_entities=False
        )
        if entry.domain == domain
    ]
    for entry in entries:
        if entry.translation_key == key:
            return entry.entity_id
    for entry in entries:
        object_id = entry.entity_id.split(".", 1)[1]
        if object_id == key or object_id.endswith(f"_{key}"):
            return entry.entity_id
    for entry in entries:
        if str(entry.unique_id).endswith(f"_{key}"):
            return entry.entity_id
    return None


class EntityGateway:
    """:class:`TrvGateway` over the entities of a thermostat's device."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Start without bound devices."""
        self._hass = hass
        # Per device: the climate entity and the bindings of its profile.
        self._climate: dict[str, str] = {}
        self._bindings: dict[str, Mapping[int, EntityBinding]] = {}
        self._entities: dict[tuple[str, int], str] = {}
        # Contexts of our own service calls, and the raw values we wrote last:
        # the state change that brings them back is an echo, not a report.
        # (Zigbee2MQTT reports the new state later, under a context of its own.)
        self._own: set[str] = set()
        self._echo: dict[tuple[str, int], int] = {}

    @property
    def bound(self) -> frozenset[str]:
        """Return the devices this gateway drives."""
        return frozenset(self._bindings)

    def bind(self, ref: DeviceRef, profile: Profile) -> None:
        """Drive the device of ``ref`` through the bindings of ``profile``."""
        self._climate[ref.ieee] = ref.entity_id
        self._bindings[ref.ieee] = dict(profile.bindings)
        for attribute in profile.bindings:
            self._entities.pop((ref.ieee, attribute), None)

    def resolve(self, climate_entity_id: str) -> DeviceRef:
        """Resolve a climate entity to its device."""
        return resolve_entity_device(self._hass, climate_entity_id)

    # --- Lookups ------------------------------------------------------------

    def _binding(self, ieee: str, attribute: int) -> EntityBinding:
        try:
            return self._bindings[ieee][attribute]
        except KeyError as err:
            msg = f"{ieee} has no entity binding for {attribute}"
            raise GatewayError(msg) from err

    def _entity_id(self, ieee: str, binding: EntityBinding, attribute: int) -> str:
        if binding.kind in (KIND_CLIMATE_TEMPERATURE, KIND_CLIMATE_MODE):
            return self._climate[ieee]
        cached = self._entities.get((ieee, attribute))
        if cached is not None:
            return cached
        climate = er.async_get(self._hass).async_get(self._climate[ieee])
        device_id = None if climate is None else climate.device_id
        domain = {KIND_NUMBER: "number", KIND_SWITCH: "switch", KIND_SELECT: "select"}[
            binding.kind
        ]
        found = (
            None
            if device_id is None or binding.key is None
            else find_device_entity(self._hass, device_id, domain, binding.key)
        )
        if found is None:
            msg = f"{self._climate[ieee]}: no {domain} entity for {binding.key!r}"
            raise GatewayError(msg)
        self._entities[(ieee, attribute)] = found
        return found

    def _raw(self, ieee: str, attribute: int) -> int:
        """Return the raw value of an entity state, as the profile encodes it."""
        binding = self._binding(ieee, attribute)
        entity_id = self._entity_id(ieee, binding, attribute)
        state = self._hass.states.get(entity_id)
        if state is None or state.state in _SILENT:
            msg = f"{entity_id} has no usable state"
            raise GatewayError(msg)
        return self._encode_state(binding, state.state, state.attributes)

    @staticmethod
    def _encode_state(
        binding: EntityBinding, value: str, attributes: Mapping[str, Any]
    ) -> int:
        try:
            if binding.kind == KIND_NUMBER:
                return round(float(value) * _CENTI)
            if binding.kind == KIND_SWITCH:
                return int(value == STATE_ON)
            if binding.kind == KIND_CLIMATE_TEMPERATURE:
                return round(float(attributes["temperature"]) * _CENTI)
            return binding.options.index(value)
        except (KeyError, TypeError, ValueError) as err:
            msg = f"{binding.capability}: cannot read {value!r}"
            raise GatewayError(msg) from err

    # --- TrvGateway ---------------------------------------------------------

    def is_available(self, ieee: str) -> bool:
        """Return whether the thermostat's climate entity is available."""
        entity_id = self._climate.get(ieee)
        if entity_id is None:
            msg = f"{ieee} is not bound"
            raise GatewayError(msg)
        state = self._hass.states.get(entity_id)
        return state is not None and state.state != STATE_UNAVAILABLE

    def link(self, ieee: str) -> LinkInfo:  # noqa: ARG002
        """Return no radio values: entities carry no LQI, RSSI or last heard."""
        return LinkInfo()

    async def write(
        self, ieee: str, endpoint: int, cluster: int, attribute: int, value: int
    ) -> None:
        """Carry out one capability through a service call."""
        del endpoint, cluster
        binding = self._binding(ieee, attribute)
        entity_id = self._entity_id(ieee, binding, attribute)
        domain, service, data = self._service(binding, value)
        context = Context()
        self._own.add(context.id)
        self._echo[(ieee, attribute)] = value
        try:
            await self._hass.services.async_call(
                domain,
                service,
                {"entity_id": entity_id, **data},
                blocking=True,
                context=context,
            )
        except (HomeAssistantError, ValueError, KeyError, IndexError) as err:
            msg = f"{entity_id}: {domain}.{service} failed: {err}"
            raise GatewayError(msg) from err
        finally:
            # State changes caused by the call arrive while it runs.
            self._hass.loop.call_soon(self._own.discard, context.id)

    @staticmethod
    def _service(binding: EntityBinding, value: int) -> tuple[str, str, dict[str, Any]]:
        kind = binding.kind
        if kind == KIND_NUMBER:
            return "number", "set_value", {"value": value / _CENTI}
        if kind == KIND_SWITCH:
            return "switch", "turn_on" if value else "turn_off", {}
        if kind == KIND_SELECT:
            return "select", "select_option", {"option": binding.options[value]}
        if kind == KIND_CLIMATE_TEMPERATURE:
            return "climate", "set_temperature", {"temperature": value / _CENTI}
        return "climate", "set_hvac_mode", {"hvac_mode": binding.options[value]}

    async def read(
        self, ieee: str, endpoint: int, cluster: int, attributes: Sequence[int]
    ) -> Mapping[int, int]:
        """Read the current entity states."""
        del endpoint, cluster
        return {attribute: self._raw(ieee, attribute) for attribute in attributes}

    def cached(
        self, ieee: str, endpoint: int, cluster: int, attribute: int
    ) -> int | None:
        """Return the current entity state, or ``None`` if it is not usable."""
        del endpoint, cluster
        try:
            return self._raw(ieee, attribute)
        except GatewayError:
            return None

    def subscribe(
        self,
        ieee: str,
        endpoint: int,
        cluster: int,
        attributes: Sequence[int],
        callback_: ObservationCallback,
    ) -> Callable[[], None]:
        """Report state changes of the entities that Easy TRV did not cause."""
        del endpoint, cluster
        by_entity: dict[str, list[int]] = {}
        for attribute in attributes:
            binding = self._binding(ieee, attribute)
            entity_id = self._entity_id(ieee, binding, attribute)
            by_entity.setdefault(entity_id, []).append(attribute)

        @callback
        def on_change(event: Event[EventStateChangedData]) -> None:
            new = event.data["new_state"]
            if new is None or new.state in _SILENT or event.context.id in self._own:
                return
            for attribute in by_entity.get(event.data["entity_id"], ()):
                binding = self._bindings[ieee][attribute]
                try:
                    raw = self._encode_state(binding, new.state, new.attributes)
                except GatewayError:
                    continue
                if self._echo.get((ieee, attribute)) == raw:
                    del self._echo[(ieee, attribute)]
                    continue
                callback_(ieee, attribute, "report", raw)

        unsubscribe = async_track_state_change_event(
            self._hass, list(by_entity), on_change
        )
        done = False

        def release() -> None:
            nonlocal done
            if not done:
                done = True
                unsubscribe()

        return release


class RoutingGateway:
    """Send each device to ZHA or to the entity gateway.

    A device is driven through entities when its room's profile is an entity
    profile (it was bound) or when it is not a ZHA device at all. ``zha`` is
    ``None`` when ZHA is not set up; only entity rooms work then.
    """

    def __init__(self, hass: HomeAssistant, zha: TrvGateway | None) -> None:
        """Wrap the ZHA gateway (if any) and create the entity gateway."""
        self.zha = zha
        self.entity = EntityGateway(hass)

    def _for(self, ieee: str) -> Any:
        if ieee in self.entity.bound or ieee.startswith(ENTITY_PREFIX):
            return self.entity
        if self.zha is None:
            msg = f"ZHA is not set up, {ieee} cannot be reached"
            raise GatewayError(msg)
        return self.zha

    def bind(self, ref: DeviceRef, profile: Profile) -> None:
        """Drive a device through the entities of an entity profile."""
        self.entity.bind(ref, profile)

    def resolve(self, climate_entity_id: str) -> DeviceRef:
        """Resolve through ZHA first, then as any device with entities."""
        if self.zha is not None:
            try:
                return self.zha.resolve(climate_entity_id)
            except NotZhaEntity:
                pass
        return self.entity.resolve(climate_entity_id)

    def is_available(self, ieee: str) -> bool:
        """Return whether the device is reachable."""
        return bool(self._for(ieee).is_available(ieee))

    async def write(
        self, ieee: str, endpoint: int, cluster: int, attribute: int, value: int
    ) -> None:
        """Write through the gateway of the device."""
        await self._for(ieee).write(ieee, endpoint, cluster, attribute, value)

    async def read(
        self, ieee: str, endpoint: int, cluster: int, attributes: Sequence[int]
    ) -> Mapping[int, int]:
        """Read through the gateway of the device."""
        result: Mapping[int, int] = await self._for(ieee).read(
            ieee, endpoint, cluster, attributes
        )
        return result

    def cached(
        self, ieee: str, endpoint: int, cluster: int, attribute: int
    ) -> int | None:
        """Return the cached value through the gateway of the device."""
        value: int | None = self._for(ieee).cached(ieee, endpoint, cluster, attribute)
        return value

    def link(self, ieee: str) -> LinkInfo:
        """Return the radio values through the gateway of the device; never raises."""
        try:
            info: LinkInfo = self._for(ieee).link(ieee)
        except GatewayError:
            return LinkInfo()
        return info

    def subscribe(
        self,
        ieee: str,
        endpoint: int,
        cluster: int,
        attributes: Sequence[int],
        callback_: ObservationCallback,
    ) -> Callable[[], None]:
        """Subscribe through the gateway of the device."""
        release: Callable[[], None] = self._for(ieee).subscribe(
            ieee, endpoint, cluster, attributes, callback_
        )
        return release
