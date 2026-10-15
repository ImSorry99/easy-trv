"""ZHA adapter: the only module that touches ZHA, zha and zigpy internals.

The rest of the integration talks to :class:`TrvGateway`. The ZHA helpers and
the ``zha``/``zigpy`` libraries behind :class:`ZhaLibraryGateway` are internal
and unstable, so every import and lookup that can break is confined to this
module and surfaces as a :class:`GatewayError` instead of crashing later.
"""

import logging
from collections import Counter
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.easy_trv.core.values import parse_zha_sw_version

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from homeassistant.core import HomeAssistant
    from zha.zigbee.device import Device

    from custom_components.easy_trv.core.values import Version

_LOGGER = logging.getLogger(__name__)

ZHA_DOMAIN = "zha"

#: ``"report"``: the device reported (or a quirk re-published) the attribute.
#: ``"written"``: someone other than this gateway wrote it successfully.
type ObservationKind = Literal["report", "written"]
#: ``(ieee, attribute, kind, raw)``, called on the event loop thread.
type ObservationCallback = Callable[[str, int, ObservationKind, int], None]


class GatewayError(Exception):
    """A gateway operation failed."""


class ZhaUnavailable(GatewayError):  # noqa: N818 - public name fixed by the interface
    """The ZHA gateway cannot be reached (not loaded or incompatible API)."""


class NotZhaEntity(GatewayError):  # noqa: N818 - public name fixed by the interface
    """The entity is not backed by a ZHA device."""


class UnsupportedAttribute(GatewayError):  # noqa: N818 - public name fixed by the interface
    """The device does not implement the attribute (a firmware or profile problem).

    Unlike any other :class:`GatewayError` it will not go away by trying again.
    """


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

    def subscribe(
        self,
        ieee: str,
        endpoint: int,
        cluster: int,
        attributes: Sequence[int],
        callback: ObservationCallback,
    ) -> Callable[[], None]:
        """Observe reports of ``attributes`` that this gateway did not cause.

        Reads and writes issued through this gateway are never delivered. The
        returned callable unsubscribes; it never raises and is idempotent.
        """
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
        # (ieee, cluster, attribute) of our own writes and reads in progress;
        # zigpy events for them are echoes of ourselves, not observations.
        self._in_flight: Counter[tuple[str, int, int]] = Counter()
        try:
            # Imported lazily and via the module attribute: these are internal,
            # unstable APIs and any failure must surface as ZhaUnavailable.
            from homeassistant.components.zha import (  # noqa: PLC0415
                helpers as zha_helpers,
            )
            from zigpy import zcl  # noqa: PLC0415
            from zigpy.types import EUI64  # noqa: PLC0415
            from zigpy.zcl import foundation  # noqa: PLC0415

            self._zha_helpers = zha_helpers
            self._eui64 = EUI64
            self._foundation = foundation
            self._report_events = (
                zcl.AttributeReportedEvent.event_type,
                zcl.AttributeUpdatedEvent.event_type,
            )
            self._written_event = zcl.AttributeWrittenEvent.event_type
            # Probe once so an unusable ZHA fails early. The gateway object is
            # looked up again on every call (see ``_device``).
            zha_helpers.get_zha_gateway(hass)
        # Internal API: any failure (ImportError, KeyError, ValueError, ...)
        # means ZHA is unusable, so the catch is deliberately broad.
        except Exception as err:
            raise ZhaUnavailable(_describe(err)) from err

    def resolve(self, climate_entity_id: str) -> DeviceRef:
        """Resolve a climate entity to its ZHA device."""
        return resolve_device(self._hass, climate_entity_id)

    def _device(self, ieee: str) -> Device:
        try:
            # Looked up per call: a ZHA config-entry reload replaces the Gateway
            # object and shuts the old one down, so a cached one would fail
            # every call until Easy TRV is reloaded.
            gateway = self._zha_helpers.get_zha_gateway(self._hass)
            device = gateway.get_device(self._eui64.convert(ieee))
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

    @contextmanager
    def _in_flight_scope(
        self, ieee: str, cluster: int, attributes: Sequence[int]
    ) -> Iterator[None]:
        """Mark attributes as being written or read by us for the duration."""
        keys = [(ieee.lower(), cluster, attribute) for attribute in attributes]
        self._in_flight.update(keys)
        try:
            yield
        finally:
            self._in_flight.subtract(keys)
            self._in_flight += Counter()  # drop zero and negative counts

    def _first_rejection(self, response: object) -> object | None:
        """Return the first non-SUCCESS status in a write response, if any.

        Understands the shapes zigpy and quirks produce: a (nested) list of
        ``WriteAttributesStatusRecord`` and bare ``Status`` values. ``None``, an
        empty response and a lone SUCCESS record (``attrid=None`` means every
        attribute succeeded) all count as success.
        """
        if isinstance(response, list | tuple):
            for item in response:
                if (rejected := self._first_rejection(item)) is not None:
                    return rejected
            return None
        if isinstance(response, self._foundation.WriteAttributesStatusRecord):
            response = response.status
        if (
            isinstance(response, self._foundation.Status)
            and response != self._foundation.Status.SUCCESS
        ):
            return response
        return None

    async def write(
        self, ieee: str, endpoint: int, cluster: int, attribute: int, value: int
    ) -> None:
        """Write one attribute (never with a manufacturer code).

        Raises:
            UnsupportedAttribute: If the device answers ``UNSUPPORTED_ATTRIBUTE``.
            GatewayError: If ZHA fails to send the write or the device answers
                with another status than SUCCESS.

        """
        device = self._device(ieee)
        try:
            with self._in_flight_scope(ieee, cluster, [attribute]):
                response = await device.write_zigbee_attribute(
                    endpoint, cluster, attribute, value
                )
        # Internal API (ZHAException, ValueError, TimeoutError, ...), deliberately
        # broad. The controller applies its own timeout.
        except Exception as err:
            raise GatewayError(_describe(err)) from err
        # zigpy reports a device-side rejection (READ_ONLY, INVALID_VALUE, ...)
        # as status records in the response instead of raising.
        rejected = self._first_rejection(response)
        if rejected is not None:
            msg = f"write rejected for 0x{attribute:04X}: {rejected}"
            if rejected == self._foundation.Status.UNSUPPORTED_ATTRIBUTE:
                raise UnsupportedAttribute(msg)
            raise GatewayError(msg)

    async def read(
        self, ieee: str, endpoint: int, cluster: int, attributes: Sequence[int]
    ) -> Mapping[int, int]:
        """Read attributes from the device, bypassing ZHA's attribute cache.

        Raises:
            UnsupportedAttribute: If the device reports the first requested
                attribute (the one an operation verifies) as unsupported.
            GatewayError: If any attribute cannot be read.

        """
        device = self._device(ieee)
        try:
            cluster_obj = device.async_get_cluster(endpoint, cluster)
            with self._in_flight_scope(ieee, cluster, attributes):
                success, failure = await cluster_obj.read_attributes(
                    list(attributes), allow_cache=False, only_cache=False
                )
        # Internal API (KeyError for a missing cluster, ZHAException,
        # TimeoutError, ...), deliberately broad.
        except Exception as err:
            raise GatewayError(_describe(err)) from err
        result: dict[int, int] = {}
        for index, attr in enumerate(attributes):
            if attr in failure or attr not in success:
                status = failure.get(attr)
                msg = f"read failed for 0x{attr:04X}: {status}"
                if (
                    index == 0
                    and status == self._foundation.Status.UNSUPPORTED_ATTRIBUTE
                ):
                    raise UnsupportedAttribute(msg)
                raise GatewayError(msg)
            try:
                result[attr] = int(success[attr])
            except (TypeError, ValueError) as err:
                msg = f"read failed for 0x{attr:04X}: not an integer: {success[attr]!r}"
                raise GatewayError(msg) from err
        return result

    def subscribe(
        self,
        ieee: str,
        endpoint: int,
        cluster: int,
        attributes: Sequence[int],
        callback: ObservationCallback,
    ) -> Callable[[], None]:
        """Listen on the zigpy cluster for reports of ``attributes``.

        Uses the typed zigpy cluster events, as ZHA's own climate entity does:
        ``attribute_report`` and ``attribute_updated`` (device reports, also
        after a quirk transformed the value) are delivered as ``"report"``,
        ``attribute_written`` with status SUCCESS as ``"written"``. Plain
        ``attribute_read`` events are not observations. zigpy emits these
        synchronously from its receive path on the event loop, so ``callback``
        runs on the loop thread; its exceptions are logged, never propagated
        into zigpy. The listeners live on the current ZHA device object, so a
        ZHA reload drops them: the caller must subscribe again.

        Raises:
            ZhaUnavailable: If the device is not known to ZHA (right now).
            GatewayError: If the cluster is missing or listening fails.

        """
        try:
            device = self._device(ieee)
        except GatewayError as err:
            raise ZhaUnavailable(str(err)) from err
        wanted = frozenset(attributes)
        key_ieee = ieee.lower()

        def deliver(kind: ObservationKind, event: Any) -> None:
            attribute = event.attribute_id
            if (
                attribute not in wanted
                or (key_ieee, cluster, attribute) in self._in_flight
            ):
                return
            try:
                raw = int(event.value)
            except (TypeError, ValueError):
                _LOGGER.debug("Ignoring non-integer 0x%04X from %s", attribute, ieee)
                return
            try:
                callback(ieee, attribute, kind, raw)
            # A listener bug must not break zigpy's receive path.
            except Exception:
                _LOGGER.exception("Observation callback failed for %s", ieee)

        def on_report(event: Any) -> None:
            deliver("report", event)

        def on_written(event: Any) -> None:
            if event.status == self._foundation.Status.SUCCESS:
                deliver("written", event)

        unsubscribers: list[Callable[[], None]] = []
        try:
            cluster_obj = device.async_get_cluster(endpoint, cluster)
            unsubscribers.extend(
                cluster_obj.on_event(event_type, on_report)
                for event_type in self._report_events
            )
            unsubscribers.append(cluster_obj.on_event(self._written_event, on_written))
        # Internal API, deliberately broad.
        except Exception as err:
            self._release(unsubscribers)
            raise GatewayError(_describe(err)) from err

        def unsubscribe() -> None:
            self._release(unsubscribers)

        return unsubscribe

    @staticmethod
    def _release(unsubscribers: list[Callable[[], None]]) -> None:
        """Run and forget all ``unsubscribers``; never raises."""
        pending = list(unsubscribers)
        unsubscribers.clear()
        for unsubscribe in pending:
            # Internal API on a possibly dead device object, deliberately broad.
            with suppress(Exception):
                unsubscribe()
