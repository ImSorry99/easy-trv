"""In-memory :class:`TrvGateway` for tests."""

from typing import TYPE_CHECKING

from custom_components.easy_trv.adapters.zha_gateway import (
    DeviceRef,
    GatewayError,
    NotZhaEntity,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from custom_components.easy_trv.core.values import Version


class FakeGateway:
    """A scriptable stand-in for the ZHA gateway.

    ``store`` holds attribute values keyed by ``(ieee, endpoint, cluster,
    attribute)``. ``writes`` and ``reads`` record every call. ``fail_write``
    and ``fail_read`` (keyed by ``(ieee, attribute)``) raise a
    :class:`GatewayError` once and are then cleared; ``read_override`` returns
    the given value instead of the stored one. Like the real adapter, every
    method raises :class:`GatewayError` for an IEEE that no registered device
    owns.
    """

    def __init__(self) -> None:
        self.devices: dict[str, DeviceRef] = {}
        self.availability: dict[str, bool] = {}
        self.store: dict[tuple[str, int, int, int], int] = {}
        self.writes: list[tuple[str, int, int, int, int]] = []
        self.reads: list[tuple[str, int, int, tuple[int, ...]]] = []
        self.fail_write: dict[tuple[str, int], str] = {}
        self.fail_read: dict[tuple[str, int], str] = {}
        self.read_override: dict[tuple[str, int], int] = {}

    def add_device(  # noqa: PLR0913 - keyword-only options mirror DeviceRef
        self,
        entity_id: str,
        ieee: str,
        *,
        manufacturer: str = "SONOFF",
        model: str = "TRVZB",
        firmware: Version | None = (1, 3, 0),
        device_id: str | None = None,
    ) -> DeviceRef:
        """Register a device under its climate entity and return its ref."""
        ref = DeviceRef(
            entity_id=entity_id,
            device_id=device_id if device_id is not None else f"dev-{ieee}",
            ieee=ieee,
            manufacturer=manufacturer,
            model=model,
            firmware=firmware,
        )
        self.devices[entity_id] = ref
        return ref

    def resolve(self, climate_entity_id: str) -> DeviceRef:
        """Return the registered device or raise :class:`NotZhaEntity`."""
        try:
            return self.devices[climate_entity_id]
        except KeyError:
            raise NotZhaEntity(climate_entity_id) from None

    def _require_known(self, ieee: str) -> None:
        """Raise like the real adapter for an IEEE ZHA does not know."""
        if all(ref.ieee != ieee for ref in self.devices.values()):
            msg = f"device {ieee} is not known to ZHA"
            raise GatewayError(msg)

    def is_available(self, ieee: str) -> bool:
        """Return the scripted availability (default: available)."""
        self._require_known(ieee)
        return self.availability.get(ieee, True)

    async def write(
        self, ieee: str, endpoint: int, cluster: int, attribute: int, value: int
    ) -> None:
        """Record and store a write unless a failure is scripted."""
        self._require_known(ieee)
        self.writes.append((ieee, endpoint, cluster, attribute, value))
        message = self.fail_write.pop((ieee, attribute), None)
        if message is not None:
            raise GatewayError(message)
        self.store[(ieee, endpoint, cluster, attribute)] = value

    async def read(
        self, ieee: str, endpoint: int, cluster: int, attributes: Sequence[int]
    ) -> Mapping[int, int]:
        """Return overrides or stored values; unknown attributes fail."""
        self._require_known(ieee)
        self.reads.append((ieee, endpoint, cluster, tuple(attributes)))
        result: dict[int, int] = {}
        for attr in attributes:
            message = self.fail_read.pop((ieee, attr), None)
            if message is not None:
                raise GatewayError(message)
            if (ieee, attr) in self.read_override:
                result[attr] = self.read_override[(ieee, attr)]
            elif (ieee, endpoint, cluster, attr) in self.store:
                result[attr] = self.store[(ieee, endpoint, cluster, attr)]
            else:
                msg = f"read failed for 0x{attr:04X}: UNSUPPORTED_ATTRIBUTE"
                raise GatewayError(msg)
        return result
