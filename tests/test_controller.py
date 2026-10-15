import asyncio
import json
import logging
from dataclasses import replace
from datetime import timedelta

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueSeverity
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.easy_trv import issues
from custom_components.easy_trv.adapters.zha_gateway import ZhaUnavailable
from custom_components.easy_trv.const import (
    DOMAIN,
    EVENT_FAILSAFE,
    EVENT_RECOVERED,
    EVENT_SETPOINT_ADOPTED,
    EVENT_STATE_CHANGED,
    EVENT_WRITE_FAILED,
)
from custom_components.easy_trv.controller import RoomController
from custom_components.easy_trv.core.model import (
    Note,
    OperatingMode,
    Preset,
    RoomConfig,
    RoomMode,
    RoomParams,
    RoomStatus,
    TrvConfig,
)
from custom_components.easy_trv.core.room_state import HeatingState
from custom_components.easy_trv.profiles.loader import load_profiles_sync
from tests.fake_gateway import FakeGateway

IEEE_A = "00:11"
IEEE_B = "00:22"
WINDOW = 0x6000
HYSTERESIS = 0x6011
FROST = 0x6002
SOURCE = 0x600E
VALUE = 0x600D
SETPOINT = 0x0012
MODE = 0x001C
RANGE = (4.0, 35.0, 0.5)
CELSIUS = {"unit_of_measurement": "°C"}
PARAMS = replace(RoomParams.defaults(), verify_delay=0.0)
# Enough loop iterations for the worker to run a queue of fake-gateway
# operations (each one yields about once) to completion.
SETTLE_ITERATIONS = 50
# Keyed by intent, like the translations: profiles name capabilities as they like.
LABELS = {
    "set_hysteresis": "Hysteresis",
    "set_room_temperature": "Room temperature",
    "set_window_detection": "Thermostat's open-window detection",
}


class SpyGateway(FakeGateway):
    """FakeGateway that also logs write and read calls in one sequence."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []
        # While set and not open, writes wait: the queue behind them builds up.
        self.gate: asyncio.Event | None = None

    async def write(self, ieee, endpoint, cluster, attribute, value) -> None:
        if self.gate is not None:
            await self.gate.wait()
        self.calls.append("write")
        await super().write(ieee, endpoint, cluster, attribute, value)

    async def read(self, ieee, endpoint, cluster, attributes):
        self.calls.append("read")
        return await super().read(ieee, endpoint, cluster, attributes)


async def settle(hass) -> None:
    """Let Home Assistant callbacks and the controller's worker run dry.

    The worker is a background task, which ``async_block_till_done`` does not
    wait for, so yield to the loop explicitly.
    """
    for _ in range(SETTLE_ITERATIONS):
        await hass.async_block_till_done()
        await asyncio.sleep(0)


async def advance(hass, freezer, seconds, step=5) -> None:
    """Move frozen time forward in steps so chained timers fire in order."""
    elapsed = 0
    while elapsed < seconds:
        freezer.tick(timedelta(seconds=step))
        async_fire_time_changed(hass)
        await settle(hass)
        elapsed += step


def written(gateway):
    """Return the recorded writes as ``(ieee, attribute, value)``."""
    return [(w[0], w[3], w[4]) for w in gateway.writes]


def writes_to(gateway, ieee, attribute):
    """Return the values written to one attribute of one device."""
    return [w[4] for w in gateway.writes if (w[0], w[3]) == (ieee, attribute)]


def health(controller):
    return controller.attributes["trv_health"]


def issue(hass, issue_id):
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id)


# --- Fixtures -------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _states(hass) -> None:
    hass.states.async_set("climate.a", "heat")
    hass.states.async_set("climate.b", "heat")
    hass.states.async_set("sensor.t1", "21.3", CELSIUS)
    hass.states.async_set("sensor.t2", "20.0", CELSIUS)


@pytest.fixture
def gateway() -> SpyGateway:
    gw = SpyGateway()
    gw.add_device("climate.a", IEEE_A, firmware=(1, 3, 0))
    gw.add_device("climate.b", IEEE_B, firmware=(1, 3, 0))
    return gw


@pytest.fixture
def profile():
    return load_profiles_sync(None).profiles["sonoff_trvzb_native"]


@pytest.fixture
def config() -> RoomConfig:
    return RoomConfig(
        "room1",
        "Living",
        (
            TrvConfig("climate.a", IEEE_A, (1, 3, 0)),
            TrvConfig("climate.b", IEEE_B, (1, 3, 0)),
        ),
        ("sensor.t1", "sensor.t2"),
        "on",
        0.4,
        "leave",
        7.0,
    )


@pytest.fixture
async def make_controller(hass, gateway, profile, config):
    """Build controllers for the room and stop them again after the test."""
    created: list[RoomController] = []

    def make(
        *, cfg=None, params=PARAMS, prof=None, labels=LABELS, outdoor_sensor=None
    ) -> RoomController:
        refs = {ref.entity_id: ref for ref in gateway.devices.values()}
        controller = RoomController(
            hass,
            cfg or config,
            params,
            prof or profile,
            gateway,
            refs,
            labels=labels,
            outdoor_sensor=outdoor_sensor,
        )
        created.append(controller)
        return controller

    yield make
    for controller in created:
        await controller.async_stop()


@pytest.fixture
def controller(make_controller) -> RoomController:
    return make_controller()


@pytest.fixture
def controller_with_delay(make_controller) -> RoomController:
    return make_controller(params=replace(PARAMS, verify_delay=10.0))


@pytest.fixture
def events(hass):
    return async_capture_events(hass, EVENT_FAILSAFE)


# --- Start, sensors, debounce ---------------------------------------------


async def test_start_writes_settings_source_and_value_in_order(
    hass, controller, gateway
) -> None:
    await controller.async_start()
    await settle(hass)
    assert written(gateway) == [
        (IEEE_A, WINDOW, 1),
        (IEEE_A, HYSTERESIS, -40),
        (IEEE_A, FROST, 700),
        (IEEE_B, WINDOW, 1),
        (IEEE_B, HYSTERESIS, -40),
        (IEEE_B, FROST, 700),
        (IEEE_A, SOURCE, 1),
        (IEEE_B, SOURCE, 1),
        (IEEE_A, VALUE, 2130),
        (IEEE_B, VALUE, 2130),
    ]
    assert controller.status == RoomStatus.OK
    assert controller.attributes["active_sensor"] == "sensor.t1"
    assert controller.attributes["source"] == "external"


async def test_sensor_change_debounced_and_written(
    hass, freezer, controller, gateway
) -> None:
    await controller.async_start()
    await settle(hass)
    gateway.writes.clear()
    hass.states.async_set("sensor.t1", "21.8", CELSIUS)
    await settle(hass)
    assert gateway.writes == []
    await advance(hass, freezer, 5)
    assert [w[4] for w in gateway.writes] == [2180, 2180]


async def test_new_sensor_value_rearms_the_debounce(
    hass, freezer, controller, gateway
) -> None:
    await controller.async_start()
    await settle(hass)
    gateway.writes.clear()
    hass.states.async_set("sensor.t1", "21.8", CELSIUS)
    await advance(hass, freezer, 3, step=1)
    hass.states.async_set("sensor.t1", "22.4", CELSIUS)
    # The first timer would have fired by now; the second one is still running.
    await advance(hass, freezer, 4, step=1)
    assert gateway.writes == []
    await advance(hass, freezer, 2, step=1)
    assert [w[4] for w in gateway.writes] == [2240, 2240]


async def test_state_reports_do_not_starve_the_debounce(
    hass, freezer, controller, gateway
) -> None:
    await controller.async_start()
    await settle(hass)
    gateway.writes.clear()
    hass.states.async_set("sensor.t1", "21.8", CELSIUS)
    for second in range(1, 16):
        await advance(hass, freezer, 1, step=1)
        if second % 3 == 0:
            # Same value again: only last_reported moves (a state report).
            hass.states.async_set("sensor.t1", "21.8", CELSIUS)
            await settle(hass)
        if second < 5:
            assert gateway.writes == []
        else:
            assert [w[4] for w in gateway.writes] == [2180, 2180]


async def test_missing_sensor_state_falls_back_to_next_sensor(
    hass, controller, gateway
) -> None:
    hass.states.async_remove("sensor.t1")
    await controller.async_start()
    await settle(hass)
    assert controller.attributes["active_sensor"] == "sensor.t2"
    assert writes_to(gateway, IEEE_A, VALUE) == [2000]


async def test_unit_warning_logged_once(hass, controller, caplog) -> None:
    hass.states.async_set("sensor.t1", "21.3", {"unit_of_measurement": "K"})
    await controller.async_start()
    await settle(hass)
    hass.states.async_set("sensor.t1", "21.4", {"unit_of_measurement": "K"})
    await settle(hass)
    assert caplog.text.count("unsupported unit") == 1
    # The Kelvin sensor is unusable, so the second one feeds the thermostats.
    assert controller.attributes["active_sensor"] == "sensor.t2"


async def test_state_report_keeps_the_sensor_fresh(hass, freezer, controller) -> None:
    await controller.async_start()
    await settle(hass)
    await advance(hass, freezer, 100 * 60, step=10 * 60)
    # Same state and attributes: Home Assistant only refreshes last_reported.
    hass.states.async_set("sensor.t1", "21.3", CELSIUS)
    await settle(hass)
    await advance(hass, freezer, 100 * 60, step=10 * 60)
    # 200 minutes after the last change, more than the 120 minute timeout.
    assert controller.attributes["source"] == "external"
    assert controller.attributes["active_sensor"] == "sensor.t1"


async def test_silent_sensors_time_out_to_internal_source(
    hass, freezer, controller
) -> None:
    await controller.async_start()
    await settle(hass)
    await advance(hass, freezer, 130 * 60, step=10 * 60)
    assert controller.attributes["source"] == "internal"
    assert controller.attributes["active_sensor"] is None
    assert controller.status == RoomStatus.DEGRADED


# --- Serial, verified writes ----------------------------------------------


async def test_operations_run_one_at_a_time(
    hass, freezer, controller_with_delay, gateway
) -> None:
    await controller_with_delay.async_start()
    await settle(hass)
    # The first write is out and waits for its verify delay: nothing else runs.
    assert gateway.calls == ["write"]
    await advance(hass, freezer, 10)
    assert gateway.calls == ["write", "read", "write"]
    await advance(hass, freezer, 100)
    # Every write is read back before the next operation starts.
    assert gateway.calls == ["write", "read"] * 10
    assert controller_with_delay.status == RoomStatus.OK


async def test_verify_delay_and_mismatch_retry(
    hass, freezer, controller_with_delay, gateway, caplog
) -> None:
    write_failed = async_capture_events(hass, EVENT_WRITE_FAILED)
    gateway.read_override[(IEEE_A, VALUE)] = 2000
    await controller_with_delay.async_start()
    await settle(hass)
    assert len(gateway.writes) == 1
    assert gateway.reads == []
    for _ in range(40):
        await advance(hass, freezer, 5)
        if health(controller_with_delay)["climate.a"] == "error":
            break
    assert health(controller_with_delay) == {"climate.a": "error", "climate.b": "ok"}
    assert controller_with_delay.status == RoomStatus.ERROR
    assert writes_to(gateway, IEEE_A, VALUE) == [2130, 2130, 2130]
    assert writes_to(gateway, IEEE_B, VALUE) == [2130]
    assert controller_with_delay.attributes["last_error"] == (
        "mismatch: expected [2130] got 2000"
    )
    raised = issue(hass, "trv_unreachable_0011")
    assert raised is not None
    assert raised.severity == IssueSeverity.ERROR
    assert raised.translation_key == "trv_unreachable"
    assert raised.translation_placeholders == {
        "room": "Living",
        "setting": "Room temperature",
        "thermostat": "climate.a",
        "attempts": "3",
        "error": "mismatch: expected [2130] got 2000",
    }
    assert issue(hass, "trv_unreachable_0022") is None
    assert [e.data["attribute"] for e in write_failed] == ["external_temperature"]
    [record] = [r for r in caplog.records if "did not accept" in r.getMessage()]
    assert record.levelno == logging.WARNING
    # The core's message already names the room; the controller adds nothing.
    assert record.getMessage().startswith(
        "Living: climate.a did not accept external_temperature after 3 attempts"
    )
    assert record.getMessage().count("Living:") == 1


async def test_unreachable_issue_names_setting_and_thermostat(
    hass, freezer, make_controller, gateway
) -> None:
    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("test", "a")}, name="Living TRV"
    )
    # The state of the fixture would make the registry pick "climate.a_2".
    hass.states.async_remove("climate.a")
    entity = er.async_get(hass).async_get_or_create(
        "climate", "test", "a", device_id=device.id, suggested_object_id="a"
    )
    assert entity.entity_id == "climate.a"
    hass.states.async_set("climate.a", "heat")
    gateway.read_override[(IEEE_A, WINDOW)] = 0
    controller = make_controller()
    await controller.async_start()
    await advance(hass, freezer, 25)
    raised = issue(hass, "trv_unreachable_0011")
    assert raised is not None
    assert raised.translation_placeholders == {
        "room": "Living",
        "setting": "Thermostat's open-window detection",
        "thermostat": "Living TRV",
        "attempts": "3",
        "error": "mismatch: expected [1] got 0",
    }


async def test_labels_fall_back_to_keys_and_entity_ids(
    hass, freezer, make_controller, gateway
) -> None:
    gateway.read_override[(IEEE_A, WINDOW)] = 0
    controller = make_controller(labels={})
    await controller.async_start()
    await advance(hass, freezer, 25)
    raised = issue(hass, "trv_unreachable_0011")
    assert raised is not None
    assert raised.translation_placeholders["setting"] == "window_detection_flag"
    assert raised.translation_placeholders["thermostat"] == "climate.a"


async def test_setting_name_follows_the_intent_of_a_custom_capability(
    hass, freezer, make_controller, gateway, profile
) -> None:
    capabilities = {
        ("ext_temp" if name == "external_temperature" else name): spec
        for name, spec in profile.capabilities.items()
    }
    gateway.read_override[(IEEE_A, VALUE)] = 2000
    controller = make_controller(prof=replace(profile, capabilities=capabilities))
    await controller.async_start()
    await advance(hass, freezer, 25)
    raised = issue(hass, "trv_unreachable_0011")
    assert raised is not None
    assert raised.translation_placeholders["setting"] == "Room temperature"


async def test_write_error_is_retried_and_recovers(
    hass, freezer, controller, gateway
) -> None:
    gateway.fail_write[(IEEE_A, VALUE)] = "radio busy"
    await controller.async_start()
    await settle(hass)
    assert writes_to(gateway, IEEE_A, VALUE) == [2130]
    assert health(controller)["climate.a"] == "ok"
    await advance(hass, freezer, 5)
    assert writes_to(gateway, IEEE_A, VALUE) == [2130, 2130]
    assert controller.status == RoomStatus.OK
    assert issue(hass, "trv_unreachable_0011") is None


async def test_recovery_clears_the_unreachable_issue(
    hass, freezer, controller, gateway
) -> None:
    recovered = async_capture_events(hass, EVENT_RECOVERED)
    gateway.read_override[(IEEE_A, VALUE)] = 2000
    await controller.async_start()
    await advance(hass, freezer, 25)
    assert health(controller)["climate.a"] == "error"
    assert issue(hass, "trv_unreachable_0011") is not None
    del gateway.read_override[(IEEE_A, VALUE)]
    # The periodic refresh writes the value again and the thermostat answers.
    await advance(hass, freezer, 25 * 60, step=25 * 60)
    assert health(controller)["climate.a"] == "ok"
    assert controller.status == RoomStatus.OK
    assert issue(hass, "trv_unreachable_0011") is None
    assert [e.data["trv_entity_id"] for e in recovered] == ["climate.a"]


async def test_start_clears_stale_thermostat_issues(hass, controller, gateway) -> None:
    # Issues left over from before a reload: the controller starts from a fresh
    # state, so it must not inherit them (a still-valid problem is raised again
    # by the first operations).
    for ieee in (IEEE_A, IEEE_B, "00:99"):
        issues.raise_unreachable(
            hass,
            ieee,
            room="Living",
            setting="x",
            thermostat="y",
            attempts="3",
            error="z",
        )
        issues.raise_firmware(
            hass,
            ieee,
            room="Living",
            setting="x",
            thermostat="y",
            required="1",
            current="0",
        )

    await controller.async_start()

    # Cleared by the start itself, before the worker has written anything.
    assert gateway.writes == []
    for ieee in ("0011", "0022"):
        assert issue(hass, f"trv_unreachable_{ieee}") is None
        assert issue(hass, f"trv_firmware_{ieee}") is None
    # A thermostat of another room keeps its issues.
    assert issue(hass, "trv_unreachable_0099") is not None
    assert issue(hass, "trv_firmware_0099") is not None


OPERATION_LOGGER = "custom_components.easy_trv.controller"


def operation_lines(caplog) -> list[str]:
    """Return the debug lines the controller wrote about executed operations."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == OPERATION_LOGGER
        and record.levelno == logging.DEBUG
        and (" attr 0x" in record.getMessage() or " not sent " in record.getMessage())
    ]


async def test_every_executed_operation_is_logged_at_debug(
    hass, controller, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger=OPERATION_LOGGER)
    await controller.async_start()
    await settle(hass)
    assert operation_lines(caplog) == [
        "Living: climate.a window_detection_flag attr 0x6000 raw=1 -> ok",
        "Living: climate.a hysteresis attr 0x6011 raw=-40 -> ok",
        "Living: climate.a frost_protection attr 0x6002 raw=700 -> ok",
        "Living: climate.b window_detection_flag attr 0x6000 raw=1 -> ok",
        "Living: climate.b hysteresis attr 0x6011 raw=-40 -> ok",
        "Living: climate.b frost_protection attr 0x6002 raw=700 -> ok",
        "Living: climate.a source_select attr 0x600E raw=1 -> ok",
        "Living: climate.b source_select attr 0x600E raw=1 -> ok",
        "Living: climate.a external_temperature attr 0x600D raw=2130 -> ok",
        "Living: climate.b external_temperature attr 0x600D raw=2130 -> ok",
    ]


async def test_operation_log_names_a_mismatch_and_a_failure(
    hass, controller, gateway, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger=OPERATION_LOGGER)
    gateway.read_override[(IEEE_A, VALUE)] = 2000
    gateway.fail_write[(IEEE_B, VALUE)] = "radio busy"
    await controller.async_start()
    await settle(hass)
    lines = operation_lines(caplog)
    assert (
        "Living: climate.a external_temperature attr 0x600D raw=2130 "
        "-> mismatch expected [2130] got 2000"
    ) in lines
    assert (
        "Living: climate.b external_temperature attr 0x600D raw=2130 "
        "-> failed: radio busy"
    ) in lines


async def test_operation_log_names_a_signal(hass, controller, gateway, caplog) -> None:
    caplog.set_level(logging.DEBUG, logger=OPERATION_LOGGER)
    gateway.read_override[(IEEE_A, SOURCE)] = 2
    await controller.async_start()
    await settle(hass)
    assert "Living: climate.a source_select attr 0x600E raw=1 -> signal failsafe" in (
        operation_lines(caplog)
    )


async def test_operation_log_names_what_was_not_sent(
    hass, make_controller, config, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger=OPERATION_LOGGER)
    old_a = replace(config.trvs[0], firmware=(1, 2, 1))
    controller = make_controller(cfg=replace(config, trvs=(old_a, config.trvs[1])))
    await controller.async_start()
    await settle(hass)
    assert (
        "Living: climate.a hysteresis not sent -> "
        "unsupported hysteresis needs 1.3.0, installed 1.2.1"
    ) in operation_lines(caplog)


def with_source_signals(profile, signals):
    """Return the profile with other signals on the ``source_select`` readback."""
    source = profile.capabilities["source_select"]
    capabilities = {
        **profile.capabilities,
        "source_select": replace(source, signals=signals),
    }
    return replace(profile, capabilities=capabilities)


async def test_signal_takes_precedence_over_accept(
    hass, freezer, make_controller, gateway, profile
) -> None:
    controller = make_controller(prof=with_source_signals(profile, {1: "failsafe"}))
    await controller.async_start()
    await settle(hass)
    # The read-back 1 is accepted for "external", but it is also a signal.
    # A signal is a failure for a source write, so it is retried after 5 s.
    assert writes_to(gateway, IEEE_A, SOURCE) == [1]
    await advance(hass, freezer, 5)
    assert writes_to(gateway, IEEE_A, SOURCE) == [1, 1]


async def test_accepted_readback_without_signal_is_not_retried(
    hass, freezer, make_controller, gateway, profile
) -> None:
    controller = make_controller(prof=with_source_signals(profile, {}))
    await controller.async_start()
    await advance(hass, freezer, 5)
    assert writes_to(gateway, IEEE_A, SOURCE) == [1]
    assert controller.status == RoomStatus.OK


async def test_failsafe_readback_reenables(
    hass, freezer, controller, gateway, events
) -> None:
    await controller.async_start()
    await settle(hass)
    gateway.writes.clear()
    gateway.read_override[(IEEE_B, SOURCE)] = 2
    await advance(hass, freezer, 20 * 60, step=20 * 60)
    assert (IEEE_B, SOURCE, 1) in written(gateway)
    assert [e.event_type for e in events] == ["easy_trv_failsafe_detected"]
    assert events[0].data["trv_entity_id"] == "climate.b"


# --- Outcome mapping ------------------------------------------------------


async def test_firmware_gated_capability_raises_issue_and_keeps_writing(
    hass, make_controller, gateway, config
) -> None:
    old_a = replace(config.trvs[0], firmware=(1, 2, 1))
    gateway.devices["climate.a"] = replace(
        gateway.devices["climate.a"], firmware=(1, 2, 1)
    )
    controller = make_controller(cfg=replace(config, trvs=(old_a, config.trvs[1])))
    await controller.async_start()
    await settle(hass)
    raised = issue(hass, "trv_firmware_0011")
    assert raised is not None
    assert raised.severity == IssueSeverity.WARNING
    assert raised.translation_key == "trv_firmware"
    assert raised.translation_placeholders == {
        "room": "Living",
        "setting": "Hysteresis",
        "thermostat": "climate.a",
        "required": "1.3.0",
        "current": "1.2.1",
    }
    assert issue(hass, "trv_firmware_0022") is None
    # Hysteresis never reaches the old device, everything else still does.
    assert written(gateway) == [
        (IEEE_A, WINDOW, 1),
        (IEEE_A, FROST, 700),
        (IEEE_B, WINDOW, 1),
        (IEEE_B, HYSTERESIS, -40),
        (IEEE_B, FROST, 700),
        (IEEE_A, SOURCE, 1),
        (IEEE_B, SOURCE, 1),
        (IEEE_A, VALUE, 2130),
        (IEEE_B, VALUE, 2130),
    ]
    assert health(controller) == {"climate.a": "unsupported", "climate.b": "ok"}
    assert controller.status == RoomStatus.DEGRADED


def without_capabilities(profile, *names):
    """Return the profile minus capabilities, like a copied 0.1.0 user profile."""
    capabilities = {
        name: spec for name, spec in profile.capabilities.items() if name not in names
    }
    return replace(profile, capabilities=capabilities)


async def test_profile_without_capability_is_not_a_firmware_problem(
    hass, make_controller, gateway, profile, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.easy_trv.controller")
    controller = make_controller(prof=without_capabilities(profile, "hysteresis"))
    await controller.async_start()
    await settle(hass)
    assert issue(hass, "trv_firmware_0011") is None
    assert issue(hass, "trv_firmware_0022") is None
    assert writes_to(gateway, IEEE_A, HYSTERESIS) == []
    assert writes_to(gateway, IEEE_A, VALUE) == [2130]
    assert health(controller) == {"climate.a": "ok", "climate.b": "ok"}
    assert controller.status == RoomStatus.OK
    lines = [
        r.getMessage()
        for r in caplog.records
        if "no set_hysteresis setting" in r.getMessage()
    ]
    assert len(lines) == 2
    assert all("Living" in line for line in lines)
    assert any("climate.a" in line for line in lines)
    assert controller.snapshot()["not_in_profile"] == {
        "climate.a": ["set_hysteresis"],
        "climate.b": ["set_hysteresis"],
    }


async def test_profile_without_adaptive_mode_gets_the_hysteresis_once(
    hass, make_controller, gateway, profile, config
) -> None:
    controller = make_controller(
        cfg=replace(config, adaptive_mode="on"),
        prof=without_capabilities(profile, "adaptive_mode", "frost_protection"),
    )
    await controller.async_start()
    await settle(hass)
    assert writes_to(gateway, IEEE_A, HYSTERESIS) == [-40]
    assert writes_to(gateway, IEEE_B, HYSTERESIS) == [-40]
    assert health(controller) == {"climate.a": "ok", "climate.b": "ok"}
    assert controller.snapshot()["not_in_profile"]["climate.a"] == [
        "set_adaptive_mode",
        "set_frost_temperature",
    ]


async def test_unsupported_attribute_raises_a_firmware_issue_without_retrying(
    hass, freezer, make_controller, gateway
) -> None:
    gateway.unsupported_write.add((IEEE_A, HYSTERESIS))
    controller = make_controller()
    await controller.async_start()
    # Longer than the first two retry delays, which a failure would have used.
    await advance(hass, freezer, 60)

    raised = issue(hass, "trv_firmware_0011")
    assert raised is not None
    assert raised.severity == IssueSeverity.WARNING
    assert raised.translation_placeholders == {
        "room": "Living",
        "setting": "Hysteresis",
        "thermostat": "climate.a",
        "required": "unknown",
        "current": "1.3.0",
    }
    assert issue(hass, "trv_unreachable_0011") is None
    assert issue(hass, "trv_firmware_0022") is None
    # One attempt, no retry; the thermostat still gets source and value.
    assert writes_to(gateway, IEEE_A, HYSTERESIS) == [-40]
    assert writes_to(gateway, IEEE_A, SOURCE) == [1]
    assert writes_to(gateway, IEEE_A, VALUE) == [2130]
    assert health(controller) == {"climate.a": "unsupported", "climate.b": "ok"}
    assert controller.status == RoomStatus.DEGRADED


async def test_a_failed_setting_is_sent_again_and_the_issue_resolves_itself(
    hass, freezer, make_controller, gateway
) -> None:
    gateway.read_override[(IEEE_A, HYSTERESIS)] = 0
    controller = make_controller()
    await controller.async_start()
    await advance(hass, freezer, 25)
    raised = issue(hass, "trv_unreachable_0011")
    assert raised is not None
    assert raised.translation_placeholders["setting"] == "Hysteresis"
    assert health(controller)["climate.a"] == "error"

    # The thermostat accepts the setting again; nothing but the refresh interval
    # makes Easy TRV try it once more.
    del gateway.read_override[(IEEE_A, HYSTERESIS)]
    recovered = async_capture_events(hass, EVENT_RECOVERED)
    await advance(hass, freezer, 1260, step=60)

    assert issue(hass, "trv_unreachable_0011") is None
    assert len(recovered) == 1
    assert health(controller) == {"climate.a": "ok", "climate.b": "ok"}
    assert controller.status == RoomStatus.OK


@pytest.mark.parametrize("scenario", ["unavailable", "unknown"])
async def test_unreachable_device_is_a_failed_outcome(
    hass, freezer, make_controller, gateway, scenario
) -> None:
    error = {
        "unavailable": "device unavailable",
        "unknown": "device 00:11 is not known to ZHA",
    }[scenario]
    controller = make_controller()
    if scenario == "unavailable":
        gateway.availability[IEEE_A] = False
    else:
        # ``is_available`` raises GatewayError for devices ZHA does not know.
        del gateway.devices["climate.a"]
    await controller.async_start()
    await advance(hass, freezer, 25)
    assert [w for w in gateway.writes if w[0] == IEEE_A] == []
    assert health(controller) == {"climate.a": "error", "climate.b": "ok"}
    assert controller.attributes["last_error"] == error
    raised = issue(hass, "trv_unreachable_0011")
    assert raised is not None
    assert raised.translation_placeholders["error"] == error
    assert writes_to(gateway, IEEE_B, VALUE) == [2130]


async def test_out_of_range_value_is_a_failed_outcome(
    hass, freezer, make_controller, gateway, config
) -> None:
    controller = make_controller(cfg=replace(config, hysteresis=5.0))
    await controller.async_start()
    await advance(hass, freezer, 25)
    assert writes_to(gateway, IEEE_A, HYSTERESIS) == []
    assert "is outside [0.2, 1.0]" in controller.attributes["last_error"]
    assert writes_to(gateway, IEEE_A, VALUE) == [2130]


async def test_timeout_is_a_failed_outcome(
    hass, freezer, make_controller, gateway, monkeypatch
) -> None:
    original = gateway.write

    async def hang_on_a(ieee, *args) -> None:
        if ieee == IEEE_A:
            await asyncio.sleep(3600)
        await original(ieee, *args)

    monkeypatch.setattr(gateway, "write", hang_on_a)
    controller = make_controller()
    await controller.async_start()
    for _ in range(100):
        await advance(hass, freezer, 5)
        if health(controller)["climate.a"] == "error":
            break
    assert health(controller) == {"climate.a": "error", "climate.b": "ok"}
    assert controller.attributes["last_error"] == "TimeoutError"


async def test_unexpected_error_becomes_a_failed_outcome_and_is_retried(
    hass, freezer, controller, gateway, caplog
) -> None:
    original = gateway.write
    calls = []

    async def explode_once(*args) -> None:
        calls.append(args)
        if len(calls) == 1:
            msg = "boom"
            raise RuntimeError(msg)
        await original(*args)

    gateway.write = explode_once
    await controller.async_start()
    await settle(hass)
    # The first write (window detection of a) blew up; the worker went on.
    assert len(gateway.writes) == 9
    assert writes_to(gateway, IEEE_A, WINDOW) == []
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and r.exc_info]
    assert len(errors) == 1
    assert "boom" in caplog.text
    # The failure reached the core, which scheduled the first retry after 5 s.
    await advance(hass, freezer, 5)
    assert writes_to(gateway, IEEE_A, WINDOW) == [1]
    assert controller.status == RoomStatus.OK


async def test_persistent_unexpected_error_ends_in_error_and_spares_other_trv(
    hass, freezer, controller, gateway, monkeypatch
) -> None:
    original = gateway.write

    async def explode_on_a(ieee, *args) -> None:
        if ieee == IEEE_A:
            msg = "boom"
            raise RuntimeError(msg)
        await original(ieee, *args)

    monkeypatch.setattr(gateway, "write", explode_on_a)
    await controller.async_start()
    await advance(hass, freezer, 25)
    assert health(controller) == {"climate.a": "error", "climate.b": "ok"}
    assert controller.attributes["last_error"] == "unexpected: RuntimeError: boom"
    raised = issue(hass, "trv_unreachable_0011")
    assert raised is not None
    assert raised.translation_placeholders["error"] == "unexpected: RuntimeError: boom"
    assert [w for w in written(gateway) if w[0] == IEEE_B] == [
        (IEEE_B, WINDOW, 1),
        (IEEE_B, HYSTERESIS, -40),
        (IEEE_B, FROST, 700),
        (IEEE_B, SOURCE, 1),
        (IEEE_B, VALUE, 2130),
    ]


async def test_failing_listener_does_not_kill_the_worker(
    hass, freezer, controller, gateway, caplog
) -> None:
    gateway.read_override[(IEEE_A, VALUE)] = 2000
    await controller.async_start()
    await settle(hass)

    def broken() -> None:
        msg = "listener broke"
        raise RuntimeError(msg)

    controller.add_listener(broken)
    # The third mismatch flips the status to error; the listener then raises
    # while the worker feeds the result to the logic.
    await advance(hass, freezer, 25)
    assert controller.status == RoomStatus.ERROR
    assert "unexpected error while handling the result" in caplog.text
    assert issue(hass, "trv_unreachable_0011") is not None
    # The worker is still alive and keeps writing.
    gateway.writes.clear()
    hass.states.async_set("sensor.t1", "21.8", CELSIUS)
    await advance(hass, freezer, 5)
    assert [w[4] for w in gateway.writes] == [2180, 2180]


# --- TRV availability -----------------------------------------------------


async def test_unavailable_trv_is_skipped_and_resynced(
    hass, controller, gateway
) -> None:
    hass.states.async_set("climate.a", "unavailable")
    await controller.async_start()
    await settle(hass)
    assert {w[0] for w in gateway.writes} == {IEEE_B}
    assert health(controller) == {"climate.a": "unavailable", "climate.b": "ok"}
    assert controller.status == RoomStatus.DEGRADED

    gateway.writes.clear()
    hass.states.async_set("climate.a", "heat")
    await settle(hass)
    assert written(gateway) == [
        (IEEE_A, WINDOW, 1),
        (IEEE_A, HYSTERESIS, -40),
        (IEEE_A, FROST, 700),
        (IEEE_A, SOURCE, 1),
        (IEEE_A, VALUE, 2130),
    ]
    assert controller.status == RoomStatus.OK

    # Changing between available states is not an availability change.
    gateway.writes.clear()
    hass.states.async_set("climate.a", "off")
    await settle(hass)
    assert gateway.writes == []

    hass.states.async_set("climate.a", "unavailable")
    await settle(hass)
    assert health(controller)["climate.a"] == "unavailable"


# --- Stop -----------------------------------------------------------------


async def test_stop_cancels_everything(hass, freezer, controller, gateway) -> None:
    gateway.read_override[(IEEE_B, VALUE)] = 0
    tracked = ("state_changed", "state_reported")
    before = [hass.bus.async_listeners().get(name, 0) for name in tracked]
    await controller.async_start()
    await settle(hass)
    # A mismatch arms a retry timer and a sensor change arms the debounce.
    hass.states.async_set("sensor.t1", "21.8", CELSIUS)
    await advance(hass, freezer, 5)
    hass.states.async_set("sensor.t1", "22.4", CELSIUS)
    await settle(hass)
    await controller.async_stop()
    await controller.async_stop()
    assert [hass.bus.async_listeners().get(name, 0) for name in tracked] == before
    gateway.writes.clear()
    del gateway.read_override[(IEEE_B, VALUE)]

    hass.states.async_set("sensor.t1", "25.0", CELSIUS)
    hass.states.async_set("climate.b", "unavailable")
    await advance(hass, freezer, 30 * 60, step=60)
    assert gateway.writes == []
    assert health(controller)["climate.b"] != "unavailable"


async def test_stop_cancels_an_operation_in_flight(
    hass, freezer, controller_with_delay, gateway
) -> None:
    await controller_with_delay.async_start()
    await settle(hass)
    assert len(gateway.writes) == 1
    await controller_with_delay.async_stop()
    await advance(hass, freezer, 100)
    assert len(gateway.writes) == 1
    assert gateway.reads == []


# --- Listeners and snapshot -----------------------------------------------


async def test_listeners_are_notified_only_on_change(hass, controller) -> None:
    calls = []
    remove = controller.add_listener(lambda: calls.append(1))
    await controller.async_start()
    await settle(hass)
    assert len(calls) == 1

    # A report with an unchanged value leaves status and attributes as they are.
    hass.states.async_set("sensor.t1", "21.3", CELSIUS)
    await settle(hass)
    assert len(calls) == 1

    hass.states.async_set("sensor.t1", "unavailable")
    await settle(hass)
    assert controller.attributes["active_sensor"] == "sensor.t2"
    assert len(calls) == 2

    remove()
    hass.states.async_set("sensor.t1", "21.3", CELSIUS)
    await settle(hass)
    assert controller.attributes["active_sensor"] == "sensor.t1"
    assert len(calls) == 2


async def test_snapshot_is_json_able(hass, controller, gateway) -> None:
    gateway.read_override[(IEEE_A, VALUE)] = 2000
    await controller.async_start()
    await settle(hass)
    snapshot = json.loads(json.dumps(controller.snapshot()))
    assert set(snapshot) == {
        "config",
        "params",
        "state",
        "not_in_profile",
        "hold",
        "notes",
        "observations",
        "heating_state",
        "state_reasons",
        "state_since",
        "window_off_at",
        "silent_contacts",
        "deviations",
    }
    assert snapshot["config"]["room_id"] == "room1"
    assert snapshot["config"]["trvs"][0] == {
        "entity_id": "climate.a",
        "ieee": IEEE_A,
        "firmware": [1, 3, 0],
    }
    assert snapshot["params"]["refresh"] == 1200.0
    assert snapshot["params"]["verify_delay"] == 0.0
    state = snapshot["state"]
    assert state["source"] == "external"
    assert state["readings"]["sensor.t1"]["value"] == 21.3
    assert isinstance(state["readings"]["sensor.t1"]["last_reported"], str)
    trv = state["trvs"]["climate.a"]
    assert trv["health"] == "ok"
    assert trv["attempts"] == {"external_temperature": 1}
    assert trv["pending_retry"] == {
        "external_temperature": {"intent": "set_room_temperature", "celsius": 21.3}
    }


# --- Room thermostat: commands, observations, holds -----------------------

OUTDOOR = "sensor.outdoor"
CONTACT = "binary_sensor.w"


def with_climate(cfg: RoomConfig, **changes) -> RoomConfig:
    """Return the room with a setpoint range, so it has a room thermostat."""
    return replace(cfg, setpoint_range=RANGE, **changes)


@pytest.fixture
def climate(make_controller, config) -> RoomController:
    return make_controller(cfg=with_climate(config))


async def start_with_setpoint(hass, controller, gateway, celsius=21.0) -> None:
    """Start the room with a setpoint and forget the writes of the start."""
    await controller.async_start()
    await settle(hass)
    await controller.async_set_setpoint(celsius)
    await settle(hass)
    gateway.writes.clear()


# Subscriptions


async def test_start_subscribes_once_per_thermostat_to_the_observed_capabilities(
    climate, gateway
) -> None:
    await climate.async_start()
    assert [(s[0], s[1], s[2], tuple(sorted(s[3]))) for s in gateway.subscriptions] == [
        (IEEE_A, 1, 0x0201, (SETPOINT, MODE)),
        (IEEE_B, 1, 0x0201, (SETPOINT, MODE)),
    ]


async def test_subscriptions_are_removed_on_unload(climate, gateway) -> None:
    await climate.async_start()
    assert len(gateway.subscriptions) == 2
    await climate.async_stop()
    assert gateway.subscriptions == []
    await climate.async_stop()


async def test_a_thermostat_unknown_to_zha_is_subscribed_when_it_comes_back(
    hass, climate, gateway, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger=OPERATION_LOGGER)
    gateway.subscribe_unavailable.add(IEEE_A)
    hass.states.async_set("climate.a", "unavailable")
    await climate.async_start()
    await settle(hass)
    assert [s[0] for s in gateway.subscriptions] == [IEEE_B]
    assert any(
        "climate.a" in r.getMessage() and "subscri" in r.getMessage()
        for r in caplog.records
        if r.levelno == logging.DEBUG
    )

    gateway.subscribe_unavailable.clear()
    hass.states.async_set("climate.a", "heat")
    await settle(hass)
    assert sorted(s[0] for s in gateway.subscriptions) == [IEEE_A, IEEE_B]

    # Coming back again replaces the subscription (ZHA may have reloaded).
    hass.states.async_set("climate.a", "unavailable")
    await settle(hass)
    hass.states.async_set("climate.a", "heat")
    await settle(hass)
    assert sorted(s[0] for s in gateway.subscriptions) == [IEEE_A, IEEE_B]


async def test_a_subscription_that_fails_again_on_return_is_tried_later(
    hass, climate, gateway
) -> None:
    await climate.async_start()
    gateway.subscribe_unavailable.add(IEEE_A)
    hass.states.async_set("climate.a", "unavailable")
    await settle(hass)
    hass.states.async_set("climate.a", "heat")
    await settle(hass)
    assert [s[0] for s in gateway.subscriptions] == [IEEE_B]


async def test_a_room_without_observed_capabilities_subscribes_to_nothing(
    make_controller, config, gateway, profile
) -> None:
    capabilities = {
        name: spec for name, spec in profile.capabilities.items() if not spec.observe
    }
    bare = replace(profile, capabilities=capabilities)
    await make_controller(cfg=config, prof=bare).async_start()
    assert gateway.subscriptions == []


def test_the_fake_gateway_raises_zha_unavailable_for_an_unknown_ieee(gateway) -> None:
    with pytest.raises(ZhaUnavailable):
        gateway.subscribe("00:99", 1, 0x0201, [SETPOINT], lambda *_: None)


# Observations


async def test_report_from_a_thermostat_is_decoded_and_adopted(
    hass, freezer, climate, gateway
) -> None:
    await start_with_setpoint(hass, climate, gateway)
    # The report comes after the room's own write, not at the same instant.
    freezer.tick(timedelta(seconds=1))
    gateway.inject(IEEE_A, SETPOINT, 2200)
    await advance(hass, freezer, 1, step=1)
    assert gateway.writes == []
    await advance(hass, freezer, 5)
    assert writes_to(gateway, IEEE_B, SETPOINT) == [2200]
    assert writes_to(gateway, IEEE_A, SETPOINT) == []
    assert climate.setpoint == 22.0
    assert climate.setpoint_source == "device:climate.a"


async def test_a_knob_turned_twice_within_the_debounce_is_adopted_once(
    hass, freezer, climate, gateway
) -> None:
    await start_with_setpoint(hass, climate, gateway)
    gateway.inject(IEEE_A, SETPOINT, 2200)
    await advance(hass, freezer, 2, step=1)
    gateway.inject(IEEE_A, SETPOINT, 2250)
    await advance(hass, freezer, 2, step=1)
    assert gateway.writes == []
    await advance(hass, freezer, 5)
    assert writes_to(gateway, IEEE_B, SETPOINT) == [2250]


async def test_stop_cancels_the_knob_timer(hass, freezer, climate, gateway) -> None:
    await start_with_setpoint(hass, climate, gateway)
    gateway.inject(IEEE_A, SETPOINT, 2200)
    await climate.async_stop()
    await advance(hass, freezer, 10)
    assert gateway.writes == []
    assert climate.setpoint == 21.0


async def test_a_report_of_the_schedule_mode_is_a_note(hass, climate, gateway) -> None:
    await start_with_setpoint(hass, climate, gateway)
    assert climate.in_sync is True
    assert climate.notes == {}
    gateway.inject(IEEE_A, MODE, 1)
    await settle(hass)
    assert climate.notes == {"climate.a": Note.SCHEDULE}
    assert climate.in_sync is False
    gateway.inject(IEEE_A, MODE, 4)
    await settle(hass)
    assert climate.notes == {}


async def test_a_foreign_value_read_back_after_a_write_is_adopted(
    hass, freezer, climate, gateway
) -> None:
    await climate.async_start()
    await settle(hass)
    gateway.read_override[(IEEE_A, SETPOINT)] = 2300
    await climate.async_set_setpoint(21.0)
    await settle(hass)
    await advance(hass, freezer, 5)
    assert climate.setpoint == 23.0
    assert climate.setpoint_source == "device:climate.a"
    assert writes_to(gateway, IEEE_B, SETPOINT) == [2100, 2300]


async def test_a_queued_setpoint_write_lands_with_the_newest_target(
    hass, climate, gateway
) -> None:
    await start_with_setpoint(hass, climate, gateway)
    gateway.gate = asyncio.Event()
    await climate.async_set_setpoint(22.0)
    await settle(hass)
    # A's 22 is in flight, B's waits in the queue behind it.
    await climate.async_set_setpoint(23.0)
    gateway.gate.set()
    await settle(hass)
    assert 2200 not in writes_to(gateway, IEEE_B, SETPOINT)
    assert writes_to(gateway, IEEE_B, SETPOINT)[-1] == 2300
    assert writes_to(gateway, IEEE_A, SETPOINT)[-1] == 2300
    assert climate.in_sync is True


async def test_a_queued_mode_write_follows_a_hold_that_ended_meanwhile(
    hass, make_controller, config, gateway
) -> None:
    room = make_controller(
        cfg=with_climate(config, window_contacts=(CONTACT,)),
    )
    hass.states.async_set(CONTACT, "off")
    await start_with_setpoint(hass, room, gateway)
    gateway.gate = asyncio.Event()
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    gateway.gate.set()
    await settle(hass)
    # B's queued off would have landed after the window closed.
    assert 0 not in writes_to(gateway, IEEE_B, MODE)
    assert writes_to(gateway, IEEE_B, MODE)[-1] == 4
    assert room.window_open is False
    assert room.in_sync is True


async def test_a_queued_off_for_a_schedule_thermostat_is_not_turned_into_heat(
    hass, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    gateway.inject(IEEE_B, MODE, 1)
    await settle(hass)
    gateway.gate = asyncio.Event()
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    gateway.gate.set()
    await settle(hass)
    # The window closed before the queue drained: B still gets its off.
    assert writes_to(gateway, IEEE_B, MODE) == [0]
    assert writes_to(gateway, IEEE_A, MODE)[-1] == 4


async def test_in_sync_is_false_when_a_thermostat_is_known_to_show_another_value(
    hass, freezer, climate, gateway
) -> None:
    await start_with_setpoint(hass, climate, gateway)
    assert climate.in_sync is True
    freezer.tick(timedelta(seconds=1))
    # A late echo of the earlier 21 is ignored as an echo, but A shows it now.
    await climate.async_set_setpoint(22.0)
    await settle(hass)
    assert climate.in_sync is True
    freezer.tick(timedelta(seconds=1))
    gateway.inject(IEEE_A, SETPOINT, 2100)
    await settle(hass)
    assert climate.in_sync is False
    freezer.tick(timedelta(seconds=1))
    gateway.inject(IEEE_A, SETPOINT, 2200)
    await settle(hass)
    assert climate.in_sync is True


async def test_in_sync_is_false_when_a_thermostat_is_known_to_show_the_wrong_mode(
    hass, freezer, climate, gateway
) -> None:
    await start_with_setpoint(hass, climate, gateway)
    await climate.async_set_mode(RoomMode.OFF)
    await settle(hass)
    freezer.tick(timedelta(seconds=1))
    await climate.async_set_mode(RoomMode.HEAT)
    await settle(hass)
    assert climate.in_sync is True
    # The late echo of the off is ignored as an echo, but B shows it now.
    freezer.tick(timedelta(seconds=1))
    gateway.inject(IEEE_B, MODE, 0)
    await settle(hass)
    assert climate.mode is RoomMode.HEAT
    assert climate.in_sync is False
    freezer.tick(timedelta(seconds=1))
    gateway.inject(IEEE_B, MODE, 4)
    await settle(hass)
    assert climate.in_sync is True


async def test_an_observation_is_ignored_after_stop(hass, climate, gateway) -> None:
    await start_with_setpoint(hass, climate, gateway)
    callbacks = [s[4] for s in gateway.subscriptions]
    await climate.async_stop()
    for callback in callbacks:
        callback(IEEE_A, SETPOINT, "report", 2200)
    assert climate.setpoint == 21.0


async def test_an_unknown_attribute_in_a_delivery_is_ignored(
    hass, climate, gateway
) -> None:
    await start_with_setpoint(hass, climate, gateway)
    callback = gateway.subscriptions[0][4]
    callback(IEEE_A, 0x7777, "report", 1)
    assert climate.setpoint == 21.0


# Start


async def test_start_adopts_the_setpoint_a_thermostat_reports(
    hass, climate, gateway
) -> None:
    hass.states.async_set("climate.a", "heat", {"temperature": 19.0})
    hass.states.async_set("climate.b", "heat", {"temperature": 22.0})
    await climate.async_start()
    await settle(hass)
    assert climate.setpoint == 19.0
    assert climate.setpoint_source == "initial"
    assert writes_to(gateway, IEEE_B, SETPOINT) == [1900]


@pytest.mark.parametrize("schedule_state", ["auto", "heat_cool"])
async def test_start_does_not_adopt_the_setpoint_of_a_thermostat_in_auto(
    hass, climate, gateway, schedule_state
) -> None:
    # "auto" (the thermostat's schedule, shown as heat_cool by some entities) is
    # not a mode the room owns: its setpoint is not adopted, nothing is written to
    # it, and the setpoint of the thermostat that reports heat wins.
    hass.states.async_set("climate.a", schedule_state, {"temperature": 12.0})
    hass.states.async_set("climate.b", "heat", {"temperature": 22.5})
    await climate.async_start()
    await settle(hass)
    assert climate.setpoint == 22.5
    assert climate.setpoint_source == "initial"
    assert writes_to(gateway, IEEE_A, SETPOINT) == []
    assert writes_to(gateway, IEEE_A, MODE) == []
    assert writes_to(gateway, IEEE_B, SETPOINT) == []
    assert climate.in_sync is False


async def test_start_reads_the_mode_and_bad_setpoints_leniently(hass, climate) -> None:
    hass.states.async_set("climate.a", "off", {"temperature": "n/a"})
    hass.states.async_set("climate.b", "off")
    await climate.async_start()
    await settle(hass)
    assert climate.mode is RoomMode.OFF
    assert climate.setpoint is None


async def test_start_reads_a_setpoint_in_the_unit_system_of_home_assistant(
    hass, climate
) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    hass.states.async_set("climate.a", "heat", {"temperature": 68.0})
    await climate.async_start()
    await settle(hass)
    assert climate.setpoint == 20.0


async def test_start_without_a_climate_state_reports_nothing(hass, climate) -> None:
    hass.states.async_remove("climate.a")
    hass.states.async_remove("climate.b")
    await climate.async_start()
    await settle(hass)
    assert climate.setpoint is None


async def test_a_restore_before_start_wins_over_the_devices(
    hass, climate, gateway
) -> None:
    hass.states.async_set("climate.a", "heat", {"temperature": 19.0})
    hass.states.async_set("climate.b", "heat", {"temperature": 19.0})
    await climate.async_restore(20.5, RoomMode.HEAT, Preset.NONE, 18.0)
    await climate.async_start()
    await settle(hass)
    assert climate.setpoint == 20.5
    assert climate.setpoint_source == "restore"
    assert writes_to(gateway, IEEE_A, SETPOINT) == [2050]
    assert writes_to(gateway, IEEE_B, SETPOINT) == [2050]


# Room commands


async def test_set_setpoint_writes_through_the_pipeline(hass, climate, gateway) -> None:
    await climate.async_start()
    await settle(hass)
    gateway.writes.clear()
    await climate.async_set_setpoint(21.3)
    await settle(hass)
    assert climate.setpoint == 21.5
    assert climate.setpoint_source == "ha"
    assert writes_to(gateway, IEEE_A, SETPOINT) == [2150]
    assert writes_to(gateway, IEEE_B, SETPOINT) == [2150]
    # Written, read back and accepted: nothing is retried or complained about.
    assert health(climate) == {"climate.a": "ok", "climate.b": "ok"}


async def test_set_mode_off_and_back_to_heat(hass, climate, gateway) -> None:
    await start_with_setpoint(hass, climate, gateway)
    await climate.async_set_mode(RoomMode.OFF)
    await settle(hass)
    assert climate.mode is RoomMode.OFF
    assert writes_to(gateway, IEEE_A, MODE) == [0]
    assert writes_to(gateway, IEEE_B, MODE) == [0]
    await climate.async_set_mode(RoomMode.HEAT)
    await settle(hass)
    assert climate.mode is RoomMode.HEAT
    assert writes_to(gateway, IEEE_A, MODE) == [0, 4]
    assert writes_to(gateway, IEEE_A, SETPOINT)[-1] == 2100


async def test_set_operation_frost_and_back(hass, climate, gateway) -> None:
    await start_with_setpoint(hass, climate, gateway)
    await climate.async_set_operation(OperatingMode.FROST)
    await settle(hass)
    assert climate.operation is OperatingMode.FROST
    assert climate.preset is Preset.FROST
    assert climate.setpoint == 7.0
    assert climate.setpoint_source == "frost"
    assert writes_to(gateway, IEEE_A, SETPOINT) == [700]
    await climate.async_set_operation(OperatingMode.HEAT)
    await settle(hass)
    assert climate.operation is OperatingMode.HEAT
    assert climate.setpoint == 21.0
    assert writes_to(gateway, IEEE_B, SETPOINT) == [700, 2100]


# Properties


async def test_a_room_without_climate_capabilities_has_no_range(controller) -> None:
    assert controller.effective_range is None


async def test_effective_range_is_the_range_of_the_room(climate) -> None:
    assert climate.effective_range == RANGE


async def test_current_temperature_is_the_value_last_written(
    hass, freezer, climate, gateway
) -> None:
    assert climate.current_temperature is None
    await climate.async_start()
    await settle(hass)
    assert writes_to(gateway, IEEE_A, VALUE) == [2130]
    assert climate.current_temperature == 21.3
    # A new reading is not what the thermostats know until it is written.
    hass.states.async_set("sensor.t1", "22.4", CELSIUS)
    await settle(hass)
    assert climate.current_temperature == 21.3
    await advance(hass, freezer, 10)
    assert climate.current_temperature == 22.4


async def test_current_temperature_falls_back_to_the_active_sensor(
    hass, climate, gateway
) -> None:
    # A room that starts switched off writes no value to the thermostats.
    await climate.async_restore(20.0, RoomMode.OFF, Preset.NONE)
    await climate.async_start()
    await settle(hass)
    assert writes_to(gateway, IEEE_A, VALUE) == []
    assert climate.current_temperature == 21.3
    hass.states.async_set("sensor.t1", "unavailable")
    hass.states.async_set("sensor.t2", "unavailable")
    await settle(hass)
    assert climate.current_temperature is None


async def test_in_sync_is_false_while_a_write_is_pending(
    hass, climate, gateway
) -> None:
    await start_with_setpoint(hass, climate, gateway)
    assert climate.in_sync is True
    gateway.read_override[(IEEE_A, SETPOINT)] = 0
    await climate.async_set_setpoint(22.0)
    await settle(hass)
    assert climate.in_sync is False


async def test_in_sync_is_false_while_the_write_is_queued_or_being_verified(
    hass, freezer, make_controller, config, gateway
) -> None:
    room = make_controller(
        cfg=with_climate(config), params=replace(PARAMS, verify_delay=10.0)
    )
    await room.async_start()
    await advance(hass, freezer, 120)
    assert room.in_sync is True
    await room.async_set_setpoint(22.0)
    # Queued, nothing written yet.
    assert room.in_sync is False
    await settle(hass)
    # Written, waiting for the verify read-back.
    assert writes_to(gateway, IEEE_A, SETPOINT) == [2200]
    assert room.in_sync is False
    await advance(hass, freezer, 60)
    assert writes_to(gateway, IEEE_B, SETPOINT) == [2200]
    assert room.in_sync is True


async def test_in_sync_is_false_once_the_attempts_are_used_up(
    hass, freezer, climate, gateway
) -> None:
    await climate.async_start()
    await settle(hass)
    assert climate.in_sync is True
    gateway.availability[IEEE_A] = False
    await climate.async_set_setpoint(22.0)
    for _ in range(40):
        await advance(hass, freezer, 5)
        if health(climate)["climate.a"] == "error":
            break
    assert health(climate)["climate.a"] == "error"
    # The thermostat is still available in Home Assistant, but not at the setpoint.
    assert hass.states.get("climate.a").state == "heat"
    assert climate.in_sync is False
    # A new, verified write brings it back.
    gateway.availability[IEEE_A] = True
    await climate.async_set_setpoint(22.5)
    await settle(hass)
    assert climate.in_sync is True


async def test_in_sync_is_false_for_an_unavailable_thermostat(
    hass, climate, gateway
) -> None:
    await start_with_setpoint(hass, climate, gateway)
    hass.states.async_set("climate.b", "unavailable")
    await settle(hass)
    assert climate.in_sync is False


# Update listeners


async def test_update_listeners_run_after_every_handled_event(hass, climate) -> None:
    calls = []
    remove = climate.add_update_listener(lambda: calls.append(climate.setpoint))
    await climate.async_start()
    await settle(hass)
    assert calls
    calls.clear()
    await climate.async_set_setpoint(22.0)
    assert calls == [22.0]
    remove()
    remove()
    await climate.async_set_setpoint(23.0)
    assert calls == [22.0]


async def test_a_failing_update_listener_does_not_break_the_others(
    hass, climate, caplog
) -> None:
    calls = []

    def broken() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    climate.add_update_listener(broken)
    climate.add_update_listener(lambda: calls.append(1))
    await climate.async_start()
    await settle(hass)
    assert calls
    assert "update listener" in caplog.text


# Window contacts


async def test_window_contact_opens_and_closes_the_hold(
    hass, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "off")
    await start_with_setpoint(hass, climate_window, gateway)
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    assert climate_window.window_open is True
    assert writes_to(gateway, IEEE_A, MODE) == [0]
    assert writes_to(gateway, IEEE_B, MODE) == [0]
    # Home Assistant states keep the room's own wishes during the hold.
    assert climate_window.mode is RoomMode.HEAT
    assert climate_window.setpoint == 21.0
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    assert climate_window.window_open is False
    assert writes_to(gateway, IEEE_A, MODE) == [0, 4]
    assert writes_to(gateway, IEEE_B, MODE) == [0, 4]
    assert writes_to(gateway, IEEE_A, SETPOINT)[-1] == 2100


async def test_a_schedule_thermostat_stays_off_and_says_so_in_repairs(
    hass, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    gateway.inject(IEEE_B, MODE, 1)
    await settle(hass)
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    # The hold switches the schedule thermostat off as well.
    assert writes_to(gateway, IEEE_B, MODE) == [0]
    raised = issue(hass, "schedule_thermostat_off_0022")
    assert raised is not None
    assert raised.severity == IssueSeverity.WARNING
    assert raised.translation_key == "schedule_thermostat_off"
    assert raised.translation_placeholders == {
        "room": "Living",
        "thermostat": "climate.b",
    }
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    # The room resumes A but leaves B off, and the issue stays.
    assert writes_to(gateway, IEEE_A, MODE) == [0, 4]
    assert writes_to(gateway, IEEE_B, MODE) == [0]
    assert issue(hass, "schedule_thermostat_off_0022") is not None
    # The person switches it back to its schedule: the issue resolves itself.
    gateway.inject(IEEE_B, MODE, 1)
    await settle(hass)
    assert issue(hass, "schedule_thermostat_off_0022") is None


async def test_start_clears_a_stale_schedule_thermostat_issue(
    hass, climate_window
) -> None:
    issues.raise_schedule_thermostat_off(
        hass, IEEE_B, room="Living", thermostat="climate.b"
    )
    await climate_window.async_start()
    assert issue(hass, "schedule_thermostat_off_0022") is None


async def test_a_stale_read_back_after_the_hold_is_retried_not_adopted(
    hass, freezer, climate_window, gateway
) -> None:
    adopted = async_capture_events(hass, EVENT_SETPOINT_ADOPTED)
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    gateway.inject(IEEE_A, MODE, 0)
    freezer.tick(timedelta(minutes=9))
    # The thermostat still reads off when the heat is verified.
    gateway.read_override[(IEEE_A, MODE)] = 0
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    assert writes_to(gateway, IEEE_A, MODE) == [0, 4]
    assert climate_window.mode is RoomMode.HEAT
    assert adopted == []
    del gateway.read_override[(IEEE_A, MODE)]
    await advance(hass, freezer, 6, step=1)
    assert writes_to(gateway, IEEE_A, MODE) == [0, 4, 4]
    assert climate_window.mode is RoomMode.HEAT
    assert adopted == []


@pytest.fixture
def climate_window(make_controller, config) -> RoomController:
    return make_controller(
        cfg=with_climate(
            config,
            window_contacts=(CONTACT, "binary_sensor.w2"),
            window_warn=timedelta(minutes=30),
            window_check=timedelta(minutes=10),
        )
    )


async def test_any_open_contact_keeps_the_window_open(
    hass, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    hass.states.async_set(CONTACT, "on")
    hass.states.async_set("binary_sensor.w2", "on")
    await settle(hass)
    assert climate_window.window_open is True
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    assert climate_window.window_open is True
    assert writes_to(gateway, IEEE_A, MODE) == [0]
    hass.states.async_set("binary_sensor.w2", "off")
    await settle(hass)
    assert climate_window.window_open is False


async def test_a_contact_that_is_open_at_start_opens_the_hold_after_the_start(
    hass, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "on")
    hass.states.async_set("binary_sensor.w2", "off")
    await climate_window.async_start()
    await settle(hass)
    assert climate_window.window_open is True
    assert writes_to(gateway, IEEE_A, MODE) == [0]


async def test_unavailable_contacts_break_the_sync_and_say_so_once(
    hass, climate_window, gateway, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.easy_trv.observation")
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    assert climate_window.in_sync is True
    hass.states.async_set(CONTACT, "unavailable")
    await settle(hass)
    assert climate_window.window_open is False
    assert climate_window.in_sync is False

    def contact_lines() -> list[str]:
        return [
            r.getMessage()
            for r in caplog.records
            if CONTACT in r.getMessage() and r.levelno == logging.DEBUG
        ]

    assert len(contact_lines()) == 1
    # Another contact changing does not repeat the line for the silent one.
    hass.states.async_set("binary_sensor.w2", "off", {"battery": 80})
    await settle(hass)
    assert len(contact_lines()) == 1
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    assert climate_window.in_sync is True
    # An unknown or missing contact is the same.
    hass.states.async_set(CONTACT, "unknown")
    await settle(hass)
    assert climate_window.in_sync is False
    hass.states.async_remove(CONTACT)
    await settle(hass)
    assert climate_window.in_sync is False
    assert gateway.writes == []


async def test_a_contact_that_was_never_seen_counts_as_closed(
    hass, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "unavailable")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    assert climate_window.window_open is False
    assert climate_window.in_sync is False
    assert gateway.writes == []


async def test_an_open_contact_that_turns_unavailable_keeps_the_hold(
    hass, freezer, climate_window, gateway
) -> None:
    window_events = async_capture_events(hass, "easy_trv_window_hold")
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    assert climate_window.window_open is True
    assert len(window_events) == 1
    await advance(hass, freezer, 35 * 60, step=60)
    assert issue(hass, "window_open_long_room1") is not None
    hass.states.async_set(CONTACT, "unavailable")
    await settle(hass)
    # It was open and nobody said it closed: the hold and its issues stay.
    assert climate_window.window_open is True
    assert climate_window.in_sync is False
    assert issue(hass, "window_open_long_room1") is not None
    assert len(window_events) == 1
    assert writes_to(gateway, IEEE_A, MODE) == [0]
    # Unavailable and back, still open: nothing changes either.
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    assert climate_window.window_open is True
    assert climate_window.in_sync is True
    assert len(window_events) == 1
    # Only a contact that reports closed ends the hold.
    hass.states.async_set(CONTACT, "unavailable")
    await settle(hass)
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    assert climate_window.window_open is False
    assert issue(hass, "window_open_long_room1") is None
    assert [e.data["state"] for e in window_events] == ["open", "closed"]


# Window open delay


@pytest.fixture
def climate_delay(make_controller, config) -> RoomController:
    return make_controller(
        cfg=with_climate(
            config,
            window_contacts=(CONTACT, "binary_sensor.w2"),
            window_open_delay=timedelta(seconds=30),
        )
    )


async def start_delayed(hass, controller, gateway) -> list:
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, controller, gateway)
    return async_capture_events(hass, "easy_trv_window_hold")


async def test_a_window_opened_for_less_than_the_delay_changes_nothing(
    hass, freezer, climate_delay, gateway
) -> None:
    window_events = await start_delayed(hass, climate_delay, gateway)
    hass.states.async_set(CONTACT, "on")
    await advance(hass, freezer, 10, step=5)
    hass.states.async_set(CONTACT, "off")
    await advance(hass, freezer, 60, step=5)
    assert climate_delay.window_open is False
    assert gateway.writes == []
    assert window_events == []


async def test_a_window_open_for_the_delay_switches_the_room_off_after_it(
    hass, freezer, climate_delay, gateway
) -> None:
    window_events = await start_delayed(hass, climate_delay, gateway)
    hass.states.async_set(CONTACT, "on")
    await advance(hass, freezer, 25, step=5)
    assert climate_delay.window_open is False
    assert gateway.writes == []
    await advance(hass, freezer, 10, step=5)
    assert climate_delay.window_open is True
    assert writes_to(gateway, IEEE_A, MODE) == [0]
    assert [e.data["state"] for e in window_events] == ["open"]
    # Closing is not delayed.
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    assert climate_delay.window_open is False
    assert writes_to(gateway, IEEE_A, MODE) == [0, 4]


async def test_the_delay_starts_again_for_a_window_opened_a_second_time(
    hass, freezer, climate_delay, gateway
) -> None:
    await start_delayed(hass, climate_delay, gateway)
    hass.states.async_set(CONTACT, "on")
    await advance(hass, freezer, 20, step=5)
    hass.states.async_set(CONTACT, "off")
    await advance(hass, freezer, 5, step=5)
    hass.states.async_set(CONTACT, "on")
    await advance(hass, freezer, 25, step=5)
    assert climate_delay.window_open is False
    await advance(hass, freezer, 10, step=5)
    assert climate_delay.window_open is True


async def test_a_second_contact_opening_does_not_restart_the_delay(
    hass, freezer, climate_delay, gateway
) -> None:
    await start_delayed(hass, climate_delay, gateway)
    hass.states.async_set(CONTACT, "on")
    await advance(hass, freezer, 20, step=5)
    hass.states.async_set("binary_sensor.w2", "on")
    # One of the two closing again leaves the other one open.
    hass.states.async_set(CONTACT, "off")
    await advance(hass, freezer, 15, step=5)
    assert climate_delay.window_open is True


async def test_a_contact_open_at_start_applies_without_the_delay(
    hass, climate_delay
) -> None:
    hass.states.async_set(CONTACT, "on")
    hass.states.async_set("binary_sensor.w2", "off")
    await climate_delay.async_start()
    await settle(hass)
    assert climate_delay.window_open is True


async def test_without_a_delay_an_open_window_applies_at_once(
    hass, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    assert climate_window.window_open is True


async def test_stopping_cancels_the_delay(
    hass, freezer, climate_delay, gateway
) -> None:
    await start_delayed(hass, climate_delay, gateway)
    hass.states.async_set(CONTACT, "on")
    await advance(hass, freezer, 5, step=5)
    await climate_delay.async_stop()
    await advance(hass, freezer, 60, step=5)
    assert climate_delay.window_open is False
    assert gateway.writes == []


async def test_a_contact_attribute_change_is_not_a_window_event(
    hass, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "on")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    hass.states.async_set(CONTACT, "on", {"battery": 80})
    await settle(hass)
    assert gateway.writes == []


async def test_window_open_long_issue(hass, freezer, climate_window, gateway) -> None:
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    await advance(hass, freezer, 25 * 60, step=60)
    assert issue(hass, "window_open_long_room1") is None
    await advance(hass, freezer, 10 * 60, step=60)
    raised = issue(hass, "window_open_long_room1")
    assert raised is not None
    assert raised.severity == IssueSeverity.ERROR
    assert raised.translation_key == "window_open_long"
    assert raised.translation_placeholders["room"] == "Living"
    assert int(raised.translation_placeholders["minutes"]) >= 30
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    assert issue(hass, "window_open_long_room1") is None


async def test_window_contact_suspect_issue_and_its_clearing(
    hass, freezer, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    await advance(hass, freezer, 12 * 60, step=60)
    raised = issue(hass, "window_contact_suspect_room1")
    assert raised is not None
    assert raised.severity == IssueSeverity.WARNING
    assert raised.translation_key == "window_contact_suspect"
    assert raised.translation_placeholders["room"] == "Living"
    assert raised.translation_placeholders["drop"] == "0.3"
    assert issue(hass, "window_open_long_room1") is None
    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    assert issue(hass, "window_contact_suspect_room1") is None


async def test_start_clears_stale_window_issues_of_the_room_only(
    hass, climate_window
) -> None:
    for room_id in ("room1", "other"):
        issues.raise_window_open_long(hass, room_id, "x", 5)
        issues.raise_window_contact_suspect(hass, room_id, "x", 5, "0.3")
        issues.raise_window_contact_unavailable(hass, room_id, "x", "w")
    await climate_window.async_start()
    assert issue(hass, "window_open_long_room1") is None
    assert issue(hass, "window_contact_suspect_room1") is None
    assert issue(hass, "window_contact_unavailable_room1") is None
    assert issue(hass, "window_open_long_other") is not None
    assert issue(hass, "window_contact_suspect_other") is not None
    assert issue(hass, "window_contact_unavailable_other") is not None


async def test_window_changes_are_ignored_after_stop(
    hass, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "off")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    await climate_window.async_stop()
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    assert gateway.writes == []
    assert climate_window.window_open is False


# Outdoor cut-off


@pytest.fixture
def climate_outdoor(make_controller, config) -> RoomController:
    return make_controller(
        cfg=with_climate(config, outdoor_cutoff=18.0), outdoor_sensor=OUTDOOR
    )


async def test_outdoor_sensor_drives_the_outdoor_hold(
    hass, climate_outdoor, gateway
) -> None:
    hass.states.async_set(OUTDOOR, "15.0", CELSIUS)
    await start_with_setpoint(hass, climate_outdoor, gateway)
    assert climate_outdoor.outdoor_hold is False
    hass.states.async_set(OUTDOOR, "18.0", CELSIUS)
    await settle(hass)
    assert climate_outdoor.outdoor_hold is True
    assert writes_to(gateway, IEEE_A, MODE) == [0]
    assert writes_to(gateway, IEEE_B, MODE) == [0]
    # Inside the deadband the hold stays.
    hass.states.async_set(OUTDOOR, "17.6", CELSIUS)
    await settle(hass)
    assert climate_outdoor.outdoor_hold is True
    hass.states.async_set(OUTDOOR, "17.0", CELSIUS)
    await settle(hass)
    assert climate_outdoor.outdoor_hold is False
    assert writes_to(gateway, IEEE_A, MODE) == [0, 4]


@pytest.mark.parametrize(
    ("only_colder", "contacts", "watched"),
    [(True, (CONTACT,), True), (False, (CONTACT,), False), (True, (), False)],
)
async def test_the_outdoor_sensor_is_read_for_the_window_warning(  # noqa: PLR0913
    hass, make_controller, config, only_colder, contacts, watched
) -> None:
    hass.states.async_set(OUTDOOR, "12.0", {"unit_of_measurement": "°C"})
    controller = make_controller(
        cfg=with_climate(
            config, window_contacts=contacts, window_warn_only_colder=only_colder
        ),
        outdoor_sensor=OUTDOOR,
    )
    await controller.async_start()
    await settle(hass)
    assert controller._state.outdoor == (12.0 if watched else None)  # noqa: SLF001

    hass.states.async_set(OUTDOOR, "9.5", {"unit_of_measurement": "°C"})
    await settle(hass)
    assert controller._state.outdoor == (9.5 if watched else None)  # noqa: SLF001
    assert controller.outdoor_hold is False


async def test_an_outdoor_temperature_at_start_is_taken_over(
    hass, climate_outdoor
) -> None:
    hass.states.async_set(OUTDOOR, "25", CELSIUS)
    await climate_outdoor.async_start()
    await settle(hass)
    assert climate_outdoor.outdoor_hold is True


async def test_an_unusable_outdoor_sensor_ends_the_hold(
    hass, climate_outdoor, gateway, caplog
) -> None:
    hass.states.async_set(OUTDOOR, "25", CELSIUS)
    await start_with_setpoint(hass, climate_outdoor, gateway)
    assert climate_outdoor.outdoor_hold is True
    for bad in ("unavailable", "unknown", "abc", "nan"):
        hass.states.async_set(OUTDOOR, "25", CELSIUS)
        await settle(hass)
        assert climate_outdoor.outdoor_hold is True
        hass.states.async_set(OUTDOOR, bad)
        await settle(hass)
        assert climate_outdoor.outdoor_hold is False, bad
    assert "outdoor sensor unavailable" in caplog.text


async def test_a_missing_outdoor_sensor_is_unknown(hass, climate_outdoor) -> None:
    await climate_outdoor.async_start()
    await settle(hass)
    assert climate_outdoor.outdoor_hold is False


async def test_outdoor_fahrenheit_is_converted(hass, climate_outdoor) -> None:
    hass.states.async_set(OUTDOOR, "77", {"unit_of_measurement": "°F"})
    await climate_outdoor.async_start()
    await settle(hass)
    assert climate_outdoor.outdoor_hold is True


async def test_the_outdoor_sensor_is_not_tracked_without_a_cutoff(
    hass, make_controller, config
) -> None:
    room = make_controller(cfg=with_climate(config), outdoor_sensor=OUTDOOR)
    hass.states.async_set(OUTDOOR, "30", CELSIUS)
    await room.async_start()
    await settle(hass)
    assert room.outdoor_hold is False


async def test_no_outdoor_tracking_without_a_sensor(
    hass, make_controller, config
) -> None:
    room = make_controller(cfg=with_climate(config, outdoor_cutoff=18.0))
    await room.async_start()
    await settle(hass)
    assert room.outdoor_hold is False


async def test_outdoor_changes_are_ignored_after_stop(
    hass, climate_outdoor, gateway
) -> None:
    hass.states.async_set(OUTDOOR, "15", CELSIUS)
    await start_with_setpoint(hass, climate_outdoor, gateway)
    await climate_outdoor.async_stop()
    hass.states.async_set(OUTDOOR, "30", CELSIUS)
    await settle(hass)
    assert climate_outdoor.outdoor_hold is False
    assert gateway.writes == []


async def test_stop_removes_every_state_listener(hass, make_controller, config) -> None:
    tracked = ("state_changed", "state_reported")
    before = [hass.bus.async_listeners().get(name, 0) for name in tracked]
    room = make_controller(
        cfg=with_climate(config, window_contacts=(CONTACT,), outdoor_cutoff=18.0),
        outdoor_sensor=OUTDOOR,
    )
    await room.async_start()
    await room.async_stop()
    assert [hass.bus.async_listeners().get(name, 0) for name in tracked] == before


# --- Heating state, visible window delay, silent contacts ------------------

SILENT_ISSUE = "window_contact_unavailable_room1"
BALCONY = "Balcony door"
BATH = "Bath window"


async def start_closed(hass, controller, gateway) -> None:
    """Start the room with both contacts closed and named."""
    hass.states.async_set(CONTACT, "off", {"friendly_name": BALCONY})
    hass.states.async_set("binary_sensor.w2", "off", {"friendly_name": BATH})
    await start_with_setpoint(hass, controller, gateway)


def silence(hass, entity_id, state="unavailable") -> None:
    """Make a contact say nothing, keeping its name."""
    name = BALCONY if entity_id == CONTACT else BATH
    hass.states.async_set(entity_id, state, {"friendly_name": name})


@pytest.mark.usefixtures("freezer")
async def test_open_delay_notifies_on_arm_and_cancel(
    hass, climate_delay, gateway
) -> None:
    await start_closed(hass, climate_delay, gateway)
    calls = []
    climate_delay.add_update_listener(
        lambda: calls.append((climate_delay.heating_state, climate_delay.window_off_at))
    )
    assert climate_delay.heating_state is HeatingState.HEATING
    assert climate_delay.window_off_at is None

    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    deadline = dt_util.utcnow() + timedelta(seconds=30)
    assert calls == [(HeatingState.WINDOW_PENDING, deadline)]
    assert climate_delay.window_off_at == deadline
    assert climate_delay.heating_state is HeatingState.WINDOW_PENDING
    assert climate_delay.state_reasons == (
        HeatingState.WINDOW_PENDING,
        HeatingState.HEATING,
    )

    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    assert calls[1:] == [(HeatingState.HEATING, None)]
    assert climate_delay.window_off_at is None
    assert climate_delay.heating_state is HeatingState.HEATING


async def test_open_delay_notifies_when_it_fires(
    hass, freezer, climate_delay, gateway
) -> None:
    await start_closed(hass, climate_delay, gateway)
    hass.states.async_set(CONTACT, "on")
    await advance(hass, freezer, 25, step=5)
    calls = []
    climate_delay.add_update_listener(lambda: calls.append(climate_delay.window_off_at))
    await advance(hass, freezer, 10, step=5)
    assert climate_delay.window_open is True
    assert climate_delay.window_off_at is None
    assert climate_delay.heating_state is HeatingState.OFF_WINDOW
    assert calls
    assert set(calls) == {None}


async def test_a_second_contact_opening_leaves_the_deadline_alone(
    hass, freezer, climate_delay, gateway
) -> None:
    await start_closed(hass, climate_delay, gateway)
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    deadline = climate_delay.window_off_at
    await advance(hass, freezer, 10, step=5)
    hass.states.async_set("binary_sensor.w2", "on")
    await settle(hass)
    assert climate_delay.window_off_at == deadline


async def test_silent_contact_issue_after_ten_minutes(
    hass, freezer, climate_window, gateway
) -> None:
    await start_closed(hass, climate_window, gateway)
    silence(hass, CONTACT)
    await settle(hass)
    assert climate_window.heating_state is HeatingState.RESTRICTED
    assert climate_window.silent_contacts == (CONTACT,)
    assert issue(hass, SILENT_ISSUE) is None
    await advance(hass, freezer, 9 * 60, step=60)
    assert issue(hass, SILENT_ISSUE) is None
    await advance(hass, freezer, 60, step=60)
    raised = issue(hass, SILENT_ISSUE)
    assert raised is not None
    assert raised.translation_key == "window_contact_unavailable"
    assert raised.severity is IssueSeverity.WARNING
    assert raised.is_fixable is False
    assert raised.translation_placeholders == {"room": "Living", "contacts": BALCONY}
    # The heating was never switched off because of it.
    assert climate_window.window_open is False
    assert gateway.writes == []


async def test_a_contact_silent_at_the_start_raises_the_issue_too(
    hass, freezer, climate_window, gateway
) -> None:
    hass.states.async_set(CONTACT, "unavailable")
    hass.states.async_set("binary_sensor.w2", "off")
    await start_with_setpoint(hass, climate_window, gateway)
    assert climate_window.silent_contacts == (CONTACT,)
    assert issue(hass, SILENT_ISSUE) is None
    await advance(hass, freezer, 10 * 60, step=60)
    raised = issue(hass, SILENT_ISSUE)
    assert raised is not None
    # Without a friendly name, Home Assistant names the state after its entity.
    assert raised.translation_placeholders["contacts"] == "w"


async def test_a_missing_contact_is_named_by_its_entity_id(
    hass, freezer, climate_window, gateway
) -> None:
    await start_closed(hass, climate_window, gateway)
    hass.states.async_remove(CONTACT)
    await settle(hass)
    await advance(hass, freezer, 10 * 60, step=60)
    raised = issue(hass, SILENT_ISSUE)
    assert raised is not None
    assert raised.translation_placeholders["contacts"] == CONTACT


async def test_silent_contact_flapping_does_not_restart_timer(
    hass, freezer, climate_window, gateway
) -> None:
    await start_closed(hass, climate_window, gateway)
    silence(hass, CONTACT)  # t0
    await advance(hass, freezer, 5 * 60, step=60)
    hass.states.async_set(CONTACT, "off")  # t0 + 5 min: the timer is cancelled
    await advance(hass, freezer, 60, step=60)
    silence(hass, CONTACT)  # t0 + 6 min: a new silence starts a new timer
    await advance(hass, freezer, 5 * 60, step=60)
    assert issue(hass, SILENT_ISSUE) is None  # t0 + 11 min
    await advance(hass, freezer, 4 * 60, step=60)
    assert issue(hass, SILENT_ISSUE) is None  # t0 + 15 min
    await advance(hass, freezer, 60, step=60)
    assert issue(hass, SILENT_ISSUE) is not None  # t0 + 16 min


async def test_another_silent_contact_does_not_restart_the_timer(
    hass, freezer, climate_window, gateway
) -> None:
    await start_closed(hass, climate_window, gateway)
    silence(hass, CONTACT)
    await advance(hass, freezer, 5 * 60, step=60)
    silence(hass, "binary_sensor.w2")
    await advance(hass, freezer, 5 * 60, step=60)
    raised = issue(hass, SILENT_ISSUE)
    assert raised is not None
    assert raised.translation_placeholders["contacts"] == f"{BALCONY}, {BATH}"


async def test_silent_set_change_reraises_issue(
    hass, freezer, climate_window, gateway
) -> None:
    await start_closed(hass, climate_window, gateway)
    silence(hass, CONTACT)
    await advance(hass, freezer, 10 * 60, step=60)
    raised = issue(hass, SILENT_ISSUE)
    assert raised is not None
    assert raised.translation_placeholders["contacts"] == BALCONY
    calls = []
    climate_window.add_update_listener(
        lambda: calls.append(climate_window.silent_contacts)
    )

    silence(hass, "binary_sensor.w2", "unknown")
    await settle(hass)
    raised = issue(hass, SILENT_ISSUE)
    assert raised is not None
    assert raised.translation_placeholders["contacts"] == f"{BALCONY}, {BATH}"
    assert climate_window.silent_contacts == (CONTACT, "binary_sensor.w2")
    # The set changed, although the room stayed unreliable: listeners hear of it.
    assert calls == [(CONTACT, "binary_sensor.w2")]

    hass.states.async_set(CONTACT, "off")
    await settle(hass)
    raised = issue(hass, SILENT_ISSUE)
    assert raised is not None
    assert raised.translation_placeholders["contacts"] == BATH
    assert climate_window.heating_state is HeatingState.RESTRICTED


async def test_a_state_change_that_keeps_the_silent_set_does_not_raise_it_again(
    hass, freezer, climate_window, gateway, monkeypatch
) -> None:
    await start_closed(hass, climate_window, gateway)
    silence(hass, CONTACT)
    await advance(hass, freezer, 10 * 60, step=60)
    raised = []
    monkeypatch.setattr(
        issues, "raise_window_contact_unavailable", lambda *args: raised.append(args)
    )
    silence(hass, CONTACT, "unknown")
    hass.states.async_set("binary_sensor.w2", "on")
    hass.states.async_set("binary_sensor.w2", "off")
    await settle(hass)
    assert raised == []


async def test_contact_reports_again_clears_issue(
    hass, freezer, climate_window, gateway
) -> None:
    await start_closed(hass, climate_window, gateway)
    silence(hass, CONTACT)
    await advance(hass, freezer, 10 * 60, step=60)
    assert issue(hass, SILENT_ISSUE) is not None
    assert climate_window.heating_state is HeatingState.RESTRICTED
    hass.states.async_set(CONTACT, "off", {"friendly_name": BALCONY})
    await settle(hass)
    assert issue(hass, SILENT_ISSUE) is None
    assert climate_window.silent_contacts == ()
    assert climate_window.heating_state is HeatingState.HEATING
    # Silent again: the issue is not back at once, only after another ten minutes.
    silence(hass, CONTACT)
    await advance(hass, freezer, 9 * 60, step=60)
    assert issue(hass, SILENT_ISSUE) is None
    await advance(hass, freezer, 60, step=60)
    assert issue(hass, SILENT_ISSUE) is not None


async def test_stop_cancels_timers_and_clears_issue(
    hass, freezer, climate_delay, gateway
) -> None:
    window_events = async_capture_events(hass, "easy_trv_window_hold")
    await start_closed(hass, climate_delay, gateway)
    silence(hass, "binary_sensor.w2")
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    assert climate_delay.silent_contacts == ("binary_sensor.w2",)
    assert climate_delay.window_off_at is not None
    calls = []
    climate_delay.add_update_listener(lambda: calls.append(1))

    await climate_delay.async_stop()
    await advance(hass, freezer, 15 * 60, step=60)

    assert issue(hass, SILENT_ISSUE) is None
    assert climate_delay.window_open is False
    assert window_events == []
    assert gateway.writes == []
    assert calls == []


async def test_stop_removes_a_raised_silent_contact_issue(
    hass, freezer, climate_window, gateway
) -> None:
    await start_closed(hass, climate_window, gateway)
    silence(hass, CONTACT)
    await advance(hass, freezer, 10 * 60, step=60)
    assert issue(hass, SILENT_ISSUE) is not None
    await climate_window.async_stop()
    assert issue(hass, SILENT_ISSUE) is None


async def test_a_silent_contact_after_stop_raises_nothing(
    hass, freezer, climate_window, gateway
) -> None:
    await start_closed(hass, climate_window, gateway)
    await climate_window.async_stop()
    silence(hass, CONTACT)
    await advance(hass, freezer, 15 * 60, step=60)
    assert issue(hass, SILENT_ISSUE) is None


async def test_state_changed_event(hass, climate, gateway) -> None:
    state_events = async_capture_events(hass, EVENT_STATE_CHANGED)
    await start_with_setpoint(hass, climate, gateway)
    # Whatever the room was doing when it started is not a change.
    assert state_events == []
    assert climate.heating_state is HeatingState.HEATING

    await climate.async_set_operation(OperatingMode.OFF)
    await settle(hass)
    assert [e.data for e in state_events] == [
        {
            "room": "Living",
            "state": "off_manual",
            "previous": "heating",
            "reasons": ["off_manual"],
        }
    ]
    assert climate.heating_state is HeatingState.OFF_MANUAL

    # The same state again is no change.
    await climate.async_set_operation(OperatingMode.OFF)
    await settle(hass)
    assert len(state_events) == 1


async def test_state_changed_event_lists_every_reason(
    hass, climate_window, gateway
) -> None:
    state_events = async_capture_events(hass, EVENT_STATE_CHANGED)
    await start_closed(hass, climate_window, gateway)
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    assert [e.data for e in state_events] == [
        {
            "room": "Living",
            "state": "off_window",
            "previous": "heating",
            "reasons": ["off_window", "heating"],
        }
    ]


async def test_a_change_of_the_reasons_alone_fires_no_event(
    hass, climate_window, gateway
) -> None:
    state_events = async_capture_events(hass, EVENT_STATE_CHANGED)
    await start_closed(hass, climate_window, gateway)
    hass.states.async_set(CONTACT, "on")
    await settle(hass)
    assert len(state_events) == 1
    await climate_window.async_set_operation(OperatingMode.FROST)
    await settle(hass)
    # Off for the window, frost protection as a further reason.
    assert climate_window.state_reasons == (
        HeatingState.OFF_WINDOW,
        HeatingState.FROST,
    )
    assert len(state_events) == 1


async def test_a_restore_before_the_start_is_not_an_evaluation(hass, climate) -> None:
    state_events = async_capture_events(hass, EVENT_STATE_CHANGED)
    await climate.async_restore(21.0, RoomMode.HEAT, Preset.NONE)
    # Before the start the room knows none of its sensors: that is no state to keep.
    assert climate.state_since is None
    await climate.async_start()
    await settle(hass)
    assert state_events == []
    assert climate.heating_state is HeatingState.HEATING
    assert climate.state_since is not None


async def test_a_room_restored_as_off_starts_off_without_an_event(
    hass, climate
) -> None:
    state_events = async_capture_events(hass, EVENT_STATE_CHANGED)
    await climate.async_restore(21.0, RoomMode.OFF, Preset.NONE)
    await climate.async_start()
    await settle(hass)
    assert state_events == []
    assert climate.heating_state is HeatingState.OFF_MANUAL


async def test_state_since_moves_only_on_change(
    hass, freezer, climate, gateway
) -> None:
    assert climate.state_since is None
    await start_with_setpoint(hass, climate, gateway)
    started = dt_util.utcnow()
    assert climate.state_since == started

    # Events that leave the state alone (every tick re-evaluates it) keep it.
    await advance(hass, freezer, 3 * 60, step=60)
    assert climate.state_since == started

    await climate.async_set_operation(OperatingMode.OFF)
    await settle(hass)
    switched = dt_util.utcnow()
    assert switched == started + timedelta(minutes=3)
    assert climate.state_since == switched

    await advance(hass, freezer, 2 * 60, step=60)
    assert climate.state_since == switched


async def test_the_state_before_the_start_follows_the_facts(
    make_controller, config
) -> None:
    room = make_controller(cfg=replace(config, temp_sensors=()))
    assert room.state_since is None
    assert room.heating_state is HeatingState.HEATING
    assert room.state_reasons == (HeatingState.HEATING,)


async def test_deviations_name_the_thermostats_that_deviate(
    hass, climate, gateway
) -> None:
    await start_with_setpoint(hass, climate, gateway)
    assert climate.deviations == {}
    hass.states.async_set("climate.a", "unavailable")
    await settle(hass)
    assert climate.deviations == {"climate.a": "unavailable"}
    assert climate.heating_state is HeatingState.RESTRICTED
    assert climate.state_reasons == (HeatingState.RESTRICTED, HeatingState.HEATING)
