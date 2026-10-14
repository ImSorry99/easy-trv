import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.config_entries import HANDLERS, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easy_trv import const
from custom_components.easy_trv.config_flow import (
    EasyTrvConfigFlow,
    EasyTrvOptionsFlow,
    RoomSubentryFlow,
    prefill_from_area,
    validate_room,
)
from custom_components.easy_trv.const import DOMAIN, SUBENTRY_ROOM
from custom_components.easy_trv.core.model import RoomParams
from custom_components.easy_trv.core.settings import resolve_settings
from custom_components.easy_trv.profiles.loader import BUILTIN_DIR, async_load_profiles
from tests import helpers_entry
from tests.helpers_entry import (
    FAST_OPTIONS,
    IEEE_A,
    IEEE_B,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
    use_config_dir,
)

IEEE_ZBT = "00:33"
BUILTIN_TEXT = (BUILTIN_DIR / "sonoff_trvzb_native.yaml").read_text()
MANIFEST = Path(helpers_entry.__file__).parent.parent / (
    "custom_components/easy_trv/manifest.json"
)


@pytest.fixture
def entry(hass):
    """An entry without rooms that is not set up."""
    return make_entry(hass, [], {})


@pytest.fixture
def entry_with_room(hass):
    """An entry with the room Living (climate.a and climate.b), not set up."""
    return make_entry(hass, [room_data()], FAST_OPTIONS)


# --- Registry helpers -----------------------------------------------------


@pytest.fixture
def device_owner(hass):
    """The config entry that owns every device the tests register."""
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    return owner


def add_device(hass, owner, identifier, *, area_id=None, **kwargs):
    kwargs.setdefault("manufacturer", "SONOFF")
    kwargs.setdefault("model", "TRVZB")
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={identifier},
        name=identifier[1],
        sw_version="0x00001300",
        **kwargs,
    )
    if area_id is not None:
        registry.async_update_device(device.id, area_id=area_id)
    return device


def add_entity(  # noqa: PLR0913 - mirrors the registry's keyword options
    hass, entity_id, platform, *, device=None, area_id=None, device_class=None, **kwargs
):
    domain, object_id = entity_id.split(".")
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        domain,
        platform,
        f"{platform}-{object_id}",
        suggested_object_id=object_id,
        # An entity needs a name of its own before it can have an area of its own.
        original_name=object_id,
        device_id=device.id if device else None,
        original_device_class=device_class,
        **kwargs,
    )
    assert entity.entity_id == entity_id
    if area_id is not None:
        registry.async_update_entity(entity_id, area_id=area_id)
    return entity


@pytest.fixture
def area_with_trvs(hass, device_owner):
    """The area Living: two TRVZB, a temperature and a humidity sensor.

    Only the devices are in the area; the entities have no area of their own,
    so the area of an entity has to be derived from its device.
    """
    area = ar.async_get(hass).async_create("Living")
    trv_a = add_device(hass, device_owner, ("zha", IEEE_A), area_id=area.id)
    trv_b = add_device(hass, device_owner, ("zha", IEEE_B), area_id=area.id)
    multi = add_device(
        hass,
        device_owner,
        ("test", "multi"),
        area_id=area.id,
        manufacturer="Acme",
        model="Multi",
    )
    add_entity(hass, "climate.a", "zha", device=trv_a)
    add_entity(hass, "climate.b", "zha", device=trv_b)
    # The thermostat's own temperature reading is not a room sensor.
    add_entity(
        hass, "sensor.a_temperature", "zha", device=trv_a, device_class="temperature"
    )
    add_entity(hass, "sensor.t1", "test", device=multi, device_class="temperature")
    add_entity(hass, "sensor.h1", "test", device=multi, device_class="humidity")
    return area


@pytest.fixture
def gateway(hass, request):
    """The fake gateway, patched in once the registry entries exist.

    The states it sets would make the registry pick ``climate.a_2`` for an
    entity that is registered later, hence the area is created first.
    """
    request.getfixturevalue("area_with_trvs")
    with patched_gateway(hass) as fake:
        yield fake


@pytest.fixture
def foreign_trvs(hass, device_owner):
    """A ZHA thermostat without a profile and a thermostat of another integration."""
    zbt = add_device(hass, device_owner, ("zha", IEEE_ZBT), model="TRV-ZBT")
    add_entity(hass, "climate.zbt", "zha", device=zbt)
    mqtt = add_device(
        hass, device_owner, ("mqtt", "trv"), manufacturer="Acme", model="MQTT TRV"
    )
    add_entity(hass, "climate.mqtt_trv", "mqtt", device=mqtt)


# --- Flow helpers ---------------------------------------------------------


def room_flows(hass):
    return hass.config_entries.subentries


async def open_room_flow(hass, entry, choice):
    """Start the "add room" flow and pick ``choice`` in its menu."""
    result = await room_flows(hass).async_init(
        (entry.entry_id, SUBENTRY_ROOM), context={"source": "user"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "user"
    assert result["menu_options"] == ["from_area", "custom"]
    return await room_flows(hass).async_configure(
        result["flow_id"], {"next_step_id": choice}
    )


async def open_entities_step(hass, entry, name="Bedroom"):
    """Walk the custom path up to the entities form."""
    result = await open_room_flow(hass, entry, "custom")
    assert result["step_id"] == "custom"
    result = await room_flows(hass).async_configure(result["flow_id"], {"name": name})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "entities"
    return result


def entities_input(**overrides: Any) -> dict[str, Any]:
    data = {
        "trvs": ["climate.a", "climate.b"],
        "temp_sensors": ["sensor.t1"],
        "humidity_sensors": [],
        "profile": "auto",
        "window_detection": "on",
        "hysteresis": "leave",
    }
    data.update(overrides)
    return data


def fields(result) -> dict[str, Any]:
    """Map the field names of a form to their selectors."""
    return {str(key): value for key, value in result["data_schema"].schema.items()}


def suggested_values(result) -> dict[str, Any]:
    return {
        str(key): key.description["suggested_value"]
        for key in result["data_schema"].schema
        if isinstance(key, vol.Marker)
        and key.description
        and "suggested_value" in key.description
    }


def defaults(result) -> dict[str, Any]:
    return {
        str(key): key.default()
        for key in result["data_schema"].schema
        if isinstance(key, vol.Marker) and key.default is not vol.UNDEFINED
    }


def option_values(selector) -> list[str]:
    return [
        option if isinstance(option, str) else option["value"]
        for option in selector.config["options"]
    ]


# --- Config flow ----------------------------------------------------------


async def test_user_flow_creates_single_entry(hass) -> None:
    with patched_gateway(hass):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Easy TRV"
    assert result["data"] == {}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_config_flow_is_the_registered_handler(entry) -> None:
    # The stand-in flow of the earlier tests is gone: the real one answers.
    assert HANDLERS[DOMAIN] is EasyTrvConfigFlow
    assert EasyTrvConfigFlow.VERSION == 2
    assert not hasattr(helpers_entry, "_StandInFlow")
    assert not hasattr(helpers_entry, "CONFIG_FLOW_FILE")
    assert EasyTrvConfigFlow.async_get_supported_subentry_types(entry) == {
        SUBENTRY_ROOM: RoomSubentryFlow
    }
    assert isinstance(
        EasyTrvConfigFlow.async_get_options_flow(entry), EasyTrvOptionsFlow
    )


def test_manifest_enables_the_config_flow() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["config_flow"] is True
    # hassfest order: domain, name, then alphabetical.
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


# --- Room flow: creating a room --------------------------------------------


async def test_room_from_area_prefills_and_creates(hass, entry, area_with_trvs) -> None:
    result = await open_room_flow(hass, entry, "from_area")
    assert result["step_id"] == "from_area"
    assert list(fields(result)) == ["area_id"]

    result = await room_flows(hass).async_configure(
        result["flow_id"], {"area_id": area_with_trvs.id}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "entities"
    assert suggested_values(result) == {
        "trvs": ["climate.a", "climate.b"],
        "temp_sensors": ["sensor.t1"],
        "humidity_sensors": ["sensor.h1"],
    }

    result = await room_flows(hass).async_configure(
        result["flow_id"],
        {
            "trvs": ["climate.a", "climate.b"],
            "temp_sensors": ["sensor.t1"],
            "humidity_sensors": [],
            "profile": "auto",
            "window_detection": "on",
            "hysteresis": "0_4",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living"

    sub = next(iter(entry.subentries.values()))
    assert sub.subentry_type == SUBENTRY_ROOM
    assert sub.title == "Living"
    assert sub.unique_id == "living"
    assert sub.data["trvs"] == ["climate.a", "climate.b"]
    assert sub.data["hysteresis"] == "0_4"
    assert dict(sub.data) == {
        "name": "Living",
        "area_id": area_with_trvs.id,
        "trvs": ["climate.a", "climate.b"],
        "temp_sensors": ["sensor.t1"],
        "humidity_sensors": [],
        "profile": "auto",
        "window_detection": "on",
        "hysteresis": "0_4",
    }


async def test_custom_room_has_no_prefill_and_no_area(hass, entry) -> None:
    result = await open_entities_step(hass, entry, name="  Bedroom ")
    assert suggested_values(result) == {}

    result = await room_flows(hass).async_configure(
        result["flow_id"], {"trvs": ["climate.a"]}
    )
    # Without registry entries the thermostat cannot be resolved.
    assert result["errors"] == {"trvs": "not_zha"}


@pytest.mark.usefixtures("area_with_trvs")
async def test_custom_room_is_stored_with_defaults(hass, entry) -> None:
    result = await open_entities_step(hass, entry, name="  Bedroom ")
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"trvs": ["climate.a"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bedroom"
    sub = next(iter(entry.subentries.values()))
    assert sub.unique_id == "bedroom"
    assert dict(sub.data) == {
        "name": "Bedroom",
        "area_id": None,
        "trvs": ["climate.a"],
        "temp_sensors": [],
        "humidity_sensors": [],
        "profile": "auto",
        "window_detection": "on",
        "hysteresis": "leave",
    }


@pytest.mark.usefixtures("area_with_trvs")
async def test_entities_form_fields_and_defaults(hass, entry) -> None:
    result = await open_entities_step(hass, entry)

    selectors = fields(result)
    assert list(selectors) == [
        "trvs",
        "temp_sensors",
        "humidity_sensors",
        "profile",
        "window_detection",
        "hysteresis",
    ]
    assert selectors["trvs"].config["multiple"] is True
    assert selectors["trvs"].config["filter"] == [
        {"integration": "zha", "domain": ["climate"]}
    ]
    assert selectors["temp_sensors"].config["multiple"] is True
    assert selectors["temp_sensors"].config["reorder"] is True
    assert selectors["temp_sensors"].config["filter"] == [
        {"domain": ["sensor"], "device_class": ["temperature"]}
    ]
    assert selectors["humidity_sensors"].config["filter"] == [
        {"domain": ["sensor"], "device_class": ["humidity"]}
    ]
    assert option_values(selectors["window_detection"]) == ["on", "off", "leave"]
    assert selectors["window_detection"].config["translation_key"] == (
        "window_detection"
    )
    assert option_values(selectors["hysteresis"]) == [
        "leave",
        "0_2",
        "0_4",
        "0_6",
        "0_8",
        "1_0",
    ]
    assert selectors["hysteresis"].config["translation_key"] == "hysteresis"
    assert selectors["profile"].config["mode"] == "dropdown"
    assert defaults(result) == {
        "humidity_sensors": [],
        "temp_sensors": [],
        "profile": "auto",
        "window_detection": "on",
        "hysteresis": "leave",
    }


async def test_profile_options_list_built_in_and_user_profiles(
    hass, tmp_path, entry
) -> None:
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_zbt", 1)
    text = text.replace("name: Sonoff TRVZB (native)", "name: My ZBT", 1)
    use_config_dir(hass, tmp_path).joinpath("zbt.yaml").write_text(text)

    result = await open_entities_step(hass, entry)

    profile = fields(result)["profile"]
    assert profile.config["options"] == [
        {"value": "auto", "label": "Auto"},
        {"value": "sonoff_trvzb_native", "label": "Sonoff TRVZB (native)"},
        {"value": "user_zbt", "label": "My ZBT"},
    ]


@pytest.mark.usefixtures("gateway")
async def test_loaded_entry_profiles_come_from_runtime_data(
    hass, entry_with_room
) -> None:
    await start(hass, entry_with_room)
    assert entry_with_room.state is ConfigEntryState.LOADED

    with patch(
        "custom_components.easy_trv.config_flow.async_load_profiles",
        side_effect=AssertionError("profiles are already loaded"),
    ):
        result = await open_entities_step(hass, entry_with_room)

    assert option_values(fields(result)["profile"]) == [
        "auto",
        "sonoff_trvzb_native",
    ]


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_requires_a_thermostat(hass, entry) -> None:
    result = await open_entities_step(hass, entry)
    result = await room_flows(hass).async_configure(
        result["flow_id"], entities_input(trvs=[])
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "entities"
    assert result["errors"] == {"trvs": "no_trv"}


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_rejects_trv_from_other_room(hass, entry_with_room) -> None:
    result = await open_entities_step(hass, entry_with_room)
    result = await room_flows(hass).async_configure(
        result["flow_id"], entities_input(trvs=["climate.a"], temp_sensors=[])
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "entities"
    assert result["errors"] == {"trvs": "trv_in_other_room"}
    assert len(entry_with_room.subentries) == 1
    # What the user entered stays in the form.
    assert suggested_values(result)["trvs"] == ["climate.a"]


@pytest.mark.usefixtures("area_with_trvs", "foreign_trvs")
async def test_room_rejects_non_zha_and_unmatched_profile(hass, entry) -> None:
    result = await open_entities_step(hass, entry)
    flow_id = result["flow_id"]

    result = await room_flows(hass).async_configure(
        flow_id, entities_input(trvs=["climate.mqtt_trv"])
    )
    assert result["errors"] == {"trvs": "not_zha"}

    result = await room_flows(hass).async_configure(
        flow_id, entities_input(trvs=["climate.does_not_exist"])
    )
    assert result["errors"] == {"trvs": "not_zha"}

    result = await room_flows(hass).async_configure(
        flow_id, entities_input(trvs=["climate.zbt"])
    )
    assert result["errors"] == {"profile": "no_profile"}

    # One thermostat without a profile spoils the whole room.
    result = await room_flows(hass).async_configure(
        flow_id, entities_input(trvs=["climate.a", "climate.zbt"])
    )
    assert result["errors"] == {"profile": "no_profile"}

    result = await room_flows(hass).async_configure(
        flow_id,
        entities_input(trvs=["climate.zbt"], profile="sonoff_trvzb_native"),
    )
    assert result["errors"] == {"profile": "profile_mismatch"}

    assert not entry.subentries
    result = await room_flows(hass).async_configure(
        flow_id, entities_input(trvs=["climate.a", "climate.b"])
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_rejects_a_zha_entity_that_is_not_a_thermostat(hass, entry) -> None:
    # The entity picker only filters in the browser; a posted switch of a ZHA
    # thermostat's device must not pass for a thermostat.
    device = dr.async_get(hass).async_get(
        er.async_get(hass).async_get("climate.a").device_id
    )
    add_entity(hass, "switch.a_child_lock", "zha", device=device)

    result = await open_entities_step(hass, entry)
    flow_id = result["flow_id"]
    result = await room_flows(hass).async_configure(
        flow_id, entities_input(trvs=["switch.a_child_lock"])
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"trvs": "not_zha"}

    result = await room_flows(hass).async_configure(
        flow_id, entities_input(trvs=["climate.a", "switch.a_child_lock"])
    )
    assert result["errors"] == {"trvs": "not_zha"}
    assert not entry.subentries


@pytest.mark.usefixtures("area_with_trvs", "foreign_trvs")
async def test_room_with_a_user_profile_for_a_foreign_model(
    hass, tmp_path, entry
) -> None:
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_zbt", 1)
    text = text.replace("model: TRVZB", "model: TRV-ZBT", 1)
    use_config_dir(hass, tmp_path).joinpath("zbt.yaml").write_text(text)

    result = await open_entities_step(hass, entry)
    result = await room_flows(hass).async_configure(
        result["flow_id"], entities_input(trvs=["climate.zbt"], profile="user_zbt")
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert next(iter(entry.subentries.values())).data["profile"] == "user_zbt"


async def test_room_name_must_be_new_and_not_blank(hass, entry_with_room) -> None:
    result = await open_room_flow(hass, entry_with_room, "custom")
    flow_id = result["flow_id"]

    result = await room_flows(hass).async_configure(flow_id, {"name": "   "})
    assert result["step_id"] == "custom"
    assert result["errors"] == {"name": "name_required"}

    result = await room_flows(hass).async_configure(flow_id, {"name": "LIVING"})
    assert result["step_id"] == "custom"
    assert result["errors"] == {"name": "name_exists"}

    result = await room_flows(hass).async_configure(flow_id, {"name": "Study"})
    assert result["step_id"] == "entities"


async def test_room_from_area_rejects_a_taken_name(
    hass, entry_with_room, area_with_trvs
) -> None:
    result = await open_room_flow(hass, entry_with_room, "from_area")
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"area_id": area_with_trvs.id}
    )
    assert result["step_id"] == "from_area"
    assert result["errors"] == {"area_id": "name_exists"}


async def test_room_from_a_vanished_area_asks_again(hass, entry) -> None:
    result = await open_room_flow(hass, entry, "from_area")
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"area_id": "deleted_meanwhile"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "from_area"
    assert not result["errors"]


# --- Prefill and validation helpers -----------------------------------------


async def test_prefill_walks_devices_and_filters(
    hass, device_owner, area_with_trvs
) -> None:
    other = ar.async_get(hass).async_create("Kitchen")
    multi = dr.async_get(hass).async_get_device_by_identifier(
        ("test", "multi"), device_owner.entry_id
    )
    remote = add_device(
        hass,
        device_owner,
        ("test", "remote"),
        area_id=other.id,
        manufacturer="Acme",
        model="Remote",
    )
    # In the area through the entity itself, without a device.
    add_entity(
        hass,
        "sensor.direct_t",
        "test",
        area_id=area_with_trvs.id,
        device_class="temperature",
    )
    add_entity(
        hass,
        "sensor.direct_h",
        "test",
        area_id=area_with_trvs.id,
        device_class="humidity",
    )
    # The device is in the area but the entity was moved elsewhere.
    add_entity(
        hass,
        "sensor.moved_t",
        "test",
        device=multi,
        area_id=other.id,
        device_class="temperature",
    )
    add_entity(
        hass,
        "sensor.disabled_t",
        "test",
        device=multi,
        device_class="temperature",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    add_entity(hass, "sensor.power", "test", device=multi, device_class="power")
    add_entity(hass, "sensor.no_class", "test", device=multi)
    # A thermostat of another integration and sensors of another area.
    add_entity(hass, "climate.mqtt_in_area", "mqtt", area_id=area_with_trvs.id)
    add_entity(
        hass, "sensor.kitchen_t", "test", device=remote, device_class="temperature"
    )

    assert prefill_from_area(hass, area_with_trvs.id) == {
        "trvs": ["climate.a", "climate.b"],
        "temp_sensors": ["sensor.direct_t", "sensor.t1"],
        "humidity_sensors": ["sensor.direct_h", "sensor.h1"],
    }
    assert prefill_from_area(hass, other.id) == {
        "trvs": [],
        "temp_sensors": ["sensor.kitchen_t", "sensor.moved_t"],
        "humidity_sensors": [],
    }
    assert prefill_from_area(hass, "no_such_area") == {
        "trvs": [],
        "temp_sensors": [],
        "humidity_sensors": [],
    }


@pytest.mark.usefixtures("area_with_trvs")
async def test_validate_room_treats_an_unknown_profile_as_a_mismatch(
    hass, entry
) -> None:
    profiles = (await async_load_profiles(hass)).profiles
    trvs = {"trvs": ["climate.a"]}

    assert validate_room(hass, entry, None, trvs, profiles) == {}
    assert validate_room(hass, entry, None, trvs | {"profile": "auto"}, profiles) == {}
    assert validate_room(hass, entry, None, trvs | {"profile": "nope"}, profiles) == {
        "profile": "profile_mismatch"
    }
    assert validate_room(hass, entry, None, {"trvs": []}, profiles) == {
        "trvs": "no_trv"
    }
    # Without any loaded profile nothing can match.
    assert validate_room(hass, entry, None, trvs, {}) == {"profile": "no_profile"}


# --- Reconfigure ------------------------------------------------------------


async def start_reconfigure(hass, entry, subentry_id):
    return await room_flows(hass).async_init(
        (entry.entry_id, SUBENTRY_ROOM),
        context={"source": "reconfigure", "subentry_id": subentry_id},
    )


@pytest.mark.usefixtures("gateway")
async def test_reconfigure_updates_room(hass, entry_with_room) -> None:
    await start(hass, entry_with_room)
    runtime_data = entry_with_room.runtime_data
    sub = next(iter(entry_with_room.subentries.values()))

    result = await start_reconfigure(hass, entry_with_room, sub.subentry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert list(fields(result)) == [
        "name",
        "trvs",
        "temp_sensors",
        "humidity_sensors",
        "profile",
        "window_detection",
        "hysteresis",
    ]
    # The form shows what is stored, not what the area would suggest.
    assert suggested_values(result) == {
        "name": "Living",
        "trvs": ["climate.a", "climate.b"],
        "temp_sensors": ["sensor.t1", "sensor.t2"],
        "humidity_sensors": [],
        "profile": "auto",
        "window_detection": "on",
        "hysteresis": "0_4",
    }

    result = await room_flows(hass).async_configure(
        result["flow_id"],
        {
            "name": "Living",
            "trvs": ["climate.a", "climate.b"],
            "temp_sensors": ["sensor.t2", "sensor.t1"],
            "humidity_sensors": [],
            "profile": "auto",
            "window_detection": "on",
            "hysteresis": "0_4",
        },
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    await settle(hass)
    updated = entry_with_room.subentries[sub.subentry_id]
    assert updated.data["temp_sensors"] == ["sensor.t2", "sensor.t1"]
    # The update listener reloaded the entry.
    assert entry_with_room.state is ConfigEntryState.LOADED
    assert entry_with_room.runtime_data is not runtime_data


async def test_reconfigure_renames_and_keeps_the_area(hass, area_with_trvs) -> None:
    entry = make_entry(
        hass, [room_data(area_id=area_with_trvs.id, hysteresis="leave")], {}
    )
    sub = next(iter(entry.subentries.values()))

    result = await start_reconfigure(hass, entry, sub.subentry_id)
    result = await room_flows(hass).async_configure(
        result["flow_id"],
        entities_input(name="  Lounge ", trvs=["climate.a"], temp_sensors=[]),
    )
    assert result["reason"] == "reconfigure_successful"

    updated = entry.subentries[sub.subentry_id]
    assert updated.title == "Lounge"
    assert updated.unique_id == "lounge"
    assert dict(updated.data) == {
        "name": "Lounge",
        "area_id": area_with_trvs.id,
        "trvs": ["climate.a"],
        "temp_sensors": [],
        "humidity_sensors": [],
        "profile": "auto",
        "window_detection": "on",
        "hysteresis": "leave",
    }

    # The old name is free again.
    result = await open_room_flow(hass, entry, "custom")
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"name": "Living"}
    )
    assert result["step_id"] == "entities"


@pytest.mark.usefixtures("area_with_trvs", "foreign_trvs")
async def test_reconfigure_rejects_errors_and_keeps_the_room(hass) -> None:
    entry = make_entry(
        hass,
        [room_data(), room_data("Study", trvs=["climate.zbt"], profile="auto")],
        {},
    )
    living, study = entry.subentries.values()
    result = await start_reconfigure(hass, entry, study.subentry_id)
    flow_id = result["flow_id"]

    result = await room_flows(hass).async_configure(
        flow_id, entities_input(name="Study", trvs=["climate.a"])
    )
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"trvs": "trv_in_other_room"}

    result = await room_flows(hass).async_configure(
        flow_id, entities_input(name="living", trvs=["climate.zbt"])
    )
    assert result["errors"] == {"name": "name_exists"}

    result = await room_flows(hass).async_configure(
        flow_id, entities_input(name="  ", trvs=["climate.zbt"])
    )
    assert result["errors"] == {"name": "name_required"}

    result = await room_flows(hass).async_configure(
        flow_id, entities_input(name="Study", trvs=["climate.zbt"])
    )
    assert result["errors"] == {"profile": "no_profile"}

    assert entry.subentries[study.subentry_id].data["trvs"] == ["climate.zbt"]
    assert entry.subentries[living.subentry_id].title == "Living"


# --- Options flow -----------------------------------------------------------


def test_params_read_the_keys_the_options_flow_writes() -> None:
    params = RoomParams.from_settings(
        resolve_settings(
            {
                const.CONF_REFRESH_MINUTES: 30,
                const.CONF_VERIFY_DELAY: 5,
                const.CONF_MAX_ATTEMPTS: 4,
                const.CONF_MIN_DELTA: 0.3,
                const.CONF_SENSOR_TIMEOUT: 45,
            },
            {},
        )
    )
    assert params.refresh == timedelta(minutes=30)
    assert params.verify_delay == 5.0
    assert params.max_attempts == 4
    assert params.min_delta == 0.3
    assert params.sensor_timeout == timedelta(minutes=45)
    assert RoomParams.from_settings(resolve_settings({}, {})) == RoomParams.defaults()


async def test_options_form_defaults_come_from_options_then_constants(
    hass, entry
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert list(fields(result)) == [
        const.CONF_REFRESH_MINUTES,
        const.CONF_VERIFY_DELAY,
        const.CONF_MAX_ATTEMPTS,
        const.CONF_MIN_DELTA,
        const.CONF_SENSOR_TIMEOUT,
    ]
    assert defaults(result) == {
        "refresh_minutes": const.DEFAULT_REFRESH_MINUTES,
        "verify_delay_seconds": const.DEFAULT_VERIFY_DELAY,
        "max_attempts": const.DEFAULT_MAX_ATTEMPTS,
        "min_delta_kelvin": const.DEFAULT_MIN_DELTA,
        "sensor_timeout_minutes": const.DEFAULT_SENSOR_TIMEOUT,
    }
    ranges = {
        name: (selector.config["min"], selector.config["max"], selector.config["step"])
        for name, selector in fields(result).items()
    }
    assert ranges == {
        "refresh_minutes": (5, 90, 1),
        "verify_delay_seconds": (2, 60, 1),
        "max_attempts": (1, 10, 1),
        "min_delta_kelvin": (0.1, 1.0, 0.1),
        "sensor_timeout_minutes": (0, 720, 1),
    }
    assert {s.config["mode"] for s in fields(result).values()} == {"box"}
    assert {
        name: selector.config.get("unit_of_measurement")
        for name, selector in fields(result).items()
    } == {
        "refresh_minutes": "min",
        "verify_delay_seconds": "s",
        "max_attempts": None,
        "min_delta_kelvin": "K",
        "sensor_timeout_minutes": "min",
    }

    entry_with_options = make_entry(hass, [], {"refresh_minutes": 45})
    result = await hass.config_entries.options.async_init(entry_with_options.entry_id)
    assert defaults(result)["refresh_minutes"] == 45
    assert defaults(result)["max_attempts"] == const.DEFAULT_MAX_ATTEMPTS


async def test_options_flow_sets_values(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "refresh_minutes": 30,
            "verify_delay_seconds": 5,
            "max_attempts": 4,
            "min_delta_kelvin": 0.3,
            "sensor_timeout_minutes": 0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.options) == {
        "refresh_minutes": 30,
        "verify_delay_seconds": 5,
        "max_attempts": 4,
        "min_delta_kelvin": 0.3,
        "sensor_timeout_minutes": 0,
    }
    # Whole numbers are stored as integers, not as the selector's floats.
    assert all(
        type(entry.options[key]) is int
        for key in (
            "refresh_minutes",
            "verify_delay_seconds",
            "max_attempts",
            "sensor_timeout_minutes",
        )
    )
    params = RoomParams.from_settings(resolve_settings(entry.options, {}))
    assert params.sensor_timeout is None


@pytest.mark.parametrize(
    ("minutes", "valid"),
    [(0, True), (5, False), (9, False), (10, True), (720, True)],
)
async def test_options_flow_sensor_timeout_is_off_or_ten_to_720(
    hass, entry, minutes, valid
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "refresh_minutes": 20,
            "verify_delay_seconds": 10,
            "max_attempts": 3,
            "min_delta_kelvin": 0.1,
            "sensor_timeout_minutes": minutes,
        },
    )
    if valid:
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options["sensor_timeout_minutes"] == minutes
    else:
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"sensor_timeout_minutes": "timeout_range"}
        assert suggested_values(result)["sensor_timeout_minutes"] == minutes
        assert not entry.options
