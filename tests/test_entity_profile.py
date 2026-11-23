"""Entity profiles (Beta): validation, the entity gateway and a room end to end."""

from typing import TYPE_CHECKING
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
import voluptuous as vol
import yaml
from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easy_trv.adapters.entity_gateway import (
    ENTITY_PREFIX,
    EntityGateway,
    RoutingGateway,
    find_device_entity,
    parse_any_version,
)
from custom_components.easy_trv.adapters.zha_gateway import GatewayError, ZhaUnavailable
from custom_components.easy_trv.const import (
    CONF_NAME,
    CONF_PROFILE_YAML,
    DOMAIN,
    SUBENTRY_PROFILE,
    SUBENTRY_ROOM,
)
from custom_components.easy_trv.core.model import (
    SetHvacMode,
    SetRoomTemperature,
    SetSource,
    SetWindowDetection,
)
from custom_components.easy_trv.profile_flow import share_url
from custom_components.easy_trv.profile_wizard import profile_id
from custom_components.easy_trv.profiles.loader import BUILTIN_DIR, parse_profile_text
from custom_components.easy_trv.profiles.schema import validate_profile_dict
from tests.helpers_entry import (
    FAST_OPTIONS,
    make_entry,
    room_data,
    settle,
    use_config_dir,
)

if TYPE_CHECKING:
    from homeassistant.core import ServiceCall

GATEWAY_FACTORY = "custom_components.easy_trv.GATEWAY_FACTORY"

PROFILE = """\
schema: 1
id: acme_z2m
name: Acme TRV (Zigbee2MQTT)
backend: entity
match: { manufacturer: Acme, model: TRV-1 }
capabilities:
  external_temperature:
    intent: set_room_temperature
    entity: { domain: number, key: external_temperature_input }
    range: [0, 99.9]
  sensor_select:
    intent: set_source
    entity: { domain: select, key: temperature_sensor_select }
    options: { external: external, internal: internal }
  window:
    intent: set_window_detection
    entity: { domain: switch, key: open_window }
"""


# --- Profile document --------------------------------------------------------


def test_entity_profile_adds_the_climate_capabilities() -> None:
    _data, profile = parse_profile_text(PROFILE, "acme")
    assert profile.backend == "entity"
    assert set(profile.capabilities) == {
        "external_temperature",
        "sensor_select",
        "window",
        "setpoint",
        "hvac_mode",
    }
    assert profile.observed() == ("setpoint", "hvac_mode")
    kinds = {b.capability: b.kind for b in profile.bindings.values()}
    assert kinds == {
        "external_temperature": "number",
        "sensor_select": "select",
        "window": "switch",
        "setpoint": "climate_temperature",
        "hvac_mode": "climate_mode",
    }
    op = profile.plan(SetSource("internal"), None)
    assert profile.bindings[op.attribute].options[op.raw_value] == "internal"
    assert profile.plan(SetRoomTemperature(21.4), None).raw_value == 2140
    assert profile.plan(SetWindowDetection(enabled=True), None).raw_value == 1
    mode = profile.plan(SetHvacMode("off"), None)
    assert profile.bindings[mode.attribute].options[mode.raw_value] == "off"
    # The hidden entities are named by their key.
    assert profile.capabilities["window"].entity.translation_key == "open_window"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            (
                "    entity: { domain: switch, key: open_window }",
                "    entity: { domain: switch }",
            ),
            "needs a key",
        ),
        (("options: { external: external, internal: internal }", ""), "needs options"),
        (("intent: set_window_detection", "intent: set_setpoint"), "climate entity"),
        (
            ("intent: set_room_temperature", "intent: set_frost_temperature"),
            "set_room_temperature",
        ),
        (("backend: entity", "backend: cloud"), "backend"),
    ],
)
def test_invalid_entity_profiles_are_rejected(change, message) -> None:
    with pytest.raises(vol.Invalid, match=message):
        validate_profile_dict(yaml.safe_load(PROFILE.replace(*change, 1)))


def test_native_profile_may_say_backend_zha() -> None:
    text = (BUILTIN_DIR / "sonoff_trvzb_native.yaml").read_text()
    data = validate_profile_dict({**yaml.safe_load(text), "backend": "zha"})
    assert "backend" not in data


def test_firmware_text_is_read_leniently() -> None:
    assert parse_any_version("Zigbee2MQTT 1.4.4 (build)") == (1, 4, 4)
    assert parse_any_version("v2.1") == (2, 1, 0)
    assert parse_any_version("0x00001300") is None
    assert parse_any_version(None) is None


# --- A thermostat of another integration -------------------------------------


@pytest.fixture
def acme(hass):
    """A Zigbee2MQTT-like thermostat: climate, number, select and switch."""
    owner = MockConfigEntry(domain="mqtt")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={("mqtt", "acme")},
        name="Acme",
        manufacturer="Acme",
        model="TRV-1",
        sw_version="1.2.3",
    )
    registry = er.async_get(hass)
    for domain, object_id in (
        ("climate", "acme"),
        ("number", "acme_external_temperature_input"),
        ("select", "acme_temperature_sensor_select"),
        ("switch", "acme_open_window"),
    ):
        registry.async_get_or_create(
            domain,
            "mqtt",
            f"0x01_{object_id}",
            suggested_object_id=object_id,
            device_id=device.id,
        )
    hass.states.async_set("climate.acme", "heat", {"temperature": 20.0})
    hass.states.async_set("number.acme_external_temperature_input", "0")
    hass.states.async_set("select.acme_temperature_sensor_select", "internal")
    hass.states.async_set("switch.acme_open_window", "off")
    hass.states.async_set("sensor.t1", "21.3", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.t2", "20.0", {"unit_of_measurement": "°C"})
    return device


@pytest.fixture
def services(hass):
    """Fake services that set the entity state like a real device would."""
    return install_services(hass)


def install_services(hass):
    """Register the fake services (again, after a platform replaced them)."""
    calls: list[tuple[str, str, dict]] = []

    def handler(domain, service):
        async def handle(call: ServiceCall) -> None:
            data = dict(call.data)
            calls.append((domain, service, dict(data)))
            entity_id = data.pop("entity_id")
            if isinstance(entity_id, list):
                (entity_id,) = entity_id
            state = hass.states.get(entity_id)
            attributes = dict(state.attributes) if state else {}
            if service == "set_value":
                hass.states.async_set(entity_id, str(data["value"]), attributes)
            elif service == "select_option":
                hass.states.async_set(entity_id, data["option"], attributes)
            elif service in ("turn_on", "turn_off"):
                hass.states.async_set(entity_id, service[5:], attributes)
            elif service == "set_temperature":
                attributes["temperature"] = data["temperature"]
                hass.states.async_set(entity_id, state.state, attributes)
            elif service == "set_hvac_mode":
                hass.states.async_set(entity_id, data["hvac_mode"], attributes)

        return handle

    for domain, service in (
        ("number", "set_value"),
        ("select", "select_option"),
        ("switch", "turn_on"),
        ("switch", "turn_off"),
        ("climate", "set_temperature"),
        ("climate", "set_hvac_mode"),
    ):
        hass.services.async_register(domain, service, handler(domain, service))
    return calls


def find(hass, device, domain, key):
    return find_device_entity(hass, device.id, domain, key)


def test_entities_are_found_by_key(hass, acme) -> None:
    assert find(hass, acme, "switch", "open_window") == "switch.acme_open_window"
    assert find(hass, acme, "number", "missing") is None


async def test_gateway_writes_reads_and_observes(hass, acme, services) -> None:
    _data, profile = parse_profile_text(PROFILE, "acme")
    gateway = RoutingGateway(hass, None)
    ref = gateway.resolve("climate.acme")
    assert ref.ieee == f"{ENTITY_PREFIX}{acme.id}"
    assert (ref.manufacturer, ref.model, ref.firmware) == ("Acme", "TRV-1", (1, 2, 3))
    gateway.bind(ref, profile)

    op = profile.plan(SetRoomTemperature(21.4), None)
    await gateway.write(ref.ieee, 0, 0, op.attribute, op.raw_value)
    assert services[-1] == (
        "number",
        "set_value",
        {"entity_id": "number.acme_external_temperature_input", "value": 21.4},
    )
    assert await gateway.read(ref.ieee, 0, 0, [op.attribute]) == {op.attribute: 2140}
    assert gateway.is_available(ref.ieee)
    # Entities carry no radio values, so the silent check never fires for them.
    assert not gateway.link(ref.ieee).known

    seen = []
    setpoint = next(
        a for a, b in profile.bindings.items() if b.kind == "climate_temperature"
    )
    release = gateway.subscribe(ref.ieee, 0, 0, [setpoint], lambda *a: seen.append(a))
    # Someone turns the knob: reported.
    hass.states.async_set("climate.acme", "heat", {"temperature": 22.5})
    await hass.async_block_till_done()
    assert seen == [(ref.ieee, setpoint, "report", 2250)]
    # Our own write: not reported.
    await gateway.write(ref.ieee, 0, 0, setpoint, 2100)
    await hass.async_block_till_done()
    assert len(seen) == 1
    release()
    release()


@pytest.mark.usefixtures("acme")
async def test_gateway_reports_missing_entities(hass) -> None:
    _data, profile = parse_profile_text(
        PROFILE.replace("key: open_window", "key: no_such_switch"), "acme"
    )
    gateway = EntityGateway(hass)
    ref = gateway.resolve("climate.acme")
    gateway.bind(ref, profile)
    op = profile.plan(SetWindowDetection(enabled=True), None)
    with pytest.raises(GatewayError, match="no_such_switch"):
        await gateway.write(ref.ieee, 0, 0, op.attribute, op.raw_value)
    assert gateway.cached(ref.ieee, 0, 0, op.attribute) is None


async def test_routing_without_zha_refuses_zha_devices(hass) -> None:
    gateway = RoutingGateway(hass, None)
    with pytest.raises(GatewayError, match="ZHA is not set up"):
        gateway.is_available("00:11")
    # The radio values never raise, also without ZHA.
    assert not gateway.link("00:11").known


# --- A room end to end -------------------------------------------------------


@pytest.mark.usefixtures("acme")
async def test_room_drives_a_thermostat_through_its_entities(hass) -> None:
    room = room_data(trvs=["climate.acme"], profile="acme_z2m")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        version=3,
        options=FAST_OPTIONS,
        title="Easy TRV",
        subentries_data=[
            ConfigSubentryData(
                data=room,
                subentry_type=SUBENTRY_ROOM,
                title=room[CONF_NAME],
                unique_id=room[CONF_NAME].casefold(),
            ),
            ConfigSubentryData(
                data={CONF_PROFILE_YAML: PROFILE},
                subentry_type=SUBENTRY_PROFILE,
                title="Acme TRV (Zigbee2MQTT)",
                unique_id=None,
            ),
        ],
    )
    entry.add_to_hass(hass)

    # Set the entity components up first, so the fake services stay in place.
    for domain in ("number", "select", "switch", "climate"):
        assert await async_setup_component(hass, domain, {})
    services = install_services(hass)
    with patch(GATEWAY_FACTORY, side_effect=ZhaUnavailable("no zha")):
        assert await hass.config_entries.async_setup(entry.entry_id)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert set(entry.runtime_data.rooms) == {
        sub.subentry_id
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_ROOM
    }
    called = {(domain, service) for domain, service, _ in services}
    assert ("number", "set_value") in called
    assert ("select", "select_option") in called
    assert hass.states.get("select.acme_temperature_sensor_select").state == "external"
    assert float(hass.states.get("number.acme_external_temperature_input").state) > 0
    assert hass.states.get("switch.acme_open_window").state == "on"
    # Every write was verified: no thermostat or room issue.
    assert [i for d, i in ir.async_get(hass).issues if d == DOMAIN] == []
    assert hass.states.get("sensor.living_heating_state") is not None


# --- Wizard and sharing ------------------------------------------------------


@pytest.mark.usefixtures("acme")
async def test_wizard_builds_an_entity_profile(hass, tmp_path) -> None:
    use_config_dir(hass, tmp_path)
    hass.states.async_set(
        "select.acme_temperature_sensor_select",
        "internal",
        {"options": ["internal", "external"]},
    )
    entry = make_entry(hass, [], {})
    flows = hass.config_entries.subentries
    result = await flows.async_init(
        (entry.entry_id, SUBENTRY_PROFILE), context={"source": "user"}
    )
    result = await flows.async_configure(result["flow_id"], {"next_step_id": "create"})
    assert result["step_id"] == "create"

    result = await flows.async_configure(
        result["flow_id"], {"name": "", "thermostat": "climate.acme"}
    )
    assert result["errors"] == {"name": "name_required"}

    result = await flows.async_configure(
        result["flow_id"], {"name": "Acme TRV", "thermostat": "climate.acme"}
    )
    assert result["step_id"] == "create_map"
    offered = result["data_schema"].schema
    assert offered["window_detection"].config["include_entities"] == [
        "switch.acme_open_window"
    ]
    assert "external_temperature" in offered

    result = await flows.async_configure(
        result["flow_id"],
        {
            "external_temperature": "number.acme_external_temperature_input",
            "source": "select.acme_temperature_sensor_select",
            "window_detection": "switch.acme_open_window",
        },
    )
    assert result["step_id"] == "create_source"
    result = await flows.async_configure(
        result["flow_id"], {"external": "external", "internal": "internal"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Acme TRV"
    document = yaml.safe_load(result["data"][CONF_PROFILE_YAML])
    assert document["id"] == "acme_trv"
    assert document["backend"] == "entity"
    assert document["match"] == {"manufacturer": "Acme", "model": "TRV-1"}
    assert document["capabilities"] == {
        "external_temperature": {
            "intent": "set_room_temperature",
            "entity": {"domain": "number", "key": "external_temperature_input"},
        },
        "source_select": {
            "intent": "set_source",
            "entity": {"domain": "select", "key": "temperature_sensor_select"},
            "options": {"external": "external", "internal": "internal"},
        },
        "window_detection": {
            "intent": "set_window_detection",
            "entity": {"domain": "switch", "key": "open_window"},
        },
    }


async def test_wizard_rejects_a_thermostat_without_entities(hass, tmp_path) -> None:
    use_config_dir(hass, tmp_path)
    entry = make_entry(hass, [], {})
    flows = hass.config_entries.subentries
    result = await flows.async_init(
        (entry.entry_id, SUBENTRY_PROFILE), context={"source": "user"}
    )
    result = await flows.async_configure(result["flow_id"], {"next_step_id": "create"})
    result = await flows.async_configure(
        result["flow_id"], {"name": "X", "thermostat": "climate.nothing"}
    )
    assert result["errors"] == {"thermostat": "no_entities"}


def test_share_url_prefills_the_issue_form() -> None:
    url = urlparse(share_url("Acme TRV", PROFILE))
    assert url.netloc == "github.com"
    assert url.path == "/ImSorry99/easy-trv/issues/new"
    query = parse_qs(url.query)
    assert query["template"] == ["profile.yml"]
    assert query["title"] == ["Profile: Acme TRV"]
    assert query["profile"] == [PROFILE]


def test_profile_id_starts_with_a_letter() -> None:
    assert profile_id("Mein Thermostat 2") == "mein_thermostat_2"
    assert profile_id("2nd floor") == "profile_2nd_floor"
    assert profile_id("!!!") == "my_thermostat"
