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


@pytest.fixture
def area_with_contacts(hass, device_owner, area_with_trvs):
    """The area Living plus window contacts, in it and in the Kitchen.

    Returns the Kitchen, whose contact belongs to the living room's neighbour.
    """
    kitchen = ar.async_get(hass).async_create("Kitchen")
    contact = add_device(
        hass, device_owner, ("test", "contact"), area_id=area_with_trvs.id
    )
    for entity_id, device_class in (
        ("binary_sensor.window", "window"),
        ("binary_sensor.door", "door"),
        ("binary_sensor.gap", "opening"),
        ("binary_sensor.garage", "garage_door"),
        ("binary_sensor.motion", "motion"),
    ):
        add_entity(hass, entity_id, "test", device=contact, device_class=device_class)
    add_entity(
        hass,
        "binary_sensor.off_window",
        "test",
        device=contact,
        device_class="window",
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    # In the area through the entity itself.
    add_entity(
        hass,
        "binary_sensor.direct_window",
        "test",
        area_id=area_with_trvs.id,
        device_class="window",
    )
    # The thermostat's own readings are not window contacts.
    trv = dr.async_get(hass).async_get_device_by_identifier(
        ("zha", IEEE_A), device_owner.entry_id
    )
    add_entity(hass, "binary_sensor.a_open", "zha", device=trv, device_class="window")
    add_entity(
        hass,
        "binary_sensor.kitchen_window",
        "test",
        area_id=kitchen.id,
        device_class="window",
    )
    return kitchen


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


async def open_settings_step(hass, entry, name="Bedroom", **entities):
    """Walk the custom path through the entities form up to the settings form."""
    result = await open_entities_step(hass, entry, name)
    result = await room_flows(hass).async_configure(
        result["flow_id"], entities_input(**{"trvs": ["climate.a"], **entities})
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"
    return result


def entities_input(**overrides: Any) -> dict[str, Any]:
    data = {
        "trvs": ["climate.a", "climate.b"],
        "temp_sensors": ["sensor.t1"],
        "humidity_sensors": [],
    }
    data.update(overrides)
    return data


# What a room stores when nothing is overridden: the selects say "global".
ALL_GLOBAL = {
    "profile": "global",
    "window_detection": "global",
    "hysteresis": "global",
    "adaptive_mode": "global",
    "outdoor_cutoff": "global",
}


def fields(result) -> dict[str, Any]:
    """Map the field names of a form to their selectors."""
    return {str(key): value for key, value in result["data_schema"].schema.items()}


def section_fields(result, name) -> dict[str, Any]:
    """Map the field names inside the section ``name`` to their selectors."""
    group = fields(result)[name]
    return {str(key): value for key, value in group.schema.schema.items()}


def _schema(result, group):
    schema = result["data_schema"].schema
    return schema if group is None else schema[group].schema.schema


def suggested_values(result, group=None) -> dict[str, Any]:
    return {
        str(key): key.description["suggested_value"]
        for key in _schema(result, group)
        if isinstance(key, vol.Marker)
        and key.description
        and "suggested_value" in key.description
    }


def defaults(result, group=None) -> dict[str, Any]:
    return {
        str(key): key.default()
        for key in _schema(result, group)
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
        "window_contacts": [],
    }

    result = await room_flows(hass).async_configure(
        result["flow_id"],
        {
            "trvs": ["climate.a", "climate.b"],
            "temp_sensors": ["sensor.t1"],
            "humidity_sensors": [],
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"
    assert not entry.subentries

    result = await room_flows(hass).async_configure(
        result["flow_id"], {"hysteresis": "0_4"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living"

    sub = next(iter(entry.subentries.values()))
    assert sub.subentry_type == SUBENTRY_ROOM
    assert sub.title == "Living"
    assert sub.unique_id == "living"
    assert dict(sub.data) == {
        "name": "Living",
        "area_id": area_with_trvs.id,
        "trvs": ["climate.a", "climate.b"],
        "temp_sensors": ["sensor.t1"],
        "humidity_sensors": [],
        "window_contacts": [],
        **ALL_GLOBAL,
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
    assert result["step_id"] == "settings"
    result = await room_flows(hass).async_configure(result["flow_id"], {})
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
        "window_contacts": [],
        **ALL_GLOBAL,
    }


@pytest.mark.usefixtures("area_with_trvs")
async def test_entities_form_fields_and_defaults(hass, entry) -> None:
    result = await open_entities_step(hass, entry)

    selectors = fields(result)
    assert list(selectors) == [
        "trvs",
        "temp_sensors",
        "humidity_sensors",
        "window_contacts",
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
    assert defaults(result) == {
        "humidity_sensors": [],
        "temp_sensors": [],
        "window_contacts": [],
    }


@pytest.mark.usefixtures("area_with_trvs")
async def test_profile_options_list_built_in_and_user_profiles(
    hass, tmp_path, entry
) -> None:
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_zbt", 1)
    text = text.replace("name: Sonoff TRVZB (native)", "name: My ZBT", 1)
    use_config_dir(hass, tmp_path).joinpath("zbt.yaml").write_text(text)

    result = await open_settings_step(hass, entry)

    profile = fields(result)["profile"]
    assert profile.config["options"] == [
        {"value": "global", "label": "Global"},
        {"value": "auto", "label": "Auto"},
        {"value": "sonoff_trvzb_native", "label": "Sonoff TRVZB (native)"},
        {"value": "user_zbt", "label": "My ZBT"},
    ]
    assert profile.config["translation_key"] == "profile"


@pytest.mark.usefixtures("gateway")
async def test_loaded_entry_profiles_come_from_runtime_data(
    hass, device_owner, entry_with_room
) -> None:
    free = add_device(hass, device_owner, ("zha", "00:44"))
    add_entity(hass, "climate.c", "zha", device=free)
    await start(hass, entry_with_room)
    assert entry_with_room.state is ConfigEntryState.LOADED

    with patch(
        "custom_components.easy_trv.config_flow.async_load_profiles",
        side_effect=AssertionError("profiles are already loaded"),
    ):
        result = await open_settings_step(hass, entry_with_room, trvs=["climate.c"])

    assert option_values(fields(result)["profile"]) == [
        "global",
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
async def test_room_rejects_non_zha_and_thermostats_without_a_profile(
    hass, entry
) -> None:
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

    assert not entry.subentries
    result = await room_flows(hass).async_configure(
        flow_id, entities_input(trvs=["climate.a", "climate.b"])
    )
    assert result["step_id"] == "settings"


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_reject_a_profile_that_does_not_match(
    hass, tmp_path, entry
) -> None:
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_zbt", 1)
    text = text.replace("model: TRVZB", "model: TRV-ZBT", 1)
    use_config_dir(hass, tmp_path).joinpath("zbt.yaml").write_text(text)

    result = await open_settings_step(hass, entry)
    flow_id = result["flow_id"]
    result = await room_flows(hass).async_configure(flow_id, {"profile": "user_zbt"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"
    assert result["errors"] == {"profile": "profile_mismatch"}
    # What the user entered stays in the form.
    assert suggested_values(result)["profile"] == "user_zbt"
    assert not entry.subentries

    result = await room_flows(hass).async_configure(
        flow_id, {"profile": "sonoff_trvzb_native"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert next(iter(entry.subentries.values())).data["profile"] == (
        "sonoff_trvzb_native"
    )


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

    result = await open_settings_step(hass, entry, trvs=["climate.zbt"])
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"profile": "user_zbt"}
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
        "window_contacts": [],
    }
    assert prefill_from_area(hass, other.id) == {
        "trvs": [],
        "temp_sensors": ["sensor.kitchen_t", "sensor.moved_t"],
        "humidity_sensors": [],
        "window_contacts": [],
    }
    assert prefill_from_area(hass, "no_such_area") == {
        "trvs": [],
        "temp_sensors": [],
        "humidity_sensors": [],
        "window_contacts": [],
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


# --- Room flow: window contacts -----------------------------------------------


@pytest.mark.usefixtures("area_with_trvs")
async def test_entities_form_offers_window_contacts_of_any_area(hass, entry) -> None:
    contacts = fields(await open_entities_step(hass, entry))["window_contacts"]

    assert contacts.config["multiple"] is True
    assert contacts.config["domain"] == ["binary_sensor"]
    assert contacts.config["device_class"] == [
        "window",
        "door",
        "opening",
        "garage_door",
    ]
    # No area filter: a window of the neighbouring room can be a contact.
    assert "area" not in contacts.config
    assert "filter" not in contacts.config


@pytest.mark.usefixtures("area_with_contacts")
async def test_prefill_suggests_the_contacts_of_the_area(hass, area_with_trvs) -> None:
    assert prefill_from_area(hass, area_with_trvs.id)["window_contacts"] == [
        "binary_sensor.direct_window",
        "binary_sensor.door",
        "binary_sensor.gap",
        "binary_sensor.garage",
        "binary_sensor.window",
    ]


@pytest.mark.usefixtures("area_with_contacts")
async def test_room_from_area_prefills_and_stores_window_contacts(
    hass, entry, area_with_trvs
) -> None:
    result = await open_room_flow(hass, entry, "from_area")
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"area_id": area_with_trvs.id}
    )
    assert suggested_values(result)["window_contacts"] == [
        "binary_sensor.direct_window",
        "binary_sensor.door",
        "binary_sensor.gap",
        "binary_sensor.garage",
        "binary_sensor.window",
    ]

    # The user drops one and adds the kitchen window (another area).
    contacts = ["binary_sensor.window", "binary_sensor.kitchen_window"]
    result = await room_flows(hass).async_configure(
        result["flow_id"], entities_input(window_contacts=contacts)
    )
    assert result["step_id"] == "settings"
    result = await room_flows(hass).async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(entry.subentries.values()))
    assert sub.data["window_contacts"] == contacts


@pytest.mark.usefixtures("area_with_contacts")
async def test_custom_room_stores_contacts_from_any_area(hass, entry) -> None:
    result = await open_settings_step(
        hass, entry, window_contacts=["binary_sensor.kitchen_window"]
    )
    result = await room_flows(hass).async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(iter(entry.subentries.values()))
    assert sub.data["window_contacts"] == ["binary_sensor.kitchen_window"]


@pytest.mark.usefixtures("area_with_trvs")
async def test_reconfigure_prefills_and_replaces_the_window_contacts(hass) -> None:
    entry = make_entry(
        hass,
        [room_data(window_contacts=["binary_sensor.window", "binary_sensor.door"])],
        {},
    )
    sub = next(iter(entry.subentries.values()))

    result = await start_reconfigure(hass, entry, sub.subentry_id)
    assert suggested_values(result)["window_contacts"] == [
        "binary_sensor.window",
        "binary_sensor.door",
    ]
    # Emptying the list removes the contacts.
    result = await room_flows(hass).async_configure(
        result["flow_id"], entities_input(name="Living", window_contacts=[])
    )
    result = await room_flows(hass).async_configure(result["flow_id"], {})
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[sub.subentry_id].data["window_contacts"] == []


# --- Room flow: settings step ------------------------------------------------

SETTINGS_FIELDS = [
    "profile",
    "window_detection",
    "hysteresis",
    "adaptive_mode",
    "frost_temperature",
    "refresh_minutes",
    "verify_delay_seconds",
    "max_attempts",
    "min_delta_kelvin",
    "sensor_timeout_minutes",
    "setpoint_min",
    "setpoint_max",
    "window_open_delay_seconds",
    "window_warn_minutes",
    "window_check_minutes",
    "window_check_drop_kelvin",
    "outdoor_cutoff",
    "outdoor_cutoff_celsius",
]
SETTING_NUMBERS = {
    "frost_temperature": (4.0, 16.0, 0.5, "°C"),
    "refresh_minutes": (1, 120, 1, "min"),
    "verify_delay_seconds": (2, 60, 1, "s"),
    "max_attempts": (1, 10, 1, None),
    "min_delta_kelvin": (0.0, 1.0, 0.1, "K"),
    "sensor_timeout_minutes": (0, 1440, 1, "min"),
    "setpoint_min": (4.0, 35.0, 0.5, "°C"),
    "setpoint_max": (4.0, 35.0, 0.5, "°C"),
    "window_open_delay_seconds": (0, 600, 1, "s"),
    "window_warn_minutes": (0, 1440, 1, "min"),
    "window_check_minutes": (0, 240, 1, "min"),
    "window_check_drop_kelvin": (0.1, 3.0, 0.1, "K"),
    "outdoor_cutoff_celsius": (-10.0, 30.0, 0.5, "°C"),
}


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_flow_has_settings_step_with_global_defaults(hass, entry) -> None:
    result = await open_settings_step(hass, entry)

    assert list(fields(result)) == SETTINGS_FIELDS
    # Every select starts at "global"; the numbers start empty (= global).
    assert defaults(result) == ALL_GLOBAL
    assert suggested_values(result) == {}
    # Selects read as the labels of their selectors, numbers carry their unit.
    assert result["description_placeholders"] == {
        "global_profile": "Auto",
        "global_window_detection": "Detection on",
        "global_hysteresis": "0.4 K",
        "global_adaptive_mode": "Adaptive on",
        "global_frost_temperature": "5.0 °C",
        "global_refresh_minutes": "20 min",
        "global_verify_delay_seconds": "10 s",
        "global_max_attempts": "3",
        "global_min_delta_kelvin": "0.1 K",
        "global_sensor_timeout_minutes": "120 min",
        "global_setpoint_min": "5.0 °C",
        "global_setpoint_max": "30.0 °C",
        "global_window_open_delay_seconds": "30 s",
        "global_window_warn_minutes": "120 min",
        "global_window_check_minutes": "30 min",
        "global_window_check_drop_kelvin": "0.3 K",
        "global_outdoor_cutoff_celsius": "Off",
    }


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_placeholders_follow_the_global_options(hass) -> None:
    entry = make_entry(
        hass,
        [],
        {
            "hysteresis": "0_6",
            "adaptive_mode": "off",
            "frost_temperature": 9.5,
            "refresh_minutes": 45,
            "sensor_timeout_minutes": 0,
        },
    )
    result = await open_settings_step(hass, entry)

    placeholders = result["description_placeholders"]
    assert placeholders["global_hysteresis"] == "0.6 K"
    assert placeholders["global_adaptive_mode"] == "Adaptive off"
    assert placeholders["global_frost_temperature"] == "9.5 °C"
    assert placeholders["global_refresh_minutes"] == "45 min"
    # A timeout of 0 means "off".
    assert placeholders["global_sensor_timeout_minutes"] == "Off"
    assert placeholders["global_max_attempts"] == "3"


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_placeholders_are_german_in_a_german_instance(
    hass,
) -> None:
    hass.config.language = "de"
    entry = make_entry(hass, [], {"sensor_timeout_minutes": 0})
    result = await open_settings_step(hass, entry)

    placeholders = result["description_placeholders"]
    assert placeholders["global_hysteresis"] == "0,4 K"
    assert placeholders["global_window_detection"] == "Erkennung an"
    assert placeholders["global_adaptive_mode"] == "Adaptiv an"
    assert placeholders["global_profile"] == "Auto"
    assert placeholders["global_frost_temperature"] == "5,0 °C"
    assert placeholders["global_min_delta_kelvin"] == "0,1 K"
    assert placeholders["global_refresh_minutes"] == "20 min"
    assert placeholders["global_sensor_timeout_minutes"] == "Aus"


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_selects_start_with_global(hass, entry) -> None:
    selectors = fields(await open_settings_step(hass, entry))

    assert option_values(selectors["window_detection"]) == [
        "global",
        "on",
        "off",
        "leave",
    ]
    assert option_values(selectors["adaptive_mode"]) == [
        "global",
        "on",
        "off",
        "leave",
    ]
    for key in ("profile", "window_detection", "hysteresis", "adaptive_mode"):
        assert selectors[key].config["translation_key"] == key


@pytest.mark.usefixtures("area_with_trvs")
async def test_hysteresis_select_has_no_leave_option(hass, entry) -> None:
    room = fields(await open_settings_step(hass, entry))["hysteresis"]
    assert option_values(room) == ["global", "0_2", "0_4", "0_6", "0_8", "1_0"]

    options = await hass.config_entries.options.async_init(entry.entry_id)
    thermostat = section_fields(options, "thermostat")["hysteresis"]
    assert option_values(thermostat) == ["0_2", "0_4", "0_6", "0_8", "1_0"]


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_numbers_are_optional_boxes(hass, entry) -> None:
    selectors = fields(await open_settings_step(hass, entry))

    for key, (minimum, maximum, step, unit) in SETTING_NUMBERS.items():
        config = selectors[key].config
        assert (config["min"], config["max"], config["step"]) == (
            minimum,
            maximum,
            step,
        ), key
        assert config["mode"] == "box"
        assert config.get("unit_of_measurement") == unit
    # No default: an empty field means "global".
    result = await open_settings_step(hass, entry, name="Study")
    assert not set(defaults(result)) & set(SETTING_NUMBERS)


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_store_only_overrides(hass, entry) -> None:
    result = await open_settings_step(hass, entry)
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"hysteresis": "0_2"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    data = next(iter(entry.subentries.values())).data
    assert data["hysteresis"] == "0_2"
    assert data["adaptive_mode"] == "global"
    assert data["window_detection"] == "global"
    assert data["profile"] == "global"
    for key in SETTING_NUMBERS:
        assert key not in data


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_store_numbers_with_their_types(hass, entry) -> None:
    result = await open_settings_step(hass, entry)
    result = await room_flows(hass).async_configure(
        result["flow_id"],
        {
            "window_detection": "off",
            "adaptive_mode": "leave",
            "frost_temperature": 9.5,
            "refresh_minutes": 30.0,
            "verify_delay_seconds": 5.0,
            "max_attempts": 4.0,
            "min_delta_kelvin": 0.3,
            "sensor_timeout_minutes": 0.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    data = next(iter(entry.subentries.values())).data
    assert data["window_detection"] == "off"
    assert data["adaptive_mode"] == "leave"
    assert data["frost_temperature"] == 9.5
    assert type(data["frost_temperature"]) is float
    assert type(data["min_delta_kelvin"]) is float
    assert data["min_delta_kelvin"] == 0.3
    # Whole numbers are stored as integers, not as the selector's floats.
    for key, value in (
        ("refresh_minutes", 30),
        ("verify_delay_seconds", 5),
        ("max_attempts", 4),
        ("sensor_timeout_minutes", 0),
    ):
        assert data[key] == value
        assert type(data[key]) is int
    # The overrides resolve on top of the global layer.
    settings = resolve_settings({}, data)
    assert (settings.window_detection, settings.refresh_minutes) == ("off", 30)


@pytest.mark.usefixtures("area_with_trvs")
@pytest.mark.parametrize(
    ("minutes", "valid"), [(0, True), (5, False), (10, True), (1440, True)]
)
async def test_room_sensor_timeout_is_off_or_ten_to_1440(
    hass, entry, minutes, valid
) -> None:
    result = await open_settings_step(hass, entry)
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"sensor_timeout_minutes": minutes}
    )
    if valid:
        assert result["type"] is FlowResultType.CREATE_ENTRY
    else:
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"sensor_timeout_minutes": "timeout_range"}
        assert suggested_values(result)["sensor_timeout_minutes"] == minutes
        assert not entry.subentries


@pytest.mark.usefixtures("area_with_trvs")
async def test_outdoor_cutoff_select_is_global_or_off(hass, entry) -> None:
    result = await open_settings_step(hass, entry)
    cutoff = fields(result)["outdoor_cutoff"]

    assert option_values(cutoff) == ["global", "off"]
    assert cutoff.config["translation_key"] == "outdoor_cutoff"
    assert defaults(result)["outdoor_cutoff"] == "global"


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_placeholders_show_the_global_window_values(hass) -> None:
    entry = make_entry(
        hass,
        [],
        {
            "setpoint_min": 6.0,
            "setpoint_max": 24.5,
            "window_open_delay_seconds": 0,
            "window_warn_minutes": 0,
            "window_check_minutes": 45,
            "window_check_drop_kelvin": 0.5,
            "outdoor_cutoff_celsius": 18.0,
        },
    )
    placeholders = (await open_settings_step(hass, entry))["description_placeholders"]

    assert placeholders["global_setpoint_min"] == "6.0 °C"
    assert placeholders["global_setpoint_max"] == "24.5 °C"
    assert placeholders["global_window_open_delay_seconds"] == "0 s"
    assert placeholders["global_window_warn_minutes"] == "Off"
    assert placeholders["global_window_check_minutes"] == "45 min"
    assert placeholders["global_window_check_drop_kelvin"] == "0.5 K"
    assert placeholders["global_outdoor_cutoff_celsius"] == "18.0 °C"


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_cutoff_placeholder_is_german(hass, entry) -> None:
    hass.config.language = "de"
    placeholders = (await open_settings_step(hass, entry))["description_placeholders"]
    assert placeholders["global_outdoor_cutoff_celsius"] == "Aus"
    assert placeholders["global_window_check_drop_kelvin"] == "0,3 K"


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_store_the_window_and_outdoor_values(hass, entry) -> None:
    result = await open_settings_step(hass, entry)
    result = await room_flows(hass).async_configure(
        result["flow_id"],
        {
            "setpoint_min": 16.0,
            "setpoint_max": 24.0,
            "window_open_delay_seconds": 45.0,
            "window_warn_minutes": 60.0,
            "window_check_minutes": 0.0,
            "window_check_drop_kelvin": 0.5,
            "outdoor_cutoff_celsius": 0.0,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    data = next(iter(entry.subentries.values())).data
    assert data["outdoor_cutoff"] == "global"
    for key, value in (
        ("setpoint_min", 16.0),
        ("setpoint_max", 24.0),
        ("window_check_drop_kelvin", 0.5),
        ("outdoor_cutoff_celsius", 0.0),
    ):
        assert data[key] == value
        assert type(data[key]) is float
    for key, value in (
        ("window_open_delay_seconds", 45),
        ("window_warn_minutes", 60),
        ("window_check_minutes", 0),
    ):
        assert data[key] == value
        assert type(data[key]) is int
    settings = resolve_settings({}, data)
    assert (settings.setpoint_min, settings.outdoor_cutoff_celsius) == (16.0, 0.0)


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_settings_store_nothing_for_empty_new_numbers(hass, entry) -> None:
    result = await open_settings_step(hass, entry)
    result = await room_flows(hass).async_configure(result["flow_id"], {})
    data = next(iter(entry.subentries.values())).data
    for key in (
        "setpoint_min",
        "setpoint_max",
        "window_open_delay_seconds",
        "window_warn_minutes",
        "window_check_minutes",
        "window_check_drop_kelvin",
        "outdoor_cutoff_celsius",
    ):
        assert key not in data


@pytest.mark.usefixtures("area_with_trvs")
async def test_room_can_switch_the_outdoor_cutoff_off(hass) -> None:
    entry = make_entry(hass, [], {"outdoor_cutoff_celsius": 18.0})
    result = await open_settings_step(hass, entry)
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"outdoor_cutoff": "off"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    data = next(iter(entry.subentries.values())).data
    assert data["outdoor_cutoff"] == "off"
    assert resolve_settings(entry.options, data).outdoor_cutoff_celsius is None
    assert resolve_settings(entry.options, {}).outdoor_cutoff_celsius == 18.0


@pytest.mark.usefixtures("area_with_trvs")
@pytest.mark.parametrize(
    ("options", "submitted", "valid"),
    [
        ({}, {"setpoint_min": 10.0, "setpoint_max": 20.0}, True),
        # The defaults are 5.0 and 30.0: a lone value is checked against them.
        ({}, {"setpoint_min": 30.0}, False),
        ({}, {"setpoint_min": 29.5}, True),
        ({}, {"setpoint_max": 5.0}, False),
        ({}, {"setpoint_max": 5.5}, True),
        ({}, {"setpoint_min": 20.0, "setpoint_max": 20.0}, False),
        ({}, {"setpoint_min": 22.0, "setpoint_max": 18.0}, False),
        # ... or against the global options.
        ({"setpoint_max": 20.0}, {"setpoint_min": 20.0}, False),
        ({"setpoint_max": 20.0}, {"setpoint_min": 19.5}, True),
        ({"setpoint_min": 12.0}, {"setpoint_max": 12.0}, False),
        ({"setpoint_min": 12.0}, {"setpoint_max": 12.5}, True),
        # Nothing set in the room: the global pair was valid when it was saved.
        ({"setpoint_min": 8.0, "setpoint_max": 12.0}, {}, True),
    ],
)
async def test_room_settings_reject_a_setpoint_range_that_is_not_a_range(
    hass, options, submitted, valid
) -> None:
    entry = make_entry(hass, [], options)
    result = await open_settings_step(hass, entry)
    result = await room_flows(hass).async_configure(result["flow_id"], submitted)

    if valid:
        assert result["type"] is FlowResultType.CREATE_ENTRY
    else:
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "settings"
        assert result["errors"] == {"base": "setpoint_range"}
        # The form keeps what was typed.
        shown = suggested_values(result)
        assert {key: shown[key] for key in submitted} == submitted
        assert not entry.subentries


@pytest.mark.usefixtures("area_with_trvs")
async def test_reconfigure_settings_reject_a_setpoint_range_that_is_not_a_range(
    hass,
) -> None:
    entry = make_entry(hass, [room_data(setpoint_min=10.0, setpoint_max=20.0)], {})
    sub = next(iter(entry.subentries.values()))
    result = await start_reconfigure(hass, entry, sub.subentry_id)
    result = await room_flows(hass).async_configure(
        result["flow_id"], entities_input(name="Living")
    )
    assert suggested_values(result)["setpoint_min"] == 10.0

    result = await room_flows(hass).async_configure(
        result["flow_id"], {"setpoint_min": 25.0, "setpoint_max": 20.0}
    )
    assert result["errors"] == {"base": "setpoint_range"}
    assert entry.subentries[sub.subentry_id].data["setpoint_min"] == 10.0

    result = await room_flows(hass).async_configure(
        result["flow_id"], {"setpoint_min": 12.0, "setpoint_max": 20.0}
    )
    assert result["reason"] == "reconfigure_successful"
    assert entry.subentries[sub.subentry_id].data["setpoint_min"] == 12.0


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
        "window_contacts",
    ]
    # The form shows what is stored, not what the area would suggest.
    assert suggested_values(result) == {
        "name": "Living",
        "trvs": ["climate.a", "climate.b"],
        "temp_sensors": ["sensor.t1", "sensor.t2"],
        "humidity_sensors": [],
    }

    result = await room_flows(hass).async_configure(
        result["flow_id"],
        {
            "name": "Living",
            "trvs": ["climate.a", "climate.b"],
            "temp_sensors": ["sensor.t2", "sensor.t1"],
            "humidity_sensors": [],
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "settings"
    # Nothing is stored before the settings are confirmed too.
    assert entry_with_room.subentries[sub.subentry_id].data["temp_sensors"] == [
        "sensor.t1",
        "sensor.t2",
    ]

    result = await room_flows(hass).async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    await settle(hass)
    updated = entry_with_room.subentries[sub.subentry_id]
    assert updated.data["temp_sensors"] == ["sensor.t2", "sensor.t1"]
    # The update listener reloaded the entry.
    assert entry_with_room.state is ConfigEntryState.LOADED
    assert entry_with_room.runtime_data is not runtime_data


@pytest.mark.usefixtures("area_with_trvs")
async def test_reconfigure_shows_current_overrides(hass) -> None:
    entry = make_entry(
        hass,
        [
            room_data(
                hysteresis="0_8",
                window_detection="global",
                refresh_minutes=45,
                frost_temperature=9.5,
            )
        ],
        {},
    )
    sub = next(iter(entry.subentries.values()))

    result = await start_reconfigure(hass, entry, sub.subentry_id)
    result = await room_flows(hass).async_configure(
        result["flow_id"], entities_input(name="Living")
    )
    assert result["step_id"] == "settings"
    assert list(fields(result)) == SETTINGS_FIELDS
    assert suggested_values(result) == {
        "profile": "auto",
        "window_detection": "global",
        "hysteresis": "0_8",
        "frost_temperature": 9.5,
        "refresh_minutes": 45,
    }
    # A key the room never stored falls back to "global" in the form.
    assert defaults(result)["adaptive_mode"] == "global"
    assert result["description_placeholders"]["global_hysteresis"] == "0.4 K"


@pytest.mark.usefixtures("area_with_trvs")
async def test_reconfigure_replaces_the_stored_settings(hass) -> None:
    entry = make_entry(
        hass,
        [room_data(refresh_minutes=45, frost_temperature=9.5, hysteresis="0_8")],
        {},
    )
    sub = next(iter(entry.subentries.values()))

    result = await start_reconfigure(hass, entry, sub.subentry_id)
    result = await room_flows(hass).async_configure(
        result["flow_id"], entities_input(name="Living")
    )
    # The user empties the refresh field and keeps the frost temperature.
    result = await room_flows(hass).async_configure(
        result["flow_id"],
        {"hysteresis": "global", "frost_temperature": 9.5, "adaptive_mode": "off"},
    )
    assert result["reason"] == "reconfigure_successful"

    data = dict(entry.subentries[sub.subentry_id].data)
    assert "refresh_minutes" not in data
    assert data["frost_temperature"] == 9.5
    assert data["hysteresis"] == "global"
    assert data["adaptive_mode"] == "off"


async def test_reconfigure_renames_and_keeps_the_area(hass, area_with_trvs) -> None:
    entry = make_entry(hass, [room_data(area_id=area_with_trvs.id)], {})
    sub = next(iter(entry.subentries.values()))

    result = await start_reconfigure(hass, entry, sub.subentry_id)
    result = await room_flows(hass).async_configure(
        result["flow_id"],
        entities_input(name="  Lounge ", trvs=["climate.a"], temp_sensors=[]),
    )
    assert result["step_id"] == "settings"
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"window_detection": "leave"}
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
        "window_contacts": [],
        **ALL_GLOBAL,
        "window_detection": "leave",
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


@pytest.mark.usefixtures("area_with_trvs")
async def test_reconfigure_settings_reject_a_profile_that_does_not_match(
    hass, tmp_path
) -> None:
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_zbt", 1)
    text = text.replace("model: TRVZB", "model: TRV-ZBT", 1)
    use_config_dir(hass, tmp_path).joinpath("zbt.yaml").write_text(text)
    entry = make_entry(hass, [room_data()], {})
    sub = next(iter(entry.subentries.values()))

    result = await start_reconfigure(hass, entry, sub.subentry_id)
    result = await room_flows(hass).async_configure(
        result["flow_id"], entities_input(name="Living")
    )
    result = await room_flows(hass).async_configure(
        result["flow_id"], {"profile": "user_zbt"}
    )
    assert result["step_id"] == "settings"
    assert result["errors"] == {"profile": "profile_mismatch"}
    assert entry.subentries[sub.subentry_id].data["profile"] == "auto"


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


WRITES = ["refresh_minutes", "verify_delay_seconds", "max_attempts", "min_delta_kelvin"]


def options_input(
    writes=None, thermostat=None, room_thermostat=None, windows_outdoor=None
) -> dict[str, Any]:
    """Return what the options form submits: four sections."""
    return {
        "writes": {
            "refresh_minutes": 20,
            "verify_delay_seconds": 10,
            "max_attempts": 3,
            "min_delta_kelvin": 0.1,
            "sensor_timeout_minutes": 120,
            **(writes or {}),
        },
        "thermostat": {
            "window_detection": "on",
            "hysteresis": "0_4",
            "adaptive_mode": "on",
            "frost_temperature": 5.0,
            **(thermostat or {}),
        },
        "room_thermostat": dict(room_thermostat or {}),
        "windows_outdoor": dict(windows_outdoor or {}),
    }


async def test_options_form_is_grouped_in_four_sections(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert list(fields(result)) == [
        "writes",
        "thermostat",
        "room_thermostat",
        "windows_outdoor",
    ]
    for group in fields(result).values():
        assert group.options == {"collapsed": False}
    assert list(section_fields(result, "writes")) == [
        *WRITES,
        "sensor_timeout_minutes",
    ]
    assert list(section_fields(result, "thermostat")) == [
        "window_detection",
        "hysteresis",
        "adaptive_mode",
        "frost_temperature",
    ]
    assert list(section_fields(result, "room_thermostat")) == [
        "setpoint_min",
        "setpoint_max",
    ]
    assert list(section_fields(result, "windows_outdoor")) == [
        "window_open_delay_seconds",
        "window_warn_minutes",
        "window_check_minutes",
        "window_check_drop_kelvin",
        "outdoor_sensor",
        "outdoor_cutoff_celsius",
    ]


async def test_options_form_suggests_only_saved_numbers(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    # Nothing saved: the numbers stay empty, the selects start at the defaults.
    assert suggested_values(result, "writes") == {}
    assert suggested_values(result, "thermostat") == {}
    assert defaults(result, "thermostat") == {
        "window_detection": const.DEFAULT_WINDOW_DETECTION,
        "hysteresis": const.DEFAULT_HYSTERESIS,
        "adaptive_mode": const.DEFAULT_ADAPTIVE_MODE,
    }
    # Each number says what empty means.
    assert result["description_placeholders"] == {
        "default_refresh_minutes": "20 min",
        "default_verify_delay_seconds": "10 s",
        "default_max_attempts": "3",
        "default_min_delta_kelvin": "0.1 K",
        "default_sensor_timeout_minutes": "120 min",
        "default_frost_temperature": "5.0 °C",
        "default_setpoint_min": "5.0 °C",
        "default_setpoint_max": "30.0 °C",
        "default_window_open_delay_seconds": "30 s",
        "default_window_warn_minutes": "120 min",
        "default_window_check_minutes": "30 min",
        "default_window_check_drop_kelvin": "0.3 K",
        "default_outdoor_cutoff_celsius": "Off",
    }

    saved = make_entry(
        hass,
        [],
        {"refresh_minutes": 45, "hysteresis": "0_8", "frost_temperature": 9.0},
    )
    result = await hass.config_entries.options.async_init(saved.entry_id)
    assert suggested_values(result, "writes") == {"refresh_minutes": 45}
    assert defaults(result, "thermostat")["hysteresis"] == "0_8"
    assert suggested_values(result, "thermostat") == {"frost_temperature": 9.0}


async def test_options_empty_numbers_stay_empty_across_saves(hass, entry) -> None:
    submitted = options_input()
    for key in WRITES:
        del submitted["writes"][key]
    del submitted["writes"]["sensor_timeout_minutes"]
    del submitted["thermostat"]["frost_temperature"]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert set(entry.options) == {"window_detection", "hysteresis", "adaptive_mode"}

    # Opening and saving the form again does not turn the defaults into options.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert suggested_values(result, "writes") == {}
    assert suggested_values(result, "thermostat") == {}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    assert set(entry.options) == {"window_detection", "hysteresis", "adaptive_mode"}


async def test_options_form_ranges_units_and_select_options(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    numbers = {
        **section_fields(result, "writes"),
        "frost_temperature": section_fields(result, "thermostat")["frost_temperature"],
    }
    assert {
        name: (
            selector.config["min"],
            selector.config["max"],
            selector.config["step"],
            selector.config.get("unit_of_measurement"),
        )
        for name, selector in numbers.items()
    } == {key: SETTING_NUMBERS[key] for key in numbers}
    assert {s.config["mode"] for s in numbers.values()} == {"box"}

    thermostat = section_fields(result, "thermostat")
    assert option_values(thermostat["window_detection"]) == ["on", "off", "leave"]
    assert option_values(thermostat["adaptive_mode"]) == ["on", "off", "leave"]
    for key in ("window_detection", "hysteresis", "adaptive_mode"):
        assert thermostat[key].config["translation_key"] == key


async def test_options_flow_stores_thermostat_settings_flat(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        options_input(
            writes={
                "refresh_minutes": 30,
                "verify_delay_seconds": 5,
                "max_attempts": 4,
                "min_delta_kelvin": 0.3,
                "sensor_timeout_minutes": 0,
            },
            thermostat={
                "window_detection": "off",
                "hysteresis": "0_6",
                "adaptive_mode": "off",
                "frost_temperature": 8.5,
            },
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Flat, as in Phase 1: the sections are only a layout.
    assert dict(entry.options) == {
        "refresh_minutes": 30,
        "verify_delay_seconds": 5,
        "max_attempts": 4,
        "min_delta_kelvin": 0.3,
        "sensor_timeout_minutes": 0,
        "window_detection": "off",
        "hysteresis": "0_6",
        "adaptive_mode": "off",
        "frost_temperature": 8.5,
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
    assert type(entry.options["frost_temperature"]) is float
    settings = resolve_settings(entry.options, {})
    assert (settings.hysteresis, settings.adaptive_mode) == (0.6, "off")
    assert RoomParams.from_settings(settings).sensor_timeout is None


async def test_options_empty_number_falls_back_to_default(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    submitted = options_input()
    del submitted["writes"]["refresh_minutes"]
    del submitted["thermostat"]["frost_temperature"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "refresh_minutes" not in entry.options
    assert "frost_temperature" not in entry.options
    assert entry.options["max_attempts"] == 3
    settings = resolve_settings(entry.options, {})
    assert settings.refresh_minutes == const.DEFAULT_REFRESH_MINUTES
    assert settings.frost_temperature == const.DEFAULT_FROST_TEMPERATURE


async def test_options_flow_edits_the_saved_options(hass) -> None:
    saved = make_entry(hass, [], {"refresh_minutes": 45, "adaptive_mode": "leave"})
    result = await hass.config_entries.options.async_init(saved.entry_id)
    # Submitting what the form shows keeps the saved values.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        options_input(
            writes={"refresh_minutes": 45}, thermostat={"adaptive_mode": "leave"}
        ),
    )
    assert saved.options["refresh_minutes"] == 45
    assert saved.options["adaptive_mode"] == "leave"


@pytest.mark.parametrize(
    ("minutes", "valid"),
    [(0, True), (5, False), (9, False), (10, True), (1440, True)],
)
async def test_options_flow_sensor_timeout_is_off_or_ten_to_1440(
    hass, entry, minutes, valid
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options_input(writes={"sensor_timeout_minutes": minutes})
    )
    if valid:
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert entry.options["sensor_timeout_minutes"] == minutes
    else:
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"sensor_timeout_minutes": "timeout_range"}
        assert suggested_values(result, "writes")["sensor_timeout_minutes"] == minutes
        assert not entry.options


async def test_options_new_numbers_have_ranges_and_units(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    numbers = {
        **section_fields(result, "room_thermostat"),
        **section_fields(result, "windows_outdoor"),
    }
    outdoor = numbers.pop("outdoor_sensor")
    assert {
        name: (
            selector.config["min"],
            selector.config["max"],
            selector.config["step"],
            selector.config.get("unit_of_measurement"),
        )
        for name, selector in numbers.items()
    } == {key: SETTING_NUMBERS[key] for key in numbers}
    assert {s.config["mode"] for s in numbers.values()} == {"box"}

    assert outdoor.config["domain"] == ["sensor"]
    assert outdoor.config["device_class"] == ["temperature"]
    assert "multiple" not in outdoor.config or outdoor.config["multiple"] is False
    # Nothing is required and nothing has a default: empty = not stored.
    assert defaults(result, "room_thermostat") == {}
    assert defaults(result, "windows_outdoor") == {}


async def test_options_flow_stores_the_new_sections_flat(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        options_input(
            room_thermostat={"setpoint_min": 16.0, "setpoint_max": 24.5},
            windows_outdoor={
                "window_open_delay_seconds": 15.0,
                "window_warn_minutes": 90.0,
                "window_check_minutes": 20.0,
                "window_check_drop_kelvin": 0.4,
                "outdoor_sensor": "sensor.outdoor",
                "outdoor_cutoff_celsius": 17.5,
            },
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = dict(entry.options)
    assert options["setpoint_min"] == 16.0
    assert options["setpoint_max"] == 24.5
    assert options["window_open_delay_seconds"] == 15
    assert type(options["window_open_delay_seconds"]) is int
    assert options["window_warn_minutes"] == 90
    assert type(options["window_warn_minutes"]) is int
    assert options["window_check_minutes"] == 20
    assert options["window_check_drop_kelvin"] == 0.4
    assert options["outdoor_sensor"] == "sensor.outdoor"
    assert options["outdoor_cutoff_celsius"] == 17.5
    # Flat: no section name leaks into the options.
    assert not {"room_thermostat", "windows_outdoor"} & set(options)
    settings = resolve_settings(options, {})
    assert (settings.setpoint_min, settings.window_warn_minutes) == (16.0, 90)
    assert settings.outdoor_cutoff_celsius == 17.5


async def test_options_new_fields_empty_are_not_stored(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options_input()
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    for key in (
        "setpoint_min",
        "setpoint_max",
        "window_open_delay_seconds",
        "window_warn_minutes",
        "window_check_minutes",
        "window_check_drop_kelvin",
        "outdoor_sensor",
        "outdoor_cutoff_celsius",
    ):
        assert key not in entry.options


async def test_options_form_suggests_the_saved_window_and_outdoor_values(hass) -> None:
    saved = make_entry(
        hass,
        [],
        {
            "setpoint_max": 26.0,
            "window_check_minutes": 0,
            "outdoor_sensor": "sensor.outdoor",
            "outdoor_cutoff_celsius": 15.0,
        },
    )
    result = await hass.config_entries.options.async_init(saved.entry_id)

    assert suggested_values(result, "room_thermostat") == {"setpoint_max": 26.0}
    assert suggested_values(result, "windows_outdoor") == {
        "window_check_minutes": 0,
        "outdoor_sensor": "sensor.outdoor",
        "outdoor_cutoff_celsius": 15.0,
    }

    # Clearing the sensor removes it; the other values stay.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        options_input(
            room_thermostat={"setpoint_max": 26.0},
            windows_outdoor={"outdoor_cutoff_celsius": 15.0},
        ),
    )
    assert "outdoor_sensor" not in saved.options
    assert saved.options["outdoor_cutoff_celsius"] == 15.0


@pytest.mark.parametrize(
    ("submitted", "valid"),
    [
        ({"setpoint_min": 10.0, "setpoint_max": 20.0}, True),
        ({"setpoint_min": 20.0, "setpoint_max": 20.0}, False),
        ({"setpoint_min": 25.0, "setpoint_max": 20.0}, False),
        # The global pair alone: a lone value meets the built-in other bound.
        ({"setpoint_min": 30.0}, False),
        ({"setpoint_min": 29.5}, True),
        ({"setpoint_max": 5.0}, False),
        ({"setpoint_max": 5.5}, True),
        ({}, True),
    ],
)
async def test_options_flow_rejects_a_setpoint_range_that_is_not_a_range(
    hass, entry, submitted, valid
) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], options_input(room_thermostat=submitted)
    )
    if valid:
        assert result["type"] is FlowResultType.CREATE_ENTRY
    else:
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "setpoint_range"}
        assert suggested_values(result, "room_thermostat") == submitted
        assert not entry.options
