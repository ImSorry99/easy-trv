"""Shared helpers for the config-entry setup tests (not a conftest on purpose)."""

import asyncio
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock, patch

from homeassistant import loader
from homeassistant.config_entries import ConfigFlow, ConfigSubentryData
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_config_flow,
)

from custom_components.easy_trv.const import (
    CONF_HUMIDITY_SENSORS,
    CONF_HYSTERESIS,
    CONF_NAME,
    CONF_PROFILE,
    CONF_TEMP_SENSORS,
    CONF_TRVS,
    CONF_WINDOW_DETECTION,
    DOMAIN,
    SUBENTRY_ROOM,
)
from tests.fake_gateway import FakeGateway

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant

GATEWAY_FACTORY = "custom_components.easy_trv.GATEWAY_FACTORY"
CONFIG_FLOW_FILE = (
    Path(__file__).parent.parent / "custom_components" / "easy_trv" / "config_flow.py"
)
CELSIUS = {"unit_of_measurement": "°C"}
IEEE_A = "00:11"
IEEE_B = "00:22"
# No wait between a write and its read-back, so a test does not sit out 10 s.
FAST_OPTIONS = {"verify_delay_seconds": 0}
# Enough loop iterations for the worker to run a queue of fake-gateway
# operations (each one yields about once) to completion.
SETTLE_ITERATIONS = 50


def room_data(name: str = "Living", **overrides: Any) -> dict[str, Any]:
    """Return the data of a room subentry as the config flow stores it."""
    data: dict[str, Any] = {
        CONF_NAME: name,
        CONF_TRVS: ["climate.a", "climate.b"],
        CONF_TEMP_SENSORS: ["sensor.t1", "sensor.t2"],
        CONF_HUMIDITY_SENSORS: [],
        CONF_PROFILE: "auto",
        CONF_WINDOW_DETECTION: "on",
        CONF_HYSTERESIS: "0.4",
    }
    data.update(overrides)
    return data


def make_entry(
    hass: HomeAssistant,
    rooms: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Add an Easy TRV config entry with one ``room`` subentry per dict."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options=options if options is not None else {},
        title="Easy TRV",
        subentries_data=[
            ConfigSubentryData(
                data=room,
                subentry_type=SUBENTRY_ROOM,
                title=room[CONF_NAME],
                unique_id=room[CONF_NAME].casefold(),
            )
            for room in rooms
        ],
    )
    entry.add_to_hass(hass)
    return entry


def make_gateway(hass: HomeAssistant) -> FakeGateway:
    """Return a gateway with two TRVZB thermostats and matching HA states."""
    gateway = FakeGateway()
    gateway.add_device("climate.a", IEEE_A)
    gateway.add_device("climate.b", IEEE_B)
    hass.states.async_set("climate.a", "heat")
    hass.states.async_set("climate.b", "heat")
    hass.states.async_set("sensor.t1", "21.3", CELSIUS)
    hass.states.async_set("sensor.t2", "20.0", CELSIUS)
    return gateway


class _StandInFlow(ConfigFlow):
    """Registered in place of the real config flow, which does not exist yet."""

    VERSION = 1


@contextmanager
def _stand_in_config_flow(hass: HomeAssistant) -> Iterator[None]:
    """Let config entries of the integration set up although it has no flow.

    Home Assistant imports the ``config_flow`` platform of an integration and
    looks up its flow handler before it sets up one of the integration's
    entries. Both are replaced by stand-ins.
    """
    hass.data[loader.DATA_COMPONENTS][f"{DOMAIN}.config_flow"] = Mock()
    with mock_config_flow(DOMAIN, _StandInFlow):
        yield


@contextmanager
def patched_gateway(hass: HomeAssistant) -> Iterator[FakeGateway]:
    """Make the integration use a pre-filled :class:`FakeGateway`.

    As long as ``config_flow.py`` is missing, a stand-in flow is registered
    as well so that entries can be set up at all.
    """
    gateway = make_gateway(hass)
    with ExitStack() as stack:
        if not CONFIG_FLOW_FILE.exists():
            stack.enter_context(_stand_in_config_flow(hass))
        stack.enter_context(patch(GATEWAY_FACTORY, lambda _hass: gateway))
        yield gateway


def use_config_dir(hass: HomeAssistant, tmp_path: Path) -> Path:
    """Point the config dir at ``tmp_path`` and return the user profiles dir."""
    hass.config.config_dir = str(tmp_path)
    profiles = tmp_path / "easy_trv" / "profiles"
    profiles.mkdir(parents=True)
    return profiles


async def settle(hass: HomeAssistant) -> None:
    """Let callbacks and the controllers' background workers run dry."""
    for _ in range(SETTLE_ITERATIONS):
        await hass.async_block_till_done()
        await asyncio.sleep(0)


async def advance(
    hass: HomeAssistant, freezer: Any, seconds: int, step: int = 5
) -> None:
    """Move frozen time forward in steps so chained timers fire in order."""
    elapsed = 0
    while elapsed < seconds:
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await settle(hass)
        elapsed += step


async def start(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set the entry up, signal that HA has started and let the rooms run."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await settle(hass)
