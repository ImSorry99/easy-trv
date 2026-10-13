import inspect
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry
from zha.application.gateway import Gateway
from zha.zigbee.device import Device
from zigpy.types import EUI64
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
