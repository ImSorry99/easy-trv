import asyncio
import json
import logging
from dataclasses import replace
from datetime import timedelta

import pytest
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueSeverity
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.easy_trv.const import (
    DOMAIN,
    EVENT_FAILSAFE,
    EVENT_RECOVERED,
    EVENT_WRITE_FAILED,
)
from custom_components.easy_trv.controller import RoomController
from custom_components.easy_trv.core.model import (
    GlobalParams,
    RoomConfig,
    RoomStatus,
    TrvConfig,
)
from custom_components.easy_trv.profiles.loader import load_profiles_sync
from tests.fake_gateway import FakeGateway

IEEE_A = "00:11"
IEEE_B = "00:22"
WINDOW = 0x6000
HYSTERESIS = 0x6011
SOURCE = 0x600E
VALUE = 0x600D
CELSIUS = {"unit_of_measurement": "°C"}
PARAMS = replace(GlobalParams.defaults(), verify_delay=0.0)
# Enough loop iterations for the worker to run a queue of fake-gateway
# operations (each one yields about once) to completion.
SETTLE_ITERATIONS = 50


class SpyGateway(FakeGateway):
    """FakeGateway that also logs write and read calls in one sequence."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    async def write(self, ieee, endpoint, cluster, attribute, value) -> None:
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
    )


@pytest.fixture
async def make_controller(hass, gateway, profile, config):
    """Build controllers for the room and stop them again after the test."""
    created: list[RoomController] = []

    def make(*, cfg=None, params=PARAMS, prof=None) -> RoomController:
        refs = {ref.entity_id: ref for ref in gateway.devices.values()}
        controller = RoomController(
            hass, cfg or config, params, prof or profile, gateway, refs
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
        (IEEE_B, WINDOW, 1),
        (IEEE_B, HYSTERESIS, -40),
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
    assert gateway.calls == ["write", "read"] * 8
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
    assert raised.translation_placeholders["room"] == "Living"
    assert raised.translation_placeholders["trv"] == "climate.a"
    assert raised.translation_placeholders["attempts"] == "3"
    assert issue(hass, "trv_unreachable_0022") is None
    assert [e.data["attribute"] for e in write_failed] == ["external_temperature"]
    [record] = [r for r in caplog.records if "did not accept" in r.getMessage()]
    assert record.levelno == logging.WARNING
    # The core's message already names the room; the controller adds nothing.
    assert record.getMessage().startswith(
        "Living: climate.a did not accept external_temperature after 3 attempts"
    )
    assert record.getMessage().count("Living:") == 1


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
        "trv": "climate.a",
        "trv_name": "climate.a",
        "capability": "hysteresis",
        "required": "1.3.0",
        "installed": "1.2.1",
    }
    assert issue(hass, "trv_firmware_0022") is None
    # Hysteresis never reaches the old device, everything else still does.
    assert written(gateway) == [
        (IEEE_A, WINDOW, 1),
        (IEEE_B, WINDOW, 1),
        (IEEE_B, HYSTERESIS, -40),
        (IEEE_A, SOURCE, 1),
        (IEEE_B, SOURCE, 1),
        (IEEE_A, VALUE, 2130),
        (IEEE_B, VALUE, 2130),
    ]
    assert health(controller) == {"climate.a": "unsupported", "climate.b": "ok"}
    assert controller.status == RoomStatus.DEGRADED


async def test_profile_without_capability_is_unsupported(
    hass, make_controller, gateway, profile
) -> None:
    capabilities = {
        name: spec
        for name, spec in profile.capabilities.items()
        if name != "hysteresis"
    }
    controller = make_controller(prof=replace(profile, capabilities=capabilities))
    await controller.async_start()
    await settle(hass)
    for ieee in ("0011", "0022"):
        raised = issue(hass, f"trv_firmware_{ieee}")
        assert raised is not None
        assert raised.translation_placeholders["capability"] == "set_hysteresis"
        # The profile has no such capability, so no version is required.
        assert raised.translation_placeholders["required"] == "unknown"
        assert raised.translation_placeholders["installed"] == "1.3.0"
    assert writes_to(gateway, IEEE_A, HYSTERESIS) == []
    assert writes_to(gateway, IEEE_A, VALUE) == [2130]
    assert health(controller) == {
        "climate.a": "unsupported",
        "climate.b": "unsupported",
    }


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
    assert len(gateway.writes) == 7
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
    assert set(snapshot) == {"config", "params", "state"}
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
