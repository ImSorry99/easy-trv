"""ZHA adapter: the only module that touches ZHA, zha and zigpy internals.

The rest of the integration talks to :class:`TrvGateway`. The ZHA helpers and
the ``zha``/``zigpy`` libraries behind :class:`ZhaLibraryGateway` are internal
and unstable, so every import and lookup that can break is confined to this
module and surfaces as a :class:`GatewayError` instead of crashing later.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.easy_trv.core.values import parse_zha_sw_version

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from homeassistant.core import HomeAssistant
    from zha.zigbee.device import Device

    from custom_components.easy_trv.core.values import Version

ZHA_DOMAIN = "zha"


class GatewayError(Exception):
    """A gateway operation failed."""


class ZhaUnavailable(GatewayError):  # noqa: N818 - public name fixed by the interface
    """The ZHA gateway cannot be reached (not loaded or incompatible API)."""


class NotZhaEntity(GatewayError):  # noqa: N818 - public name fixed by the interface
    """The entity is not backed by a ZHA device."""


@dataclass(frozen=True)
class DeviceRef:
    """A ZHA-backed TRV as seen from its climate entity."""

    entity_id: str
    device_id: str
    ieee: str
    manufacturer: str
    model: str
    firmware: Version | None


class TrvGateway(Protocol):
    """Everything the integration needs from the Zigbee stack."""

    def resolve(self, climate_entity_id: str) -> DeviceRef:
        """Resolve a climate entity to its ZHA device."""
        ...

    def is_available(self, ieee: str) -> bool:
        """Return whether the device is currently reachable."""
        ...

    async def write(
        self, ieee: str, endpoint: int, cluster: int, attribute: int, value: int
    ) -> None:
        """Write one attribute."""
        ...

    async def read(
        self, ieee: str, endpoint: int, cluster: int, attributes: Sequence[int]
    ) -> Mapping[int, int]:
        """Read attributes from the device, bypassing the attribute cache."""
        ...


def resolve_device(hass: HomeAssistant, climate_entity_id: str) -> DeviceRef:
    """Resolve a ZHA climate entity to a :class:`DeviceRef` via the registries.

    Raises:
        NotZhaEntity: If the entity is unknown, is not provided by ZHA, has no
            device, or its device carries no ZHA identifier.

    """
    entry = er.async_get(hass).async_get(climate_entity_id)
    if entry is None or entry.platform != ZHA_DOMAIN or entry.device_id is None:
        raise NotZhaEntity(climate_entity_id)
    device = dr.async_get(hass).async_get(entry.device_id)
    if not isinstance(device, dr.DeviceEntry):
        raise NotZhaEntity(climate_entity_id)
    ieee = next(
        (value for domain, value in device.identifiers if domain == ZHA_DOMAIN),
        None,
    )
    if ieee is None:
        raise NotZhaEntity(climate_entity_id)
    return DeviceRef(
        entity_id=climate_entity_id,
        device_id=device.id,
        ieee=ieee,
        manufacturer=device.manufacturer or "",
        model=device.model or "",
        firmware=parse_zha_sw_version(device.sw_version),
    )


def _describe(err: Exception) -> str:
    """Return a non-empty message for ``err`` (``TimeoutError()`` has none)."""
    return str(err) or type(err).__name__


class ZhaLibraryGateway:
    """:class:`TrvGateway` backed by the running ZHA gateway object."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Look up the ZHA gateway once.

        Raises:
            ZhaUnavailable: If ZHA's internal API cannot be imported or ZHA is
                not set up.

        """
        self._hass = hass
        try:
            # Imported lazily and via the module attribute: these are internal,
            # unstable APIs and any failure must surface as ZhaUnavailable.
            from homeassistant.components.zha import (  # noqa: PLC0415
                helpers as zha_helpers,
            )
            from zigpy.types import EUI64  # noqa: PLC0415

            self._eui64 = EUI64
            self._gateway = zha_helpers.get_zha_gateway(hass)
        # Internal API: any failure (ImportError, KeyError, ValueError, ...)
        # means ZHA is unusable, so the catch is deliberately broad.
        except Exception as err:
            raise ZhaUnavailable(_describe(err)) from err

    def resolve(self, climate_entity_id: str) -> DeviceRef:
        """Resolve a climate entity to its ZHA device."""
        return resolve_device(self._hass, climate_entity_id)

    def _device(self, ieee: str) -> Device:
        try:
            device = self._gateway.get_device(self._eui64.convert(ieee))
        # Internal API, deliberately broad.
        except Exception as err:
            raise GatewayError(_describe(err)) from err
        if device is None:
            msg = f"device {ieee} is not known to ZHA"
            raise GatewayError(msg)
        return device

    def is_available(self, ieee: str) -> bool:
        """Return whether ZHA currently considers the device reachable."""
        device = self._device(ieee)
        try:
            return bool(device.available)
        # Internal API, deliberately broad.
        except Exception as err:
            raise GatewayError(_describe(err)) from err

    async def write(
        self, ieee: str, endpoint: int, cluster: int, attribute: int, value: int
    ) -> None:
        """Write one attribute (never with a manufacturer code)."""
        device = self._device(ieee)
        try:
            await device.write_zigbee_attribute(endpoint, cluster, attribute, value)
        # Internal API (ZHAException, ValueError, TimeoutError, ...), deliberately
        # broad. The controller applies its own timeout.
        except Exception as err:
            raise GatewayError(_describe(err)) from err

    async def read(
        self, ieee: str, endpoint: int, cluster: int, attributes: Sequence[int]
    ) -> Mapping[int, int]:
        """Read attributes from the device, bypassing ZHA's attribute cache."""
        device = self._device(ieee)
        try:
            cluster_obj = device.async_get_cluster(endpoint, cluster)
            success, failure = await cluster_obj.read_attributes(
                list(attributes), allow_cache=False, only_cache=False
            )
        # Internal API (KeyError for a missing cluster, ZHAException,
        # TimeoutError, ...), deliberately broad.
        except Exception as err:
            raise GatewayError(_describe(err)) from err
        result: dict[int, int] = {}
        for attr in attributes:
            if attr in failure or attr not in success:
                msg = f"read failed for 0x{attr:04X}: {failure.get(attr)}"
                raise GatewayError(msg)
            try:
                result[attr] = int(success[attr])
            except (TypeError, ValueError) as err:
                msg = f"read failed for 0x{attr:04X}: not an integer: {success[attr]!r}"
                raise GatewayError(msg) from err
        return result
