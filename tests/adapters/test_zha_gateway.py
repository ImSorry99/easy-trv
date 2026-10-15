import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from zha.application.gateway import Gateway
from zha.zigbee.device import Device
from zigpy.event import EventBase
from zigpy.types import EUI64
from zigpy.zcl import (
    AttributeReadEvent,
    AttributeReportedEvent,
    AttributeUpdatedEvent,
    AttributeWrittenEvent,
    ClusterType,
)
from zigpy.zcl.foundation import Status, WriteAttributesStatusRecord

from custom_components.easy_trv.adapters import zha_gateway
from custom_components.easy_trv.adapters.zha_gateway import DeviceRef, GatewayError
from tests.fake_gateway import FakeGateway

IEEE = "00:12:4b:00:aa:bb:cc:dd"
PATCH_TARGET = "homeassistant.components.zha.helpers.get_zha_gateway"


def _gateway_with(device: MagicMock | None) -> MagicMock:
    gw_obj = MagicMock()
    gw_obj.get_device.return_value = device
    return gw_obj


def _device_with_cluster(read_result: object) -> tuple[MagicMock, MagicMock]:
    device = MagicMock()
    device.write_zigbee_attribute = AsyncMock()
    cluster = MagicMock()
    cluster.read_attributes = AsyncMock(return_value=read_result)
    device.async_get_cluster.return_value = cluster
    return device, cluster


# --- resolve_device -------------------------------------------------------


async def test_resolve_device_from_registries(
    hass, device_registry, entity_registry
) -> None:
    entry = MockConfigEntry(domain="zha")
    entry.add_to_hass(hass)
    dev = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("zha", "00:12:4b:00:aa:bb:cc:dd")},
        manufacturer="SONOFF",
        model="TRVZB",
        sw_version="0x00001300",
    )
    entity_registry.async_get_or_create(
        "climate", "zha", "uid1", suggested_object_id="living_trv", device_id=dev.id
    )
    ref = zha_gateway.resolve_device(hass, "climate.living_trv")
    assert ref == DeviceRef(
        "climate.living_trv",
        dev.id,
        "00:12:4b:00:aa:bb:cc:dd",
        "SONOFF",
        "TRVZB",
        (1, 3, 0),
    )


async def test_resolve_defaults_missing_metadata(
    hass, device_registry, entity_registry
) -> None:
    entry = MockConfigEntry(domain="zha")
    entry.add_to_hass(hass)
    dev = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("zha", IEEE)}
    )
    entity_registry.async_get_or_create(
        "climate", "zha", "uid3", suggested_object_id="bare_trv", device_id=dev.id
    )
    ref = zha_gateway.resolve_device(hass, "climate.bare_trv")
    assert ref == DeviceRef("climate.bare_trv", dev.id, IEEE, "", "", None)


async def test_resolve_rejects_non_zha(hass, entity_registry) -> None:
    entity_registry.async_get_or_create(
        "climate", "mqtt", "uid2", suggested_object_id="other"
    )
    with pytest.raises(zha_gateway.NotZhaEntity):
        zha_gateway.resolve_device(hass, "climate.other")


async def test_resolve_rejects_unknown_entity(hass) -> None:
    with pytest.raises(zha_gateway.NotZhaEntity):
        zha_gateway.resolve_device(hass, "climate.does_not_exist")


async def test_resolve_rejects_zha_entity_without_device(hass, entity_registry) -> None:
    entity_registry.async_get_or_create(
        "climate", "zha", "uid4", suggested_object_id="orphan"
    )
    with pytest.raises(zha_gateway.NotZhaEntity):
        zha_gateway.resolve_device(hass, "climate.orphan")


async def test_resolve_rejects_device_without_zha_identifier(
    hass, device_registry, entity_registry
) -> None:
    entry = MockConfigEntry(domain="zha")
    entry.add_to_hass(hass)
    dev = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("other", "x")}
    )
    entity_registry.async_get_or_create(
        "climate", "zha", "uid5", suggested_object_id="foreign", device_id=dev.id
    )
    with pytest.raises(zha_gateway.NotZhaEntity):
        zha_gateway.resolve_device(hass, "climate.foreign")


async def test_resolve_rejects_entity_whose_device_vanished(
    hass, device_registry, entity_registry
) -> None:
    entry = MockConfigEntry(domain="zha")
    entry.add_to_hass(hass)
    dev = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("zha", IEEE)}
    )
    entity_registry.async_get_or_create(
        "climate", "zha", "uid7", suggested_object_id="ghost", device_id=dev.id
    )
    with (
        patch.object(dr.DeviceRegistry, "async_get", return_value=None),
        pytest.raises(zha_gateway.NotZhaEntity),
    ):
        zha_gateway.resolve_device(hass, "climate.ghost")


def test_error_hierarchy() -> None:
    assert issubclass(zha_gateway.ZhaUnavailable, zha_gateway.GatewayError)
    assert issubclass(zha_gateway.NotZhaEntity, zha_gateway.GatewayError)
    assert issubclass(zha_gateway.UnsupportedAttribute, zha_gateway.GatewayError)


# --- ZhaLibraryGateway ----------------------------------------------------


async def test_library_gateway_unavailable_when_zha_not_loaded(hass) -> None:
    with (
        patch(PATCH_TARGET, side_effect=KeyError("zha")),
        pytest.raises(zha_gateway.ZhaUnavailable),
    ):
        zha_gateway.ZhaLibraryGateway(hass)


async def test_library_gateway_unavailable_without_zha_setup(hass) -> None:
    # No patching: the real get_zha_gateway runs against a hass without ZHA.
    with pytest.raises(zha_gateway.ZhaUnavailable):
        zha_gateway.ZhaLibraryGateway(hass)


async def test_library_gateway_unavailable_when_no_gateway_object(hass) -> None:
    with (
        patch(PATCH_TARGET, side_effect=ValueError("No gateway object exists")),
        pytest.raises(zha_gateway.ZhaUnavailable, match="No gateway object exists"),
    ):
        zha_gateway.ZhaLibraryGateway(hass)


async def test_library_gateway_unavailable_when_import_breaks(hass) -> None:
    with (
        patch.dict("sys.modules", {"zigpy.types": None}),
        pytest.raises(zha_gateway.ZhaUnavailable),
    ):
        zha_gateway.ZhaLibraryGateway(hass)


async def test_library_gateway_probes_once_then_looks_up_per_call(hass) -> None:
    gw_obj = _gateway_with(None)
    with patch(PATCH_TARGET, return_value=gw_obj) as get_gateway:
        gw = zha_gateway.ZhaLibraryGateway(hass)
        get_gateway.assert_called_once_with(hass)
        with pytest.raises(zha_gateway.GatewayError):
            gw.is_available(IEEE)
        with pytest.raises(zha_gateway.GatewayError):
            gw.is_available(IEEE)
    assert get_gateway.call_count == 3


async def test_library_gateway_follows_a_replaced_zha_gateway(hass) -> None:
    """After a ZHA reload the old Gateway is shut down; use the new one."""
    device_b, _ = _device_with_cluster(({0x600D: 7}, {}))
    gateway_a = _gateway_with(MagicMock())
    gateway_b = _gateway_with(device_b)
    with patch(PATCH_TARGET, return_value=gateway_a) as get_gateway:
        gw = zha_gateway.ZhaLibraryGateway(hass)
        get_gateway.return_value = gateway_b  # ZHA entry reloaded
        await gw.write(IEEE, 1, 0xFC11, 0x600D, 7)
        assert await gw.read(IEEE, 1, 0xFC11, [0x600D]) == {0x600D: 7}
        assert gw.is_available(IEEE) is not None
    gateway_a.get_device.assert_not_called()
    assert gateway_b.get_device.call_count == 3
    device_b.write_zigbee_attribute.assert_awaited_once_with(1, 0xFC11, 0x600D, 7)


async def test_library_gateway_lookup_failure_after_construction_raises(hass) -> None:
    """ZHA being mid-reload (no gateway object) is a GatewayError, not a crash."""
    with patch(PATCH_TARGET, return_value=_gateway_with(MagicMock())) as get_gateway:
        gw = zha_gateway.ZhaLibraryGateway(hass)
        get_gateway.side_effect = ValueError("No gateway object exists")
        with pytest.raises(zha_gateway.GatewayError, match="No gateway object"):
            await gw.write(IEEE, 1, 0xFC11, 0x600D, 7)
        with pytest.raises(zha_gateway.GatewayError, match="No gateway object"):
            await gw.read(IEEE, 1, 0xFC11, [0x600D])
        with pytest.raises(zha_gateway.GatewayError, match="No gateway object"):
            gw.is_available(IEEE)


async def test_library_gateway_write_and_read_wrap_zha(hass) -> None:
    device = MagicMock()
    device.write_zigbee_attribute = AsyncMock()
    cluster = MagicMock()
    cluster.read_attributes = AsyncMock(return_value=({0x600D: 2140, 0x600E: 3}, {}))
    device.async_get_cluster.return_value = cluster
    gw_obj = MagicMock()
    gw_obj.get_device.return_value = device
    with patch(PATCH_TARGET, return_value=gw_obj):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        await gw.write("00:12:4b:00:aa:bb:cc:dd", 1, 0xFC11, 0x600D, 2140)
        assert await gw.read(
            "00:12:4b:00:aa:bb:cc:dd", 1, 0xFC11, [0x600D, 0x600E]
        ) == {
            0x600D: 2140,
            0x600E: 3,
        }
    device.write_zigbee_attribute.assert_awaited_once_with(1, 0xFC11, 0x600D, 2140)
    device.async_get_cluster.assert_called_once_with(1, 0xFC11)
    cluster.read_attributes.assert_awaited_once_with(
        [0x600D, 0x600E], allow_cache=False, only_cache=False
    )
    gw_obj.get_device.assert_called_with(EUI64.convert("00:12:4b:00:aa:bb:cc:dd"))


async def test_library_gateway_read_failure_raises(hass) -> None:
    device, _ = _device_with_cluster(({0x600E: 3}, {0x600D: "UNSUPPORTED_ATTRIBUTE"}))
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError, match="0x600D") as excinfo:
            await gw.read(IEEE, 1, 0xFC11, [0x600D, 0x600E])
    assert "UNSUPPORTED_ATTRIBUTE" in str(excinfo.value)


async def test_library_gateway_read_of_an_unsupported_primary_attribute(hass) -> None:
    failure = {0x6011: Status.UNSUPPORTED_ATTRIBUTE}
    device, _ = _device_with_cluster(({0x600E: 3}, failure))
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.UnsupportedAttribute, match="0x6011"):
            await gw.read(IEEE, 1, 0xFC11, [0x6011, 0x600E])


async def test_library_gateway_read_of_an_unsupported_extra_attribute_is_plain(
    hass,
) -> None:
    failure = {0x600E: Status.UNSUPPORTED_ATTRIBUTE}
    device, _ = _device_with_cluster(({0x600D: 2140}, failure))
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError, match="0x600E") as excinfo:
            await gw.read(IEEE, 1, 0xFC11, [0x600D, 0x600E])
    assert type(excinfo.value) is zha_gateway.GatewayError


@pytest.mark.parametrize("failure", [{0x6011: Status.FAILURE}, {}])
async def test_library_gateway_other_read_failures_of_the_primary_are_plain(
    hass, failure
) -> None:
    device, _ = _device_with_cluster(({0x600E: 3}, failure))
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError, match="0x6011") as excinfo:
            await gw.read(IEEE, 1, 0xFC11, [0x6011, 0x600E])
    assert type(excinfo.value) is zha_gateway.GatewayError


async def test_library_gateway_read_missing_attribute_raises(hass) -> None:
    device, _ = _device_with_cluster(({0x600E: 3}, {}))
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError, match="0x600D"):
            await gw.read(IEEE, 1, 0xFC11, [0x600D, 0x600E])


async def test_library_gateway_read_returns_plain_ints(hass) -> None:
    class Uint16(int):
        """Stand-in for zigpy's int subclasses."""

    device, _ = _device_with_cluster(({0x600D: Uint16(2140)}, {}))
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        result = await gw.read(IEEE, 1, 0xFC11, [0x600D])
    assert result == {0x600D: 2140}
    assert type(result[0x600D]) is int


async def test_library_gateway_read_rejects_non_integer_value(hass) -> None:
    device, _ = _device_with_cluster(({0x600D: None}, {}))
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError, match="0x600D"):
            await gw.read(IEEE, 1, 0xFC11, [0x600D])


@pytest.mark.parametrize("error", [KeyError(1), TimeoutError(), ValueError("boom")])
async def test_library_gateway_read_wraps_lookup_and_io_errors(
    hass, error: Exception
) -> None:
    device, cluster = _device_with_cluster(({}, {}))
    cluster.read_attributes = AsyncMock(side_effect=error)
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError) as excinfo:
            await gw.read(IEEE, 1, 0xFC11, [0x600D])
    assert excinfo.value.__cause__ is error
    assert str(excinfo.value)


async def test_library_gateway_read_wraps_missing_cluster(hass) -> None:
    device, _ = _device_with_cluster(({}, {}))
    device.async_get_cluster.side_effect = KeyError(0xFC11)
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError):
            await gw.read(IEEE, 1, 0xFC11, [0x600D])


@pytest.mark.parametrize("error", [ValueError("no cluster"), TimeoutError()])
async def test_library_gateway_write_wraps_errors(hass, error: Exception) -> None:
    device, _ = _device_with_cluster(({}, {}))
    device.write_zigbee_attribute = AsyncMock(side_effect=error)
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError) as excinfo:
            await gw.write(IEEE, 1, 0xFC11, 0x600D, 2140)
    assert excinfo.value.__cause__ is error
    assert str(excinfo.value)


def _record(status: Status, attrid: int | None = None) -> WriteAttributesStatusRecord:
    if attrid is None:
        return WriteAttributesStatusRecord(status=status)
    return WriteAttributesStatusRecord(status=status, attrid=attrid)


@pytest.mark.parametrize(
    ("response", "status_name"),
    [
        # zigpy 2.x: one list holding one record per written attribute
        ([[_record(Status.READ_ONLY, 0x600D)]], "READ_ONLY"),
        # older zigpy: a WriteAttributesResponse (list) holding only the failures
        ([[_record(Status.NOT_AUTHORIZED, 0x600D)]], "NOT_AUTHORIZED"),
        # flat list of records
        ([_record(Status.INVALID_VALUE, 0x600D)], "INVALID_VALUE"),
        # a success record next to a failed one
        (
            [
                [
                    _record(Status.SUCCESS, 0x1234),
                    _record(Status.UNSUPPORTED_ATTRIBUTE, 0x600D),
                ]
            ],
            "UNSUPPORTED_ATTRIBUTE",
        ),
        # quirks may hand back bare statuses
        ([Status.FAILURE], "FAILURE"),
        (Status.INVALID_VALUE, "INVALID_VALUE"),
    ],
)
async def test_library_gateway_write_rejected_status_raises(
    hass, response, status_name
) -> None:
    device, _ = _device_with_cluster(({}, {}))
    device.write_zigbee_attribute = AsyncMock(return_value=response)
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError, match="write rejected") as excinfo:
            await gw.write(IEEE, 1, 0xFC11, 0x600D, 2140)
    assert "0x600D" in str(excinfo.value)
    assert status_name in str(excinfo.value)


@pytest.mark.parametrize(
    "response",
    [
        [[_record(Status.UNSUPPORTED_ATTRIBUTE, 0x6011)]],
        [_record(Status.UNSUPPORTED_ATTRIBUTE, 0x6011)],
        [[_record(Status.SUCCESS, 0x1234), _record(Status.UNSUPPORTED_ATTRIBUTE)]],
        [Status.UNSUPPORTED_ATTRIBUTE],
        Status.UNSUPPORTED_ATTRIBUTE,
    ],
)
async def test_library_gateway_write_of_an_unsupported_attribute_has_its_own_error(
    hass, response
) -> None:
    device, _ = _device_with_cluster(({}, {}))
    device.write_zigbee_attribute = AsyncMock(return_value=response)
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(
            zha_gateway.UnsupportedAttribute, match="write rejected"
        ) as excinfo:
            await gw.write(IEEE, 1, 0xFC11, 0x6011, -40)
    assert "0x6011" in str(excinfo.value)
    assert "UNSUPPORTED_ATTRIBUTE" in str(excinfo.value)


@pytest.mark.parametrize(
    "status", [Status.READ_ONLY, Status.NOT_AUTHORIZED, Status.INVALID_VALUE]
)
async def test_library_gateway_other_write_rejections_stay_plain(hass, status) -> None:
    device, _ = _device_with_cluster(({}, {}))
    device.write_zigbee_attribute = AsyncMock(return_value=[[_record(status, 0x6011)]])
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError) as excinfo:
            await gw.write(IEEE, 1, 0xFC11, 0x6011, -40)
    assert type(excinfo.value) is zha_gateway.GatewayError


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        [[]],
        # global success: a single SUCCESS record without an attribute id
        [[_record(Status.SUCCESS)]],
        [[_record(Status.SUCCESS, 0x600D)]],
        [_record(Status.SUCCESS)],
        [Status.SUCCESS],
        Status.SUCCESS,
    ],
)
async def test_library_gateway_write_accepts_success_responses(hass, response) -> None:
    device, _ = _device_with_cluster(({}, {}))
    device.write_zigbee_attribute = AsyncMock(return_value=response)
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        await gw.write(IEEE, 1, 0xFC11, 0x600D, 2140)
    device.write_zigbee_attribute.assert_awaited_once_with(1, 0xFC11, 0x600D, 2140)


async def test_library_gateway_unknown_device_raises(hass) -> None:
    with patch(PATCH_TARGET, return_value=_gateway_with(None)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError):
            await gw.write(IEEE, 1, 0xFC11, 0x600D, 2140)
        with pytest.raises(zha_gateway.GatewayError):
            await gw.read(IEEE, 1, 0xFC11, [0x600D])
        with pytest.raises(zha_gateway.GatewayError):
            gw.is_available(IEEE)


async def test_library_gateway_invalid_ieee_raises_gateway_error(hass) -> None:
    with patch(PATCH_TARGET, return_value=_gateway_with(MagicMock())):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError):
            gw.is_available("not-an-ieee")


@pytest.mark.parametrize("available", [True, False])
async def test_library_gateway_is_available(hass, available) -> None:
    device = MagicMock()
    device.available = available
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        assert gw.is_available(IEEE) is available


async def test_library_gateway_is_available_wraps_errors(hass) -> None:
    device = MagicMock()
    type(device).available = PropertyMock(side_effect=RuntimeError("boom"))
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError, match="boom"):
            gw.is_available(IEEE)


async def test_library_gateway_resolve_delegates_to_registries(
    hass, device_registry, entity_registry
) -> None:
    entry = MockConfigEntry(domain="zha")
    entry.add_to_hass(hass)
    dev = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("zha", IEEE)},
        manufacturer="SONOFF",
        model="TRVZB",
        sw_version="1.2.1",
    )
    entity_registry.async_get_or_create(
        "climate", "zha", "uid6", suggested_object_id="via_gateway", device_id=dev.id
    )
    with patch(PATCH_TARGET, return_value=_gateway_with(None)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        ref = gw.resolve("climate.via_gateway")
    assert ref == DeviceRef(
        "climate.via_gateway", dev.id, IEEE, "SONOFF", "TRVZB", (1, 2, 1)
    )


def test_zha_api_matches_the_calls_the_adapter_makes() -> None:
    """Pin the installed zha API the adapter relies on (unstable internals)."""
    write = list(inspect.signature(Device.write_zigbee_attribute).parameters)
    assert write[:5] == ["self", "endpoint_id", "cluster_id", "attribute", "value"]
    get_cluster = list(inspect.signature(Device.async_get_cluster).parameters)
    assert get_cluster[:3] == ["self", "endpoint_id", "cluster_id"]
    assert isinstance(Device.available, property)
    assert list(inspect.signature(Gateway.get_device).parameters) == ["self", "ieee"]


# --- subscribe ------------------------------------------------------------

THERMOSTAT = 0x0201
LOCAL_TEMP_SETPOINT = 0x0012


def _noop(*_args: object) -> None:
    """An observation callback that ignores everything."""


class _EventCluster(EventBase):
    """A zigpy-like cluster: real EventBase semantics, scriptable reads."""

    def __init__(self) -> None:
        super().__init__()
        self.read_attributes = AsyncMock(return_value=({}, {}))

    def fire(
        self, event_class: type, attribute: int, value: int, **extra: object
    ) -> None:
        fields: dict[str, object] = {
            "device_ieee": IEEE,
            "endpoint_id": 1,
            "cluster_type": ClusterType.Server,
            "cluster_id": THERMOSTAT,
            "attribute_name": "x",
            "attribute_id": attribute,
            "manufacturer_code": None,
            "value": value,
        }
        if event_class in (AttributeReportedEvent, AttributeReadEvent):
            fields["raw_value"] = value
        if event_class is AttributeWrittenEvent:
            fields["status"] = Status.SUCCESS
        fields.update(extra)
        self.emit(event_class.event_type, event_class(**fields))


def _subscribable() -> tuple[MagicMock, _EventCluster]:
    device = MagicMock()
    device.write_zigbee_attribute = AsyncMock()
    cluster = _EventCluster()
    device.async_get_cluster.return_value = cluster
    return device, cluster


async def test_subscribe_delivers_device_reports(hass) -> None:
    device, cluster = _subscribable()
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(
            IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], lambda *a: seen.append(a)
        )
    device.async_get_cluster.assert_called_once_with(1, THERMOSTAT)
    cluster.fire(AttributeReportedEvent, LOCAL_TEMP_SETPOINT, 2150)
    assert seen == [(IEEE, LOCAL_TEMP_SETPOINT, "report", 2150)]


async def test_subscribe_delivers_updated_events_as_reports(hass) -> None:
    """Quirk-transformed and unknown-attribute reports arrive as updated events."""
    device, cluster = _subscribable()
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(
            IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], lambda *a: seen.append(a)
        )
    cluster.fire(AttributeUpdatedEvent, LOCAL_TEMP_SETPOINT, 2200)
    assert seen == [(IEEE, LOCAL_TEMP_SETPOINT, "report", 2200)]


async def test_subscribe_delivers_foreign_writes_as_written(hass) -> None:
    device, cluster = _subscribable()
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(
            IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], lambda *a: seen.append(a)
        )
    cluster.fire(AttributeWrittenEvent, LOCAL_TEMP_SETPOINT, 1900)
    cluster.fire(
        AttributeWrittenEvent, LOCAL_TEMP_SETPOINT, 1800, status=Status.FAILURE
    )
    assert seen == [(IEEE, LOCAL_TEMP_SETPOINT, "written", 1900)]


async def test_subscribe_ignores_other_attributes_and_plain_reads(hass) -> None:
    device, cluster = _subscribable()
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(
            IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], lambda *a: seen.append(a)
        )
    cluster.fire(AttributeReportedEvent, 0x0000, 2000)
    cluster.fire(AttributeReadEvent, LOCAL_TEMP_SETPOINT, 2000)
    assert seen == []


async def test_subscribe_skips_non_integer_values(hass) -> None:
    device, cluster = _subscribable()
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(
            IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], lambda *a: seen.append(a)
        )
    cluster.fire(AttributeReportedEvent, LOCAL_TEMP_SETPOINT, None)
    assert seen == []


async def test_a_raising_callback_does_not_break_zigpy(hass) -> None:
    device, cluster = _subscribable()
    calls: list[int] = []

    def boom(_ieee: str, _attr: int, _kind: str, raw: int) -> None:
        calls.append(raw)
        raise RuntimeError

    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], boom)
    cluster.fire(AttributeReportedEvent, LOCAL_TEMP_SETPOINT, 2150)
    cluster.fire(AttributeReportedEvent, LOCAL_TEMP_SETPOINT, 2160)
    assert calls == [2150, 2160]


async def test_own_write_is_not_delivered(hass) -> None:
    device, cluster = _subscribable()

    async def write(*_args: object) -> None:
        cluster.fire(AttributeWrittenEvent, LOCAL_TEMP_SETPOINT, 2150)
        cluster.fire(AttributeUpdatedEvent, LOCAL_TEMP_SETPOINT, 2150)

    device.write_zigbee_attribute = AsyncMock(side_effect=write)
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(
            IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], lambda *a: seen.append(a)
        )
        await gw.write(IEEE, 1, THERMOSTAT, LOCAL_TEMP_SETPOINT, 2150)
        # in-flight ends with the write: a later report is delivered again
        cluster.fire(AttributeReportedEvent, LOCAL_TEMP_SETPOINT, 2150)
    assert seen == [(IEEE, LOCAL_TEMP_SETPOINT, "report", 2150)]


async def test_own_failed_write_clears_in_flight(hass) -> None:
    device, cluster = _subscribable()
    device.write_zigbee_attribute = AsyncMock(side_effect=TimeoutError())
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(
            IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], lambda *a: seen.append(a)
        )
        with pytest.raises(zha_gateway.GatewayError):
            await gw.write(IEEE, 1, THERMOSTAT, LOCAL_TEMP_SETPOINT, 2150)
        cluster.fire(AttributeReportedEvent, LOCAL_TEMP_SETPOINT, 2150)
    assert len(seen) == 1


async def test_own_read_is_not_delivered(hass) -> None:
    device, cluster = _subscribable()

    async def read(_attributes: list[int], **_kwargs: object) -> tuple[dict, dict]:
        cluster.fire(AttributeReadEvent, LOCAL_TEMP_SETPOINT, 2150)
        cluster.fire(AttributeUpdatedEvent, LOCAL_TEMP_SETPOINT, 2150)
        return {LOCAL_TEMP_SETPOINT: 2150}, {}

    cluster.read_attributes = AsyncMock(side_effect=read)
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(
            IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], lambda *a: seen.append(a)
        )
        assert await gw.read(IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT]) == {
            LOCAL_TEMP_SETPOINT: 2150
        }
        cluster.fire(AttributeReportedEvent, LOCAL_TEMP_SETPOINT, 2150)
    assert len(seen) == 1


async def test_in_flight_only_covers_the_written_attribute(hass) -> None:
    device, cluster = _subscribable()

    async def write(*_args: object) -> None:
        cluster.fire(AttributeReportedEvent, 0x001C, 4)

    device.write_zigbee_attribute = AsyncMock(side_effect=write)
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(IEEE, 1, THERMOSTAT, [0x001C], lambda *a: seen.append(a))
        await gw.write(IEEE, 1, THERMOSTAT, LOCAL_TEMP_SETPOINT, 2150)
    assert seen == [(IEEE, 0x001C, "report", 4)]


async def test_overlapping_operations_keep_in_flight_until_all_finish(hass) -> None:
    device, cluster = _subscribable()
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_write(*_args: object) -> None:
        started.set()
        await release.wait()

    device.write_zigbee_attribute = AsyncMock(side_effect=slow_write)
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        gw.subscribe(
            IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], lambda *a: seen.append(a)
        )
        first = asyncio.create_task(
            gw.write(IEEE, 1, THERMOSTAT, LOCAL_TEMP_SETPOINT, 1)
        )
        await started.wait()
        cluster.read_attributes = AsyncMock(return_value=({LOCAL_TEMP_SETPOINT: 1}, {}))
        await gw.read(IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT])
        cluster.fire(AttributeReportedEvent, LOCAL_TEMP_SETPOINT, 1)
        assert seen == []  # the write is still in flight
        release.set()
        await first
        cluster.fire(AttributeReportedEvent, LOCAL_TEMP_SETPOINT, 1)
    assert len(seen) == 1


async def test_unsubscribe_stops_delivery(hass) -> None:
    device, cluster = _subscribable()
    seen: list[tuple[str, int, str, int]] = []
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        unsubscribe = gw.subscribe(
            IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], lambda *a: seen.append(a)
        )
    unsubscribe()
    unsubscribe()  # idempotent
    cluster.fire(AttributeReportedEvent, LOCAL_TEMP_SETPOINT, 2150)
    cluster.fire(AttributeUpdatedEvent, LOCAL_TEMP_SETPOINT, 2150)
    cluster.fire(AttributeWrittenEvent, LOCAL_TEMP_SETPOINT, 2150)
    assert seen == []


async def test_unsubscribe_never_raises(hass) -> None:
    device, cluster = _subscribable()
    cluster.on_event = MagicMock(
        side_effect=lambda *_a: MagicMock(side_effect=KeyError)
    )
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        unsubscribe = gw.subscribe(IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], _noop)
    unsubscribe()
    unsubscribe()


async def test_subscribe_unknown_device_raises_zha_unavailable(hass) -> None:
    with patch(PATCH_TARGET, return_value=_gateway_with(None)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.ZhaUnavailable):
            gw.subscribe(IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], _noop)


async def test_subscribe_without_cluster_raises_gateway_error(hass) -> None:
    device, _ = _subscribable()
    device.async_get_cluster.side_effect = KeyError(THERMOSTAT)
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError):
            gw.subscribe(IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], _noop)


async def test_subscribe_removes_listeners_if_registration_fails(hass) -> None:
    device, cluster = _subscribable()
    real = cluster.on_event
    calls = 0

    def flaky(name: str, callback: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError
        return real(name, callback)

    cluster.on_event = flaky
    with patch(PATCH_TARGET, return_value=_gateway_with(device)):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        with pytest.raises(zha_gateway.GatewayError):
            gw.subscribe(IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], _noop)
    listeners = cluster._event_listeners.values()  # noqa: SLF001
    assert all(not registered for registered in listeners)


def test_zigpy_event_api_matches_what_the_adapter_subscribes_to() -> None:
    """Pin the zigpy event contract subscribe() relies on (unstable internals)."""
    assert AttributeReportedEvent.event_type == "attribute_report"
    assert AttributeUpdatedEvent.event_type == "attribute_updated"
    assert AttributeWrittenEvent.event_type == "attribute_written"
    on_event = list(inspect.signature(EventBase.on_event).parameters)
    assert on_event[:3] == ["self", "event_name", "callback"]


# --- FakeGateway ----------------------------------------------------------


async def test_fake_gateway_contract() -> None:
    fake = FakeGateway()
    ref = fake.add_device("climate.living_trv", IEEE)
    assert ref == DeviceRef(
        "climate.living_trv", f"dev-{IEEE}", IEEE, "SONOFF", "TRVZB", (1, 3, 0)
    )
    assert fake.resolve("climate.living_trv") == ref
    with pytest.raises(zha_gateway.NotZhaEntity):
        fake.resolve("climate.unknown")

    # availability defaults to True and can be switched off per device
    assert fake.is_available(IEEE) is True
    fake.availability[IEEE] = False
    assert fake.is_available(IEEE) is False

    # a write is recorded and stored, a read returns it and is recorded
    await fake.write(IEEE, 1, 0xFC11, 0x600D, 2140)
    assert fake.writes == [(IEEE, 1, 0xFC11, 0x600D, 2140)]
    assert fake.store == {(IEEE, 1, 0xFC11, 0x600D): 2140}
    assert await fake.read(IEEE, 1, 0xFC11, [0x600D]) == {0x600D: 2140}
    assert fake.reads == [(IEEE, 1, 0xFC11, (0x600D,))]

    # fail_write raises once, stores nothing, then clears itself
    fake.fail_write[(IEEE, 0x600D)] = "radio busy"
    with pytest.raises(zha_gateway.GatewayError, match="radio busy"):
        await fake.write(IEEE, 1, 0xFC11, 0x600D, 2200)
    assert fake.store[(IEEE, 1, 0xFC11, 0x600D)] == 2140
    await fake.write(IEEE, 1, 0xFC11, 0x600D, 2200)
    assert fake.store[(IEEE, 1, 0xFC11, 0x600D)] == 2200
    assert fake.fail_write == {}

    # read_override wins over the stored value
    fake.read_override[(IEEE, 0x600D)] = 1234
    assert await fake.read(IEEE, 1, 0xFC11, [0x600D]) == {0x600D: 1234}

    # fail_read raises once, then clears itself
    fake.fail_read[(IEEE, 0x600D)] = "no answer"
    with pytest.raises(zha_gateway.GatewayError, match="no answer"):
        await fake.read(IEEE, 1, 0xFC11, [0x600D])
    assert fake.fail_read == {}

    # an attribute that was never written cannot be read
    with pytest.raises(zha_gateway.GatewayError, match="0x600E"):
        await fake.read(IEEE, 1, 0xFC11, [0x600E])

    # unsupported_write keeps raising UnsupportedAttribute, records the write
    # and stores nothing; other attributes and devices are not affected
    fake.unsupported_write.add((IEEE, 0x6011))
    for _ in range(2):
        with pytest.raises(zha_gateway.UnsupportedAttribute, match="0x6011"):
            await fake.write(IEEE, 1, 0xFC11, 0x6011, -40)
    assert fake.writes[-1] == (IEEE, 1, 0xFC11, 0x6011, -40)
    assert (IEEE, 1, 0xFC11, 0x6011) not in fake.store
    await fake.write(IEEE, 1, 0xFC11, 0x600D, 2300)
    fake.unsupported_write.clear()

    # unknown devices fail like the real adapter, leaving no trace
    unknown = "00:12:4b:00:00:00:00:99"
    message = f"device {unknown} is not known to ZHA"
    writes_before, reads_before = list(fake.writes), list(fake.reads)
    store_before = dict(fake.store)
    with pytest.raises(GatewayError, match=message):
        fake.is_available(unknown)
    with pytest.raises(GatewayError, match=message):
        await fake.write(unknown, 1, 0xFC11, 0x600D, 2140)
    with pytest.raises(GatewayError, match=message):
        await fake.read(unknown, 1, 0xFC11, [0x600D])
    assert fake.writes == writes_before
    assert fake.reads == reads_before
    assert fake.store == store_before


async def test_fake_gateway_subscribe_and_inject() -> None:
    fake = FakeGateway()
    fake.add_device("climate.living_trv", IEEE)
    seen: list[tuple[str, int, str, int]] = []
    other: list[tuple[str, int, str, int]] = []

    def callback(*args: object) -> None:
        seen.append(args)  # type: ignore[arg-type]

    unsubscribe = fake.subscribe(
        IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT, 0x001C], callback
    )
    fake.subscribe(IEEE, 1, THERMOSTAT, [0x0000], lambda *a: other.append(a))
    assert fake.subscriptions[0][:4] == (
        IEEE,
        1,
        THERMOSTAT,
        (LOCAL_TEMP_SETPOINT, 0x001C),
    )

    fake.inject(IEEE, LOCAL_TEMP_SETPOINT, 2150)
    fake.inject(IEEE, 0x001C, 4, kind="written")
    fake.inject(IEEE, 0x0099, 1)
    fake.inject("00:12:4b:00:00:00:00:99", LOCAL_TEMP_SETPOINT, 1)
    assert seen == [
        (IEEE, LOCAL_TEMP_SETPOINT, "report", 2150),
        (IEEE, 0x001C, "written", 4),
    ]
    assert other == []

    # the fake's own write does not echo; unsubscribe removes the record
    await fake.write(IEEE, 1, THERMOSTAT, LOCAL_TEMP_SETPOINT, 2200)
    assert len(seen) == 2
    unsubscribe()
    unsubscribe()
    fake.inject(IEEE, LOCAL_TEMP_SETPOINT, 2150)
    assert len(seen) == 2
    assert len(fake.subscriptions) == 1


async def test_fake_gateway_subscribe_unknown_device_raises() -> None:
    fake = FakeGateway()
    with pytest.raises(zha_gateway.GatewayError):
        fake.subscribe(IEEE, 1, THERMOSTAT, [LOCAL_TEMP_SETPOINT], _noop)
