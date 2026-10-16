"""Hiding the thermostat entities Easy TRV controls (``visibility.py``)."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.easy_trv.const import DOMAIN
from custom_components.easy_trv.profiles.loader import load_profiles_sync
from custom_components.easy_trv.visibility import (
    MARKER,
    controlled_entities,
    reconcile_hidden,
    show_all,
)
from tests.helpers_entry import (
    FAST_OPTIONS,
    IEEE_A,
    IEEE_B,
    make_entry,
    patched_gateway,
    room_data,
    settle,
    start,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

INTEGRATION = er.RegistryEntryHider.INTEGRATION
USER = er.RegistryEntryHider.USER
HIDE_ON = {**FAST_OPTIONS, "hide_thermostat_entities": "on"}
HIDE_BOTH = {**HIDE_ON, "statistics": "hidden"}
NATIVE = load_profiles_sync(None).profiles["sonoff_trvzb_native"]

# What Easy TRV writes to a TRVZB and what ZHA calls the entity: (domain, key).
CONTROLLED = [
    ("number", "external_temperature_sensor_value"),
    ("switch", "external_temperature_sensor"),
    ("switch", "open_window"),
    ("number", "temperature_control_accuracy"),
    ("switch", "adaptive_mode"),
    ("number", "frost_protection_temperature"),
]
# What Easy TRV leaves alone: other settings, a sensor, an entity without a key.
OTHERS = [
    ("switch", "child_lock"),
    ("switch", "boost_mode"),
    ("sensor", None),  # the battery
    ("number", None),
]


@dataclass
class Trv:
    """The registry entries of one thermostat."""

    device_id: str
    climate: str
    controlled: set[str] = field(default_factory=set)
    others: set[str] = field(default_factory=set)

    @property
    def hidden(self) -> set[str]:
        """What a room hides for this thermostat: the climate and the six."""
        return {self.climate, *self.controlled}


@pytest.fixture(autouse=True)
def fake_gateway(hass):
    with patched_gateway(hass) as gateway:
        yield gateway


@pytest.fixture
def owner(hass: HomeAssistant) -> MockConfigEntry:
    """The config entry that owns the devices; ZHA itself is not set up."""
    entry = MockConfigEntry(domain="test")
    entry.add_to_hass(hass)
    return entry


def add_trv(hass: HomeAssistant, owner: MockConfigEntry, name: str, ieee: str) -> Trv:
    """Register a ZHA thermostat: a device, its climate entity and its entities."""
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id,
        identifiers={("zha", ieee)},
        manufacturer="SONOFF",
        model="TRVZB",
        name=f"TRV {name}",
    )
    # The fake gateway's state holds the entity id; the registry would avoid it.
    climate_id = f"climate.{name}"
    state = hass.states.get(climate_id)
    hass.states.async_remove(climate_id)
    climate = er.async_get(hass).async_get_or_create(
        "climate",
        "zha",
        f"{ieee}-climate",
        suggested_object_id=name,
        device_id=device.id,
    )
    assert climate.entity_id == climate_id
    if state is not None:
        hass.states.async_set(climate_id, state.state, state.attributes)
    trv = Trv(device.id, climate_id)
    for domain, key in CONTROLLED:
        entity = add_entity(hass, trv, name, domain, key)
        trv.controlled.add(entity.entity_id)
    for index, (domain, key) in enumerate(OTHERS):
        entity = add_entity(hass, trv, name, domain, key, index)
        trv.others.add(entity.entity_id)
    return trv


def add_entity(  # noqa: PLR0913 - the fields of a registry entry
    hass: HomeAssistant,
    trv: Trv,
    name: str,
    domain: str,
    key: str | None,
    index: int = 0,
    platform: str = "zha",
) -> er.RegistryEntry:
    """Register one more entity on the device of ``trv``."""
    label = key or f"plain{index}"
    return er.async_get(hass).async_get_or_create(
        domain,
        platform,
        f"{trv.device_id}-{platform}-{domain}-{label}",
        suggested_object_id=f"{name}_{platform}_{label}",
        device_id=trv.device_id,
        translation_key=key,
    )


@pytest.fixture
def trv_a(hass, owner) -> Trv:
    return add_trv(hass, owner, "a", IEEE_A)


@pytest.fixture
def trv_b(hass, owner) -> Trv:
    return add_trv(hass, owner, "b", IEEE_B)


def entry_of(hass: HomeAssistant, entity_id: str) -> er.RegistryEntry:
    entry = er.async_get(hass).async_get(entity_id)
    assert entry is not None
    return entry


def is_marked(entry: er.RegistryEntry) -> bool:
    return entry.options.get(DOMAIN) == MARKER


def is_untouched(entry: er.RegistryEntry) -> bool:
    return entry.hidden_by is None and DOMAIN not in entry.options


def hidden_by_integration(hass: HomeAssistant) -> set[str]:
    registry = er.async_get(hass)
    return {
        e.entity_id for e in registry.entities.values() if e.hidden_by is INTEGRATION
    }


def marked_entities(hass: HomeAssistant) -> set[str]:
    registry = er.async_get(hass)
    return {e.entity_id for e in registry.entities.values() if is_marked(e)}


def set_options(hass: HomeAssistant, entry: MockConfigEntry, **options: Any) -> None:
    hass.config_entries.async_update_entry(entry, options={**FAST_OPTIONS, **options})


# --- Which entities are controlled ------------------------------------------------


def test_the_marker_is_the_hidden_flag_of_the_domain() -> None:
    assert MARKER == {"hidden": True}


def test_controlled_entities_are_the_climate_and_the_six(hass, trv_a, trv_b) -> None:
    assert controlled_entities(hass, [trv_a.climate], NATIVE) == trv_a.hidden
    assert controlled_entities(hass, [trv_a.climate, trv_b.climate], NATIVE) == (
        trv_a.hidden | trv_b.hidden
    )
    assert controlled_entities(hass, [], NATIVE) == set()


def test_other_platform_or_no_translation_key_not_matched(hass, trv_a) -> None:
    # The same domain and key as a controlled entity, but another integration.
    other_platform = add_entity(
        hass, trv_a, "a", "number", "frost_protection_temperature", platform="x"
    )
    # A controlled key in the wrong domain, and a number without any key.
    wrong_domain = add_entity(hass, trv_a, "a", "sensor", "adaptive_mode")
    no_key = add_entity(hass, trv_a, "a", "number", None, index=9)
    assert no_key.translation_key is None

    controlled = controlled_entities(hass, [trv_a.climate], NATIVE)

    assert controlled == trv_a.hidden
    for entity in (other_platform, wrong_domain, no_key):
        assert entity.entity_id not in controlled


def test_controlled_entities_include_disabled_ones(hass, trv_a) -> None:
    disabled = er.async_get(hass).async_get_or_create(
        "number",
        "zha",
        "disabled-accuracy",
        device_id=trv_a.device_id,
        translation_key="temperature_control_accuracy",
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )

    assert disabled.entity_id in controlled_entities(hass, [trv_a.climate], NATIVE)


def test_entities_of_other_devices_are_not_controlled(hass, trv_a, trv_b) -> None:
    assert controlled_entities(hass, [trv_a.climate], NATIVE).isdisjoint(trv_b.hidden)


@pytest.mark.usefixtures("trv_a")
def test_a_climate_without_a_device_contributes_only_itself(hass) -> None:
    lone = er.async_get(hass).async_get_or_create(
        "climate", "zha", "lone", suggested_object_id="lone"
    )
    assert lone.device_id is None

    controlled = controlled_entities(hass, [lone.entity_id], NATIVE)

    assert controlled == {lone.entity_id}


def test_a_climate_that_is_not_registered_contributes_nothing(hass, trv_a) -> None:
    assert controlled_entities(hass, ["climate.ghost", trv_a.climate], NATIVE) == (
        trv_a.hidden
    )


# --- Setup hides what is controlled -----------------------------------------------


async def test_hide_controlled_entities(hass, trv_a) -> None:
    entry = make_entry(hass, [room_data(trvs=["climate.a"])], HIDE_ON)

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    # Exactly the climate and the six, with the marker; nothing else of the TRV
    # and none of Easy TRV's own entities.
    assert hidden_by_integration(hass) == trv_a.hidden
    assert marked_entities(hass) == trv_a.hidden
    for entity_id in trv_a.others:
        assert is_untouched(entry_of(hass, entity_id)), entity_id
    own = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert own
    assert all(is_untouched(e) for e in own)


async def test_hide_covers_every_thermostat_of_the_room(hass, trv_a, trv_b) -> None:
    entry = make_entry(hass, [room_data()], HIDE_ON)

    await start(hass, entry)

    assert hidden_by_integration(hass) == trv_a.hidden | trv_b.hidden
    assert marked_entities(hass) == trv_a.hidden | trv_b.hidden


@pytest.mark.usefixtures("trv_a")
async def test_hidden_entities_keep_working(hass) -> None:
    entry = make_entry(hass, [room_data(trvs=["climate.a"])], HIDE_ON)

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.living_heating_state").state == "heating"
    assert hass.states.get("climate.living_heating") is not None


@pytest.mark.usefixtures("trv_a", "trv_b")
async def test_nothing_is_hidden_by_default(hass) -> None:
    entry = make_entry(hass, [room_data()], FAST_OPTIONS)

    await start(hass, entry)

    assert hidden_by_integration(hass) == set()
    assert marked_entities(hass) == set()


@pytest.mark.parametrize(
    ("options", "room", "hidden"),
    [
        ({"hide_thermostat_entities": "on"}, {}, True),
        (
            {"hide_thermostat_entities": "on"},
            {"hide_thermostat_entities": "global"},
            True,
        ),
        (
            {"hide_thermostat_entities": "on"},
            {"hide_thermostat_entities": "off"},
            False,
        ),
        ({}, {"hide_thermostat_entities": "on"}, True),
        ({"hide_thermostat_entities": "off"}, {"hide_thermostat_entities": "on"}, True),
        ({}, {"hide_thermostat_entities": "off"}, False),
    ],
)
async def test_the_room_setting_overrides_the_global_one(
    hass, trv_a, options, room, hidden
) -> None:
    entry = make_entry(
        hass,
        [room_data(trvs=["climate.a"], **room)],
        {**FAST_OPTIONS, **options},
    )

    await start(hass, entry)

    assert hidden_by_integration(hass) == (trv_a.hidden if hidden else set())


@pytest.mark.usefixtures("trv_b")
async def test_only_rooms_with_the_setting_on_hide(hass, trv_a) -> None:
    entry = make_entry(
        hass,
        [
            room_data("Living", trvs=["climate.a"]),
            room_data("Bedroom", trvs=["climate.b"], hide_thermostat_entities="off"),
        ],
        HIDE_ON,
    )

    await start(hass, entry)

    assert hidden_by_integration(hass) == trv_a.hidden


# --- What is left to the thermostat stays visible --------------------------------

OPEN_WINDOW = "switch.a_zha_open_window"
ADAPTIVE_MODE = "switch.a_zha_adaptive_mode"


async def test_leave_window_detection_keeps_open_window_visible(hass, trv_a) -> None:
    entry = make_entry(
        hass, [room_data(trvs=["climate.a"], window_detection="leave")], HIDE_ON
    )

    await start(hass, entry)

    # The person controls the flag: everything else is hidden as before.
    assert OPEN_WINDOW in trv_a.hidden
    assert hidden_by_integration(hass) == trv_a.hidden - {OPEN_WINDOW}
    assert marked_entities(hass) == trv_a.hidden - {OPEN_WINDOW}
    assert is_untouched(entry_of(hass, OPEN_WINDOW))


async def test_leave_adaptive_mode_keeps_adaptive_switch_visible(hass, trv_a) -> None:
    entry = make_entry(
        hass, [room_data(trvs=["climate.a"], adaptive_mode="leave")], HIDE_ON
    )

    await start(hass, entry)

    assert ADAPTIVE_MODE in trv_a.hidden
    assert hidden_by_integration(hass) == trv_a.hidden - {ADAPTIVE_MODE}
    assert marked_entities(hass) == trv_a.hidden - {ADAPTIVE_MODE}
    assert is_untouched(entry_of(hass, ADAPTIVE_MODE))


async def test_leave_in_the_global_options_counts_for_a_room_that_follows_them(
    hass, trv_a
) -> None:
    entry = make_entry(
        hass,
        [room_data(trvs=["climate.a"], window_detection="global")],
        {**HIDE_ON, "window_detection": "leave", "adaptive_mode": "leave"},
    )

    await start(hass, entry)

    assert hidden_by_integration(hass) == trv_a.hidden - {OPEN_WINDOW, ADAPTIVE_MODE}


async def test_open_window_is_shown_again_when_the_room_switches_to_leave(
    hass, trv_a
) -> None:
    entry = make_entry(hass, [room_data(trvs=["climate.a"])], HIDE_ON)
    await start(hass, entry)
    assert hidden_by_integration(hass) == trv_a.hidden
    assert is_marked(entry_of(hass, OPEN_WINDOW))
    subentry = next(iter(entry.subentries.values()))

    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data=room_data(trvs=["climate.a"], window_detection="leave"),
    )
    await settle(hass)

    assert hidden_by_integration(hass) == trv_a.hidden - {OPEN_WINDOW}
    assert marked_entities(hass) == trv_a.hidden - {OPEN_WINDOW}
    assert is_untouched(entry_of(hass, OPEN_WINDOW))


def test_controlled_entities_skip_the_named_capabilities(hass, trv_a) -> None:
    controlled = controlled_entities(
        hass, [trv_a.climate], NATIVE, skip=("window_detection_flag", "adaptive_mode")
    )

    assert controlled == trv_a.hidden - {OPEN_WINDOW, ADAPTIVE_MODE}
    assert controlled_entities(hass, [trv_a.climate], NATIVE, skip=()) == trv_a.hidden


# --- The person's choices stay ----------------------------------------------------


async def test_user_unhide_is_respected(hass, trv_a) -> None:
    entry = make_entry(hass, [room_data(trvs=["climate.a"])], HIDE_ON)
    await start(hass, entry)
    registry = er.async_get(hass)
    registry.async_update_entity(trv_a.climate, hidden_by=None)
    registry.async_update_entity(
        "number.a_zha_frost_protection_temperature", hidden_by=None
    )

    await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)

    # Still visible, still marked: Easy TRV hid them once and does not again.
    for entity_id in (trv_a.climate, "number.a_zha_frost_protection_temperature"):
        shown = entry_of(hass, entity_id)
        assert shown.hidden_by is None
        assert is_marked(shown)
    assert hidden_by_integration(hass) == trv_a.hidden - {
        trv_a.climate,
        "number.a_zha_frost_protection_temperature",
    }


async def test_user_hidden_entity_untouched(hass, trv_a) -> None:
    registry = er.async_get(hass)
    registry.async_update_entity(trv_a.climate, hidden_by=USER)
    entry = make_entry(hass, [room_data(trvs=["climate.a"])], HIDE_ON)

    await start(hass, entry)

    # The person hid it: no marker, no change.
    climate = entry_of(hass, trv_a.climate)
    assert climate.hidden_by is USER
    assert DOMAIN not in climate.options
    assert hidden_by_integration(hass) == trv_a.controlled

    set_options(hass, entry)
    await settle(hass)

    assert entry_of(hass, trv_a.climate).hidden_by is USER
    assert DOMAIN not in entry_of(hass, trv_a.climate).options
    assert hidden_by_integration(hass) == set()


async def test_an_entity_hidden_by_someone_else_is_never_shown(hass, trv_a) -> None:
    registry = er.async_get(hass)
    registry.async_update_entity(trv_a.climate, hidden_by=INTEGRATION)
    entry = make_entry(hass, [room_data(trvs=["climate.a"])], FAST_OPTIONS)

    await start(hass, entry)

    # Hidden by another integration, no marker of ours: not ours to show.
    assert entry_of(hass, trv_a.climate).hidden_by is INTEGRATION


async def test_hiding_by_hand_after_easy_trv_stays_hidden_when_it_lets_go(
    hass, trv_a
) -> None:
    entry = make_entry(hass, [room_data(trvs=["climate.a"])], HIDE_ON)
    await start(hass, entry)
    er.async_get(hass).async_update_entity(trv_a.climate, hidden_by=USER)

    set_options(hass, entry)
    await settle(hass)

    climate = entry_of(hass, trv_a.climate)
    assert climate.hidden_by is USER
    assert DOMAIN not in climate.options
    assert marked_entities(hass) == set()


# --- Easy TRV lets go -------------------------------------------------------------


async def test_setting_off_shows_again(hass, trv_a) -> None:
    entry = make_entry(hass, [room_data(trvs=["climate.a"])], HIDE_ON)
    await start(hass, entry)
    assert hidden_by_integration(hass) == trv_a.hidden

    set_options(hass, entry, hide_thermostat_entities="off")
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert hidden_by_integration(hass) == set()
    assert marked_entities(hass) == set()
    for entity_id in trv_a.hidden:
        assert is_untouched(entry_of(hass, entity_id)), entity_id


@pytest.mark.usefixtures("trv_a")
async def test_the_room_setting_off_shows_again(hass) -> None:
    entry = make_entry(hass, [room_data(trvs=["climate.a"])], HIDE_ON)
    await start(hass, entry)
    subentry = next(iter(entry.subentries.values()))

    hass.config_entries.async_update_subentry(
        entry,
        subentry,
        data=room_data(trvs=["climate.a"], hide_thermostat_entities="off"),
    )
    await settle(hass)

    assert hidden_by_integration(hass) == set()
    assert marked_entities(hass) == set()


async def test_room_removed_shows_again(hass, trv_a, trv_b) -> None:
    entry = make_entry(
        hass,
        [
            room_data("Living", trvs=["climate.a"]),
            room_data("Bedroom", trvs=["climate.b"]),
        ],
        HIDE_ON,
    )
    await start(hass, entry)
    assert hidden_by_integration(hass) == trv_a.hidden | trv_b.hidden
    bedroom = next(s for s in entry.subentries.values() if s.title == "Bedroom")

    hass.config_entries.async_remove_subentry(entry, bedroom.subentry_id)
    await settle(hass)

    assert hidden_by_integration(hass) == trv_a.hidden
    assert marked_entities(hass) == trv_a.hidden
    for entity_id in trv_b.hidden:
        assert is_untouched(entry_of(hass, entity_id)), entity_id


async def test_thermostat_removed_from_room_shows_again(hass, trv_a, trv_b) -> None:
    entry = make_entry(hass, [room_data()], HIDE_ON)
    await start(hass, entry)
    assert hidden_by_integration(hass) == trv_a.hidden | trv_b.hidden
    subentry = next(iter(entry.subentries.values()))

    hass.config_entries.async_update_subentry(
        entry, subentry, data=room_data(trvs=["climate.a"])
    )
    await settle(hass)

    assert hidden_by_integration(hass) == trv_a.hidden
    assert marked_entities(hass) == trv_a.hidden
    for entity_id in trv_b.hidden:
        assert is_untouched(entry_of(hass, entity_id)), entity_id


async def test_entry_removal_shows_all(hass, trv_a, trv_b) -> None:
    entry = make_entry(
        hass,
        [
            room_data("Living", trvs=["climate.a"]),
            room_data("Bedroom", trvs=["climate.b"]),
        ],
        HIDE_ON,
    )
    await start(hass, entry)
    assert hidden_by_integration(hass) == trv_a.hidden | trv_b.hidden
    # One the person hid by hand after Easy TRV did: it stays hidden.
    er.async_get(hass).async_update_entity(trv_b.climate, hidden_by=USER)

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert hidden_by_integration(hass) == set()
    assert marked_entities(hass) == set()
    assert entry_of(hass, trv_b.climate).hidden_by is USER
    for entity_id in trv_a.hidden | trv_b.hidden:
        assert DOMAIN not in entry_of(hass, entity_id).options, entity_id


# --- A room that cannot be set up -------------------------------------------------


async def test_failed_room_keeps_hidden_entities(hass, trv_a, trv_b) -> None:
    entry = make_entry(
        hass,
        [
            room_data("Living", trvs=["climate.a"]),
            room_data("Bedroom", trvs=["climate.b"]),
        ],
        HIDE_ON,
    )
    await start(hass, entry)
    assert hidden_by_integration(hass) == trv_a.hidden | trv_b.hidden
    bedroom = next(s for s in entry.subentries.values() if s.title == "Bedroom")

    # Corrupt data: the room is invalid now, but its thermostat is still there.
    hass.config_entries.async_update_subentry(
        entry, bedroom, data=room_data("Bedroom", trvs=["climate.b"], hysteresis="x")
    )
    await settle(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.rooms.keys() == {
        next(s for s in entry.subentries.values() if s.title == "Living").subentry_id
    }
    assert hidden_by_integration(hass) == trv_a.hidden | trv_b.hidden
    assert marked_entities(hass) == trv_a.hidden | trv_b.hidden


async def test_failed_room_that_becomes_valid_hides_again(hass, trv_a) -> None:
    entry = make_entry(hass, [room_data(trvs=["climate.a"], hysteresis="x")], HIDE_ON)

    await start(hass, entry)

    # Never set up: nothing hidden yet.
    assert entry.runtime_data.rooms == {}
    assert hidden_by_integration(hass) == set()

    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(
        entry, subentry, data=room_data(trvs=["climate.a"])
    )
    await settle(hass)

    assert hidden_by_integration(hass) == trv_a.hidden


@pytest.mark.parametrize("trvs", [5, None, "climate.a", ["climate.a", 5, None]])
@pytest.mark.usefixtures("trv_b")
async def test_corrupt_thermostat_data_does_not_break_setup(hass, trv_a, trvs) -> None:
    entry = make_entry(
        hass,
        [room_data("Living", trvs=["climate.a"]), room_data("Bedroom", trvs=trvs)],
        HIDE_ON,
    )

    await start(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    # The healthy room hides its thermostat; the broken one is skipped.
    assert hidden_by_integration(hass) == trv_a.hidden


# --- The statistics sensors are hidden in the same pass ---------------------------


def statistics_of(room: str, *thermostats: str) -> set[str]:
    """Return the statistics sensors of a room without window contacts."""
    return {
        f"sensor.{room}_deviation",
        f"sensor.{room}_heating_time",
        *(
            f"sensor.{trv}_{key}"
            for trv in thermostats
            for key in ("writes", "write_failures", "dropouts")
        ),
    }


async def test_hiding_the_thermostats_and_the_statistics_work_together(
    hass, trv_a
) -> None:
    statistics = statistics_of("living", "a")
    entry = make_entry(hass, [room_data(trvs=["climate.a"])], HIDE_BOTH)

    await start(hass, entry)

    # One pass for both: neither undoes the other.
    assert hidden_by_integration(hass) == trv_a.hidden | statistics
    assert marked_entities(hass) == trv_a.hidden | statistics

    set_options(hass, entry, hide_thermostat_entities="on", statistics="visible")
    await settle(hass)

    assert hidden_by_integration(hass) == trv_a.hidden
    assert marked_entities(hass) == trv_a.hidden

    set_options(hass, entry, hide_thermostat_entities="off", statistics="hidden")
    await settle(hass)

    assert hidden_by_integration(hass) == statistics
    assert marked_entities(hass) == statistics
    for entity_id in trv_a.hidden:
        assert is_untouched(entry_of(hass, entity_id)), entity_id

    set_options(hass, entry, hide_thermostat_entities="on", statistics="hidden")
    await settle(hass)

    assert hidden_by_integration(hass) == trv_a.hidden | statistics


async def test_a_failed_room_keeps_its_thermostats_and_statistics_hidden(
    hass, trv_a, trv_b
) -> None:
    rooms = [
        room_data("Living", trvs=["climate.a"]),
        room_data("Bedroom", trvs=["climate.b"]),
    ]
    entry = make_entry(hass, rooms, HIDE_BOTH)
    await start(hass, entry)
    everything = (
        trv_a.hidden
        | trv_b.hidden
        | statistics_of("living", "a")
        | statistics_of("bedroom", "b")
    )
    assert hidden_by_integration(hass) == everything
    bedroom = next(s for s in entry.subentries.values() if s.title == "Bedroom")

    hass.config_entries.async_update_subentry(
        entry, bedroom, data={**rooms[1], "hysteresis": "x"}
    )
    await settle(hass)

    assert entry.runtime_data.rooms.keys() == {
        next(s for s in entry.subentries.values() if s.title == "Living").subentry_id
    }
    assert hidden_by_integration(hass) == everything
    assert marked_entities(hass) == everything


def test_reconcile_marks_an_entity_of_ours_registered_hidden_in_this_setup(
    hass, freezer
) -> None:
    registry = er.async_get(hass)
    # Hidden by another integration (the group helper's "Hide members") before
    # this setup, so it is not Easy TRV's.
    earlier = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        "earlier",
        suggested_object_id="earlier",
        hidden_by=INTEGRATION,
    )
    freezer.tick(1)
    started = dt_util.utcnow()
    freezer.tick(1)
    ours = registry.async_get_or_create(
        "sensor", DOMAIN, "ours", suggested_object_id="ours", hidden_by=INTEGRATION
    )
    theirs = registry.async_get_or_create(
        "sensor", "other", "theirs", suggested_object_id="theirs", hidden_by=INTEGRATION
    )
    wanted = {earlier.entity_id, ours.entity_id, theirs.entity_id}

    reconcile_hidden(hass, wanted, set(), started=started)

    # Ours was registered hidden by this setup: Easy TRV's to show later. The
    # others were hidden by someone else.
    assert is_marked(entry_of(hass, ours.entity_id))
    assert entry_of(hass, ours.entity_id).hidden_by is INTEGRATION
    for left_alone in (earlier, theirs):
        assert not is_marked(entry_of(hass, left_alone.entity_id))
        assert entry_of(hass, left_alone.entity_id).hidden_by is INTEGRATION

    # Not wanted any more: ours is shown again, the others are left alone.
    reconcile_hidden(hass, set(), set(), started=started)

    assert entry_of(hass, ours.entity_id).hidden_by is None
    assert DOMAIN not in entry_of(hass, ours.entity_id).options
    for left_alone in (earlier, theirs):
        assert entry_of(hass, left_alone.entity_id).hidden_by is INTEGRATION


def test_reconcile_adopts_nothing_without_a_start_time(hass) -> None:
    ours = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, "ours", suggested_object_id="ours", hidden_by=INTEGRATION
    )

    reconcile_hidden(hass, {ours.entity_id}, set())

    assert not is_marked(entry_of(hass, ours.entity_id))
    assert entry_of(hass, ours.entity_id).hidden_by is INTEGRATION


def test_reconcile_leaves_an_entity_of_ours_the_user_hid_alone(hass) -> None:
    mine = er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, "mine", suggested_object_id="mine", hidden_by=USER
    )

    reconcile_hidden(hass, {mine.entity_id}, set())

    assert entry_of(hass, mine.entity_id).hidden_by is USER
    assert not is_marked(entry_of(hass, mine.entity_id))


# --- Reconcile and show_all on their own ------------------------------------------


def test_reconcile_hides_marks_and_is_stable(hass, trv_a) -> None:
    reconcile_hidden(hass, trv_a.hidden, set())

    for entity_id in trv_a.hidden:
        entry = entry_of(hass, entity_id)
        assert entry.hidden_by is INTEGRATION
        assert is_marked(entry)

    # A second pass changes nothing.
    reconcile_hidden(hass, trv_a.hidden, set())
    assert hidden_by_integration(hass) == trv_a.hidden


def test_reconcile_ignores_an_entity_that_is_not_registered(hass, trv_a) -> None:
    reconcile_hidden(hass, {"number.ghost", trv_a.climate}, set())

    assert hidden_by_integration(hass) == {trv_a.climate}


def test_reconcile_leaves_the_entities_of_kept_devices_alone(
    hass, trv_a, trv_b
) -> None:
    reconcile_hidden(hass, trv_a.hidden | trv_b.hidden, set())

    # Neither is wanted any more, but the device of b belongs to a failed room.
    reconcile_hidden(hass, set(), {trv_b.device_id})

    assert hidden_by_integration(hass) == trv_b.hidden
    assert marked_entities(hass) == trv_b.hidden
    for entity_id in trv_a.hidden:
        assert is_untouched(entry_of(hass, entity_id)), entity_id


def test_show_all_shows_every_marked_entity(hass, trv_a, trv_b) -> None:
    reconcile_hidden(hass, trv_a.hidden | trv_b.hidden, set())
    er.async_get(hass).async_update_entity(trv_b.climate, hidden_by=USER)
    er.async_get(hass).async_update_entity(trv_a.climate, hidden_by=None)

    show_all(hass)

    assert hidden_by_integration(hass) == set()
    assert marked_entities(hass) == set()
    # The person's own choices are not undone.
    assert entry_of(hass, trv_b.climate).hidden_by is USER
    assert entry_of(hass, trv_a.climate).hidden_by is None
