import logging
from types import MappingProxyType
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easy_trv import issues
from custom_components.easy_trv.adapters.zha_gateway import ZhaUnavailable
from custom_components.easy_trv.const import (
    CONF_AREA,
    CONF_OUTDOOR_CUTOFF_CELSIUS,
    CONF_OUTDOOR_SENSOR,
    DOMAIN,
    SUBENTRY_ROOM,
)
from custom_components.easy_trv.controller import RoomController
from custom_components.easy_trv.profiles.loader import (
    BUILTIN_DIR,
)
from tests.helpers_entry import (
    CELSIUS,
    FAST_OPTIONS,
    FIRMWARE,
    GATEWAY_FACTORY,
    advance,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
    use_config_dir,
)

VALUE = 0x600D
BUILTIN_TEXT = (BUILTIN_DIR / "sonoff_trvzb_native.yaml").read_text()


def zha_issue(hass):
    return ir.async_get(hass).async_get_issue(DOMAIN, "zha_api_incompatible")


def room_issue(hass, subentry_id: str):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"room_invalid_{subentry_id}")


def window_issue(hass, kind: str, room_id: str):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{kind}_{room_id}")


def subentry_of(entry, title: str):
    return next(s for s in entry.subentries.values() if s.title == title)


def value_writes(gateway) -> list[int]:
    """Return the room temperatures written so far (raw centi-degrees)."""
    return [w[4] for w in gateway.writes if w[3] == VALUE]


@pytest.fixture(autouse=True)
def fake_gateway(hass):
    with patched_gateway(hass) as gateway:
        yield gateway


@pytest.fixture
def entry(hass):
    return make_entry(hass, [room_data()], FAST_OPTIONS)


# --- Setup ----------------------------------------------------------------


async def test_setup_creates_room_device_and_state_sensor(hass, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    state = hass.states.get("sensor.living_heating_state")
    assert state is not None
    assert state.state == "heating"
    assert state.attributes["source"] == "external"

    subentry_id = next(iter(entry.subentries))
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry_id), entry.entry_id
    )
    assert device is not None
    assert device.name == "Living heating"
    # A thermostat and a state sensor per room, next to the statistics sensors
    # (see test_statistics_sensor), attached to the room's subentry.
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    by_id = {e.entity_id: e for e in entities}
    assert {"climate.living_heating", "sensor.living_heating_state"} <= by_id.keys()
    assert {e.domain for e in entities} == {"climate", "sensor"}
    assert by_id["sensor.living_heating_state"].unique_id == f"{subentry_id}_state"
    assert by_id["climate.living_heating"].unique_id == f"{subentry_id}_climate"
    assert {e.config_subentry_id for e in entities} == {subentry_id}
    assert entry.runtime_data.rooms.keys() == {subentry_id}
    # The capability labels are loaded once per entry, for the Repairs texts.
    labels = entry.runtime_data.labels
    assert labels["set_window_detection"] == "Thermostat's open-window detection"
    assert zha_issue(hass) is None


async def test_setup_removes_the_retired_entities(hass, entry) -> None:
    registry = er.async_get(hass)
    subentry_id = next(iter(entry.subentries))

    def register(domain, platform, suffix, object_id, owner=entry):
        return registry.async_get_or_create(
            domain,
            platform,
            f"{subentry_id}_{suffix}",
            config_entry=owner,
            config_subentry_id=subentry_id if owner is entry else None,
            suggested_object_id=object_id,
        )

    # What 0.4.0 and 0.5.0 left behind and no platform provides now: the
    # operating mode select, and the status sensor.
    select = register("select", DOMAIN, "operating_mode", "living_heating_mode")
    status = register("sensor", DOMAIN, "status", "living_heating_status")
    # Neighbours that must stay: entities of this integration whose domain or
    # unique id merely look similar (the state sensor included), and the same
    # unique ids of another integration.
    sensor_like_select = register("sensor", DOMAIN, "operating_mode", "lookalike")
    select_like_status = register("select", DOMAIN, "status", "other_lookalike")
    # Only the exact unique id of the status sensor is retired, not any sensor
    # whose unique id ends with "_status".
    longer_status = register("sensor", DOMAIN, "window_status", "window_lookalike")
    state = register("sensor", DOMAIN, "state", "living_heating_state")
    other_entry = MockConfigEntry(domain="other")
    other_entry.add_to_hass(hass)
    foreign_select = register(
        "select", "other", "operating_mode", "foreign_mode", other_entry
    )
    foreign_status = register("sensor", "other", "status", "foreign", other_entry)
    for retired in (select, status):
        assert registry.async_get(retired.entity_id) is not None

    await start(hass, entry)

    for retired in (select, status):
        assert registry.async_get(retired.entity_id) is None
        assert hass.states.get(retired.entity_id) is None
    for kept in (
        sensor_like_select,
        select_like_status,
        longer_status,
        foreign_select,
        foreign_status,
    ):
        assert registry.async_get(kept.entity_id) is not None
    # The state sensor keeps its registry entry and is live.
    assert registry.async_get(state.entity_id).id == state.id
    assert hass.states.get(state.entity_id).state == "heating"
    assert registry.async_get("climate.living_heating") is not None


async def test_rooms_start_only_when_home_assistant_has_started(
    hass, entry, fake_gateway
) -> None:
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)
    state = hass.states.get("sensor.living_heating_state")
    assert state is not None
    assert "source" in state.attributes
    assert state.attributes["source"] is None
    assert fake_gateway.writes == []

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)
    state = hass.states.get("sensor.living_heating_state")
    assert state.attributes["source"] == "external"
    assert fake_gateway.writes


async def test_unload_before_start_cancels_the_pending_start(
    hass, entry, fake_gateway
) -> None:
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    waiting = hass.bus.async_listeners().get(EVENT_HOMEASSISTANT_STARTED, 0)
    assert await hass.config_entries.async_unload(entry.entry_id)
    # Our wait for the start of Home Assistant is gone.
    assert hass.bus.async_listeners().get(EVENT_HOMEASSISTANT_STARTED, 0) == (
        waiting - 1
    )

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)
    assert fake_gateway.writes == []


async def test_setup_zha_unavailable_retries_and_clears_issue(hass, entry) -> None:
    with patch(GATEWAY_FACTORY, side_effect=ZhaUnavailable("no zha")):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = zha_issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {"error": "no zha"}

    # ZHA comes up: the retry succeeds and the issue disappears.
    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert zha_issue(hass) is None
    assert hass.states.get("sensor.living_heating_state").state == "heating"


async def test_profile_auto_selects_builtin_and_rejects_unmatched(
    hass, fake_gateway
) -> None:
    fake_gateway.add_device("climate.x", "00:33", model="TRV-ZBT")
    hass.states.async_set("climate.x", "heat")
    foreign = make_entry(hass, [room_data(trvs=["climate.x"])], FAST_OPTIONS)

    await start(hass, foreign)

    # The entry loads; the room that no profile fits is reported and skipped.
    assert foreign.state is ConfigEntryState.LOADED
    assert foreign.runtime_data.rooms == {}
    issue = room_issue(hass, subentry_of(foreign, "Living").subentry_id)
    assert issue is not None
    assert "climate.x (SONOFF TRV-ZBT)" in issue.translation_placeholders["error"]
    assert hass.states.get("sensor.living_heating_state") is None


async def test_non_zha_thermostat_invalidates_only_its_room(hass) -> None:
    hass.states.async_set("climate.mqtt", "heat")
    entry = make_entry(hass, [room_data(trvs=["climate.a", "climate.mqtt"])])

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    issue = room_issue(hass, subentry_of(entry, "Living").subentry_id)
    assert issue is not None
    assert (
        "climate.mqtt is not a ZHA thermostat"
        in (issue.translation_placeholders["error"])
    )


async def test_explicit_profile_is_used(hass) -> None:
    entry = make_entry(hass, [room_data(profile="sonoff_trvzb_native")], FAST_OPTIONS)
    await start(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.living_heating_state").state == "heating"


async def test_unknown_explicit_profile_invalidates_only_its_room(hass) -> None:
    entry = make_entry(hass, [room_data(profile="no_such_profile")])

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    issue = room_issue(hass, subentry_of(entry, "Living").subentry_id)
    assert issue is not None
    assert "no_such_profile" in issue.translation_placeholders["error"]


async def test_a_room_with_the_global_profile_uses_the_global_choice(hass) -> None:
    entry = make_entry(hass, [room_data(profile="global")], FAST_OPTIONS)

    await start(hass, entry)

    assert room_issue(hass, subentry_of(entry, "Living").subentry_id) is None
    assert hass.states.get("sensor.living_heating_state").state == "heating"


async def test_a_room_with_the_global_profile_follows_an_explicit_global_one(
    hass,
) -> None:
    options = {**FAST_OPTIONS, "profile": "no_such_profile"}
    entry = make_entry(hass, [room_data(profile="global")], options)

    await start(hass, entry)

    issue = room_issue(hass, subentry_of(entry, "Living").subentry_id)
    assert issue is not None
    assert "no_such_profile" in issue.translation_placeholders["error"]


# --- A room that cannot be set up -------------------------------------------


async def test_one_unresolvable_room_leaves_the_other_rooms_running(
    hass, fake_gateway, caplog
) -> None:
    entry = make_entry(
        hass,
        [
            room_data("Living"),
            room_data("Bedroom", trvs=["climate.ghost"], temp_sensors=["sensor.t2"]),
        ],
        FAST_OPTIONS,
    )

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    living, bedroom = subentry_of(entry, "Living"), subentry_of(entry, "Bedroom")
    assert entry.runtime_data.rooms.keys() == {living.subentry_id}
    assert hass.states.get("sensor.living_heating_state").state == "heating"
    assert hass.states.get("sensor.bedroom_heating_state") is None
    assert room_issue(hass, living.subentry_id) is None
    issue = room_issue(hass, bedroom.subentry_id)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert issue.translation_key == "room_invalid"
    assert issue.translation_placeholders == {
        "room": "Bedroom",
        "error": "climate.ghost is not a ZHA thermostat",
    }
    assert "Room Bedroom could not be set up" in caplog.text

    # Fixing the room and reloading removes the issue and brings the room up.
    fake_gateway.add_device("climate.c", "00:33", firmware=FIRMWARE)
    hass.states.async_set("climate.c", "heat")
    hass.config_entries.async_update_subentry(
        entry,
        bedroom,
        data=room_data("Bedroom", trvs=["climate.c"], temp_sensors=["sensor.t2"]),
    )
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert room_issue(hass, bedroom.subentry_id) is None
    assert entry.runtime_data.rooms.keys() == {living.subentry_id, bedroom.subentry_id}
    assert hass.states.get("sensor.bedroom_heating_state").state == "heating"


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"hysteresis": "garbage"}, "garbage"),  # ValueError
        ({"trvs": None}, "TypeError"),
        ({"trvs": ...}, "KeyError"),  # the key is missing altogether
    ],
)
async def test_a_room_with_corrupt_data_is_skipped_like_an_unresolvable_one(
    hass, overrides, fragment
) -> None:
    bedroom = room_data("Bedroom", trvs=["climate.b"], temp_sensors=["sensor.t2"])
    for key, value in overrides.items():
        if value is ...:
            del bedroom[key]
        else:
            bedroom[key] = value
    entry = make_entry(
        hass, [room_data("Living", trvs=["climate.a"]), bedroom], FAST_OPTIONS
    )

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    living, broken = subentry_of(entry, "Living"), subentry_of(entry, "Bedroom")
    assert entry.runtime_data.rooms.keys() == {living.subentry_id}
    assert hass.states.get("sensor.living_heating_state").state == "heating"
    assert hass.states.get("sensor.bedroom_heating_state") is None
    issue = room_issue(hass, broken.subentry_id)
    assert issue is not None
    assert issue.translation_placeholders["room"] == "Bedroom"
    assert fragment in issue.translation_placeholders["error"]
    assert room_issue(hass, living.subentry_id) is None


async def test_a_room_that_works_again_loses_its_issue_on_reload(
    hass, fake_gateway
) -> None:
    entry = make_entry(hass, [room_data(trvs=["climate.a", "climate.mqtt"])])
    await start(hass, entry)
    living = subentry_of(entry, "Living")
    assert room_issue(hass, living.subentry_id) is not None

    # The thermostat shows up in ZHA after the first attempt.
    fake_gateway.add_device("climate.mqtt", "00:44")
    hass.states.async_set("climate.mqtt", "heat")
    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    assert room_issue(hass, living.subentry_id) is None
    assert entry.runtime_data.rooms.keys() == {living.subentry_id}


async def test_setup_removes_room_issues_of_deleted_rooms(hass, entry) -> None:
    issues.raise_room_invalid(hass, "deleted_subentry", "Old room", "gone")

    await start(hass, entry)

    assert room_issue(hass, "deleted_subentry") is None


async def test_setup_removes_window_issues_of_deleted_rooms(hass, entry) -> None:
    for room_id in ("deleted_subentry", "stale"):
        issues.raise_window_open_long(hass, room_id, "Old room", 5)
        issues.raise_window_contact_suspect(hass, room_id, "Old room", 5, "0.3")
    living = subentry_of(entry, "Living")
    issues.raise_window_open_long(hass, living.subentry_id, "Living", 5)

    await start(hass, entry)

    for room_id in ("deleted_subentry", "stale"):
        assert window_issue(hass, "window_open_long", room_id) is None
        assert window_issue(hass, "window_contact_suspect", room_id) is None
    # The room that exists was cleared by its own controller when it started.
    assert window_issue(hass, "window_open_long", living.subentry_id) is None


async def test_the_global_outdoor_sensor_reaches_the_room_controllers(hass) -> None:
    entry = make_entry(
        hass,
        [room_data()],
        {
            **FAST_OPTIONS,
            CONF_OUTDOOR_SENSOR: "sensor.outdoor",
            CONF_OUTDOOR_CUTOFF_CELSIUS: 18.0,
        },
    )
    hass.states.async_set("sensor.outdoor", "25", CELSIUS)

    await start(hass, entry)

    [controller] = entry.runtime_data.rooms.values()
    assert controller.outdoor_hold is True
    hass.states.async_set("sensor.outdoor", "10", CELSIUS)
    await settle(hass)
    assert controller.outdoor_hold is False


async def test_user_profile_covers_a_foreign_thermostat(
    hass, tmp_path, fake_gateway
) -> None:
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_zbt", 1)
    text = text.replace("model: TRVZB", "model: TRV-ZBT", 1)
    use_config_dir(hass, tmp_path).joinpath("zbt.yaml").write_text(text)
    fake_gateway.add_device("climate.x", "00:33", model="TRV-ZBT")
    hass.states.async_set("climate.x", "heat")
    entry = make_entry(hass, [room_data(trvs=["climate.x"])], FAST_OPTIONS)

    await start(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert set(entry.runtime_data.profiles.profiles) == {
        "sonoff_trvzb_native",
        "user_zbt",
    }


async def test_invalid_user_profile_raises_issue_and_setup_still_succeeds(
    hass, tmp_path, entry
) -> None:
    profiles_dir = use_config_dir(hass, tmp_path)
    (profiles_dir / "broken.yaml").write_text("schema: 1\nid: [unterminated")

    await start(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "profile_invalid_broken")
    assert issue is not None
    assert issue.translation_placeholders["file"] == "broken.yaml"
    assert issue.translation_placeholders["error"]
    assert [e.file for e in entry.runtime_data.profiles.errors] == ["broken.yaml"]

    # Fixing the file and reloading removes the issue.
    (profiles_dir / "broken.yaml").unlink()
    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert ir.async_get(hass).async_get_issue(DOMAIN, "profile_invalid_broken") is None


async def test_start_is_logged_and_one_failing_room_does_not_block_the_others(
    hass, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="custom_components.easy_trv")
    entry = make_entry(
        hass,
        [
            room_data("Living", trvs=["climate.a"]),
            room_data("Bedroom", trvs=["climate.b"], temp_sensors=["sensor.t2"]),
        ],
        FAST_OPTIONS,
    )
    original = RoomController.async_start

    async def flaky_start(self) -> None:
        if self.snapshot()["config"]["name"] == "Living":
            msg = "boom"
            raise RuntimeError(msg)
        await original(self)

    with patch.object(RoomController, "async_start", flaky_start):
        await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert "Room Living (1 thermostat(s), profile sonoff_trvzb_native) could not" in (
        caplog.text
    )
    assert "Started room Bedroom (1 thermostat(s), profile sonoff_trvzb_native)" in (
        caplog.text
    )
    assert (
        hass.states.get("sensor.bedroom_heating_state").attributes["source"]
        == "external"
    )
    assert hass.states.get("sensor.living_heating_state").attributes["source"] is None


async def test_subentries_of_other_types_are_ignored(hass) -> None:
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)
    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType({"anything": 1}),
            subentry_type="not_a_room",
            title="Other",
            unique_id="other",
        ),
    )

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert len(entry.runtime_data.rooms) == 1
    assert hass.states.get("sensor.living_heating_state") is not None
    assert hass.states.get("sensor.other_heating_state") is None


# --- Reload on change -----------------------------------------------------


async def test_options_change_reloads(hass, entry) -> None:
    await start(hass, entry)
    before = entry.runtime_data
    subentry_id = next(iter(entry.subentries))
    assert before.rooms[subentry_id].snapshot()["params"]["refresh"] == 1200.0

    hass.config_entries.async_update_entry(
        entry, options={**FAST_OPTIONS, "refresh_minutes": 30}
    )
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data is not before
    new_room = entry.runtime_data.rooms[subentry_id]
    assert new_room.snapshot()["params"]["refresh"] == 1800.0
    assert (
        hass.states.get("sensor.living_heating_state").attributes["source"]
        == "external"
    )


async def test_adding_a_room_reloads_and_adds_its_state_sensor(
    hass, entry, fake_gateway
) -> None:
    await start(hass, entry)
    fake_gateway.add_device("climate.c", "00:33", firmware=FIRMWARE)
    hass.states.async_set("climate.c", "heat")

    hass.config_entries.async_add_subentry(
        entry,
        ConfigSubentry(
            data=MappingProxyType(
                room_data("Bedroom", trvs=["climate.c"], temp_sensors=["sensor.t2"])
            ),
            subentry_type=SUBENTRY_ROOM,
            title="Bedroom",
            unique_id="bedroom",
        ),
    )
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert len(entry.runtime_data.rooms) == 2
    assert hass.states.get("sensor.living_heating_state").state == "heating"
    assert hass.states.get("sensor.bedroom_heating_state").state == "heating"


# --- Unload ---------------------------------------------------------------


async def test_reload_clears_the_unreachable_issue_of_a_recovered_thermostat(
    hass, freezer, entry, fake_gateway
) -> None:
    # The thermostat never confirms the value: three attempts, then an error.
    fake_gateway.read_override[("00:11", VALUE)] = 2000
    await start(hass, entry)
    await advance(hass, freezer, 25)
    issues_registry = ir.async_get(hass)
    assert issues_registry.async_get_issue(DOMAIN, "trv_unreachable_0011") is not None
    assert hass.states.get("sensor.living_heating_state").state == "fault"

    # Re-paired or fixed: the thermostat answers again, the entry is reloaded.
    del fake_gateway.read_override[("00:11", VALUE)]
    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert issues_registry.async_get_issue(DOMAIN, "trv_unreachable_0011") is None
    assert hass.states.get("sensor.living_heating_state").state == "heating"


async def test_setup_removes_issues_of_thermostats_in_no_room(hass, entry) -> None:
    for ieee in ("00:99", "00:11"):
        issues.raise_unreachable(
            hass, ieee, room="Old", setting="x", thermostat="y", attempts="3", error="z"
        )
        issues.raise_firmware(
            hass,
            ieee,
            room="Old",
            setting="x",
            thermostat="y",
            required="1",
            current="0",
        )
    registry = ir.async_get(hass)

    # Rooms start only once Home Assistant has started, so the cleanup that
    # setup itself does is visible on its own.
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await settle(hass)

    for kind in ("trv_unreachable", "trv_firmware"):
        # Not a thermostat of any configured room: removed.
        assert registry.async_get_issue(DOMAIN, f"{kind}_0099") is None
        # A configured thermostat: left to its room, which clears it at start.
        assert registry.async_get_issue(DOMAIN, f"{kind}_0011") is not None

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)
    for kind in ("trv_unreachable", "trv_firmware"):
        assert registry.async_get_issue(DOMAIN, f"{kind}_0011") is None


async def test_unload_stops_the_controllers(hass, freezer, entry, fake_gateway) -> None:
    await start(hass, entry)
    fake_gateway.writes.clear()
    # Control: while loaded, a changed sensor value is written to the TRVs.
    hass.states.async_set("sensor.t1", "22.0", CELSIUS)
    await advance(hass, freezer, 10)
    assert value_writes(fake_gateway) == [2200, 2200]

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get("sensor.living_heating_state").state == "unavailable"

    fake_gateway.writes.clear()
    fake_gateway.reads.clear()
    hass.states.async_set("sensor.t1", "23.5", CELSIUS)
    hass.states.async_set("climate.a", "unavailable")
    await advance(hass, freezer, 130)
    assert fake_gateway.writes == []
    assert fake_gateway.reads == []


# --- Resolved settings and migration ---------------------------------------

HYSTERESIS = 0x6011
ADAPTIVE = 0x6017


def attribute_writes(gateway, attribute: int) -> list[int]:
    """Return the raw values written to ``attribute`` so far."""
    return [w[4] for w in gateway.writes if w[3] == attribute]


def migrated_issue(hass):
    return ir.async_get(hass).async_get_issue(DOMAIN, "settings_migrated")


async def test_room_uses_global_hysteresis_when_room_says_global(
    hass, fake_gateway
) -> None:
    options = {**FAST_OPTIONS, "hysteresis": "0_6", "adaptive_mode": "off"}
    entry = make_entry(
        hass, [room_data(trvs=["climate.a"], hysteresis="global")], options
    )
    await start(hass, entry)
    assert attribute_writes(fake_gateway, HYSTERESIS) == [-60]


async def test_a_room_setting_wins_over_the_global_one(hass, fake_gateway) -> None:
    options = {**FAST_OPTIONS, "hysteresis": "0_6", "adaptive_mode": "off"}
    entry = make_entry(hass, [room_data(trvs=["climate.a"], hysteresis="0_2")], options)
    await start(hass, entry)
    assert attribute_writes(fake_gateway, HYSTERESIS) == [-20]


async def test_runtime_data_holds_the_resolved_settings_per_room(hass) -> None:
    options = {**FAST_OPTIONS, "frost_temperature": 9.0}
    entry = make_entry(
        hass,
        [room_data(name="Living"), room_data(name="Bed", frost_temperature=12.0)],
        options,
    )
    await start(hass, entry)
    resolved = entry.runtime_data.settings
    assert set(resolved) == set(entry.subentries)
    by_title = {
        entry.subentries[sid].title: settings for sid, settings in resolved.items()
    }
    assert by_title["Living"].frost_temperature == 9.0
    assert by_title["Bed"].frost_temperature == 12.0
    assert by_title["Living"].verify_delay_seconds == 0


async def test_migrate_v1_entry_turns_leave_into_global_and_raises_issue(
    hass,
) -> None:
    entry = make_entry(
        hass,
        [
            room_data(name="Living", hysteresis="leave"),
            room_data(name="Bed", hysteresis="0_2"),
        ],
        FAST_OPTIONS,
        version=1,
    )
    await start(hass, entry)
    assert entry.version == 2
    living = subentry_of(entry, "Living")
    bed = subentry_of(entry, "Bed")
    assert living.data["hysteresis"] == "global"
    assert bed.data["hysteresis"] == "0_2"
    issue = migrated_issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {"rooms": "Living", "hysteresis": "0.4 K"}
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.is_fixable is False


async def test_migration_names_every_changed_room_and_the_global_value(
    hass,
) -> None:
    options = {**FAST_OPTIONS, "hysteresis": "0_6"}
    entry = make_entry(
        hass,
        [
            room_data(name="Living", hysteresis="leave"),
            room_data(name="Bed", hysteresis="leave"),
        ],
        options,
        version=1,
    )
    await start(hass, entry)
    issue = migrated_issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {
        "rooms": "Living, Bed",
        "hysteresis": "0.6 K",
    }


async def test_migrated_room_with_default_adaptive_writes_no_hysteresis(
    hass, fake_gateway
) -> None:
    entry = make_entry(
        hass,
        [room_data(trvs=["climate.a"], hysteresis="leave")],
        FAST_OPTIONS,
        version=1,
    )
    await start(hass, entry)
    assert attribute_writes(fake_gateway, ADAPTIVE) == [2]
    assert attribute_writes(fake_gateway, HYSTERESIS) == []


async def test_migration_without_leave_rooms_raises_no_issue(hass) -> None:
    entry = make_entry(hass, [room_data(hysteresis="0_4")], FAST_OPTIONS, version=1)
    await start(hass, entry)
    assert entry.version == 2
    assert migrated_issue(hass) is None


async def test_a_version_2_entry_is_not_migrated_again(hass) -> None:
    entry = make_entry(hass, [room_data(hysteresis="leave")], FAST_OPTIONS)
    await start(hass, entry)
    assert entry.version == 2
    assert subentry_of(entry, "Living").data["hysteresis"] == "leave"
    assert migrated_issue(hass) is None


async def test_upgrade_from_0_1_0_with_old_and_new_firmware(hass, fake_gateway) -> None:
    """A 0.1.0 entry: adaptive mode arrives on 1.4.4, 1.3.0 keeps the hysteresis."""
    fake_gateway.add_device("climate.a", "00:11", firmware=(1, 3, 0))
    fake_gateway.add_device("climate.b", "00:22", firmware=(1, 4, 4))
    entry = make_entry(
        hass,
        [
            room_data(name="Living", trvs=["climate.a"], hysteresis="leave"),
            room_data(name="Bed", trvs=["climate.b"], hysteresis="0_2"),
        ],
        FAST_OPTIONS,
        version=1,
    )
    await start(hass, entry)

    def writes(ieee: str) -> list[tuple[int, int]]:
        return [(w[3], w[4]) for w in fake_gateway.writes if w[0] == ieee]

    # Firmware 1.3.0: adaptive mode is refused, then the hysteresis follows once.
    assert writes("00:11") == [
        (0x6000, 1),
        (0x6002, 500),
        (0x600E, 1),
        (VALUE, 2130),
        (HYSTERESIS, -40),
    ]
    # Firmware 1.4.4: adaptive mode manages the hysteresis, nothing is written.
    assert writes("00:22") == [
        (0x6000, 1),
        (ADAPTIVE, 2),
        (0x6002, 500),
        (0x600E, 1),
        (VALUE, 2130),
    ]

    issue = migrated_issue(hass)
    assert issue is not None
    assert issue.translation_placeholders == {"rooms": "Living", "hysteresis": "0.4 K"}
    firmware = ir.async_get(hass).async_get_issue(DOMAIN, "trv_firmware_0011")
    assert firmware is not None
    assert firmware.translation_placeholders["setting"] == "Adaptive mode"
    assert firmware.translation_placeholders["required"] == "1.4.4"
    assert firmware.translation_placeholders["current"] == "1.3.0"
    assert ir.async_get(hass).async_get_issue(DOMAIN, "trv_firmware_0022") is None
    assert (
        hass.states.get("sensor.living_heating_state").state == "restricted_unsupported"
    )
    assert hass.states.get("sensor.bed_heating_state").state == "heating"


async def test_migration_notice_uses_the_decimal_comma_in_german(hass) -> None:
    hass.config.language = "de"
    entry = make_entry(hass, [room_data(hysteresis="leave")], FAST_OPTIONS, version=1)
    await start(hass, entry)
    issue = migrated_issue(hass)
    assert issue is not None
    assert issue.translation_placeholders["hysteresis"] == "0,4 K"


async def test_user_profile_without_adaptive_and_frost_is_no_firmware_problem(
    hass, tmp_path, fake_gateway
) -> None:
    """A profile copied from 0.1.0 has neither capability: a gap, not a defect."""
    text = BUILTIN_TEXT.replace("id: sonoff_trvzb_native", "id: user_zbt", 1)
    text = text.replace("model: TRVZB", "model: TRV-ZBT", 1)
    text = text[: text.index("  adaptive_mode:")]
    use_config_dir(hass, tmp_path).joinpath("zbt.yaml").write_text(text)
    fake_gateway.add_device("climate.x", "00:33", model="TRV-ZBT", firmware=FIRMWARE)
    hass.states.async_set("climate.x", "heat")
    entry = make_entry(hass, [room_data(trvs=["climate.x"])], FAST_OPTIONS)

    await start(hass, entry)

    assert ir.async_get(hass).async_get_issue(DOMAIN, "trv_firmware_0033") is None
    assert hass.states.get("sensor.living_heating_state").state == "heating"
    assert attribute_writes(fake_gateway, ADAPTIVE) == []
    assert attribute_writes(fake_gateway, 0x6002) == []
    assert attribute_writes(fake_gateway, HYSTERESIS) == [-40]
    (room,) = entry.runtime_data.rooms.values()
    assert room.snapshot()["not_in_profile"] == {
        "climate.x": ["set_adaptive_mode", "set_frost_temperature"]
    }


async def test_disjoint_setpoint_range_invalidates_the_room(hass) -> None:
    entry = make_entry(
        hass,
        [room_data(setpoint_range="manual", setpoint_min=36.0, setpoint_max=40.0)],
        FAST_OPTIONS,
    )

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.rooms == {}
    issue = room_issue(hass, subentry_of(entry, "Living").subentry_id)
    assert issue is not None
    assert "setpoint range is empty" in issue.translation_placeholders["error"]
    assert hass.states.get("climate.living_heating") is None


async def test_deleting_a_room_removes_its_climate_entity(hass) -> None:
    entry = make_entry(
        hass,
        [room_data("Living"), room_data("Bedroom", trvs=["climate.b"])],
        FAST_OPTIONS,
    )
    await start(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get("climate.bedroom_heating") is not None

    hass.config_entries.async_remove_subentry(
        entry, subentry_of(entry, "Bedroom").subentry_id
    )
    await settle(hass)

    assert registry.async_get("climate.bedroom_heating") is None
    assert hass.states.get("climate.bedroom_heating") is None
    assert registry.async_get("climate.living_heating") is not None


# --- Areas ----------------------------------------------------------------


def pre_register_room_device(hass, entry, subentry_id: str):
    """Create the room device the way an older version did: without an area.

    ``suggested_area`` only places a device when Home Assistant creates it, so
    a device that exists already stays where it is.
    """
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=subentry_id,
        identifiers={(DOMAIN, subentry_id)},
        manufacturer="Easy TRV",
        name="Living heating",
    )


def room_device(hass, entry, subentry_id: str):
    return dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, subentry_id), entry.entry_id
    )


async def test_setup_puts_room_device_into_its_area(hass) -> None:
    area = ar.async_get(hass).async_create("Wohnzimmer")
    entry = make_entry(hass, [room_data(**{CONF_AREA: area.id})], FAST_OPTIONS)
    subentry_id = next(iter(entry.subentries))
    assert pre_register_room_device(hass, entry, subentry_id).area_id is None

    await start(hass, entry)

    device = room_device(hass, entry, subentry_id)
    assert device is not None
    assert device.area_id == area.id
    # The entities follow their device; Easy TRV gives none an area of its own.
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert {"climate.living_heating", "sensor.living_heating_state"} <= {
        e.entity_id for e in entities
    }
    assert {e.area_id for e in entities} == {None}


async def test_setup_keeps_a_device_area_set_by_hand(hass) -> None:
    areas = ar.async_get(hass)
    room_area = areas.async_create("Wohnzimmer")
    hand_area = areas.async_create("Flur")
    entry = make_entry(hass, [room_data(**{CONF_AREA: room_area.id})], FAST_OPTIONS)
    subentry_id = next(iter(entry.subentries))
    device = pre_register_room_device(hass, entry, subentry_id)
    dr.async_get(hass).async_update_device(device.id, area_id=hand_area.id)

    await start(hass, entry)

    device = room_device(hass, entry, subentry_id)
    assert device is not None
    assert device.area_id == hand_area.id


async def test_setup_ignores_a_deleted_area(hass) -> None:
    areas = ar.async_get(hass)
    area = areas.async_create("Wohnzimmer")
    entry = make_entry(hass, [room_data(**{CONF_AREA: area.id})], FAST_OPTIONS)
    subentry_id = next(iter(entry.subentries))
    pre_register_room_device(hass, entry, subentry_id)
    areas.async_delete(area.id)

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    device = room_device(hass, entry, subentry_id)
    assert device is not None
    assert device.area_id is None
    assert not areas.async_list_areas()


async def test_setup_leaves_a_room_without_an_area_alone(hass) -> None:
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)
    subentry_id = next(iter(entry.subentries))
    pre_register_room_device(hass, entry, subentry_id)

    await start(hass, entry)

    device = room_device(hass, entry, subentry_id)
    assert device is not None
    assert device.area_id is None
