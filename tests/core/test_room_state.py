from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.easy_trv.core.model import (
    Note,
    OperatingMode,
    RoomConfig,
    RoomState,
    Source,
    TrvConfig,
    TrvHealth,
)
from custom_components.easy_trv.core.room_state import (
    HeatingState,
    StateFacts,
    failsafe_recent,
    heating_state,
    state_facts,
    thermostat_deviations,
)

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
REFRESH = timedelta(minutes=10)

CFG = RoomConfig(
    "room1",
    "Living",
    (
        TrvConfig("climate.a", "00:11", (1, 3, 0)),
        TrvConfig("climate.b", "00:22", (1, 3, 0)),
    ),
    ("sensor.t1",),
    "on",
    0.4,
    "leave",
    7.0,
)
NO_SENSORS = replace(CFG, temp_sensors=())


def facts(**overrides: object) -> StateFacts:
    base: dict[str, object] = {
        "operation": OperatingMode.HEAT,
        "window_hold": False,
        "outdoor_hold": False,
        "window_pending": False,
        "internal_source": False,
        "failsafe_recent": False,
        "deviations": {},
        "silent_contacts": frozenset(),
    }
    return StateFacts(**{**base, **overrides})  # type: ignore[arg-type]


def with_trv(st: RoomState, entity_id: str, **changes: object) -> RoomState:
    trvs = {**st.trvs, entity_id: replace(st.trvs[entity_id], **changes)}
    return replace(st, trvs=trvs)


def test_plain_heating() -> None:
    assert heating_state(facts()) == (HeatingState.HEATING, (HeatingState.HEATING,))


def test_frost() -> None:
    assert heating_state(facts(operation=OperatingMode.FROST)) == (
        HeatingState.FROST,
        (HeatingState.FROST,),
    )


def test_off_manual() -> None:
    assert heating_state(facts(operation=OperatingMode.OFF)) == (
        HeatingState.OFF_MANUAL,
        (HeatingState.OFF_MANUAL,),
    )


def test_fault_wins_over_everything() -> None:
    state, reasons = heating_state(
        facts(
            deviations={"climate.a": "error"},
            operation=OperatingMode.OFF,
            window_hold=True,
        )
    )
    assert state is HeatingState.FAULT
    assert reasons == (
        HeatingState.FAULT,
        HeatingState.OFF_MANUAL,
        HeatingState.OFF_WINDOW,
    )


def test_manual_off_wins_over_holds() -> None:
    assert heating_state(
        facts(operation=OperatingMode.OFF, window_hold=True, outdoor_hold=True)
    ) == (
        HeatingState.OFF_MANUAL,
        (
            HeatingState.OFF_MANUAL,
            HeatingState.OFF_WINDOW,
            HeatingState.OFF_OUTDOOR,
        ),
    )


def test_window_hold_before_outdoor() -> None:
    assert heating_state(facts(window_hold=True, outdoor_hold=True)) == (
        HeatingState.OFF_WINDOW,
        (HeatingState.OFF_WINDOW, HeatingState.OFF_OUTDOOR, HeatingState.HEATING),
    )


def test_window_pending() -> None:
    assert heating_state(facts(window_pending=True)) == (
        HeatingState.WINDOW_PENDING,
        (HeatingState.WINDOW_PENDING, HeatingState.HEATING),
    )


def test_emergency_from_internal_source() -> None:
    state, reasons = heating_state(facts(internal_source=True))
    assert state is HeatingState.EMERGENCY
    assert reasons == (HeatingState.EMERGENCY, HeatingState.HEATING)


def test_emergency_from_failsafe() -> None:
    state, reasons = heating_state(facts(failsafe_recent=True))
    assert state is HeatingState.EMERGENCY
    assert reasons == (HeatingState.EMERGENCY, HeatingState.HEATING)


@pytest.mark.parametrize(
    "reason", ["unavailable", "unsupported", "schedule", "window_phase"]
)
def test_restricted_from_each_deviation(reason: str) -> None:
    state, reasons = heating_state(facts(deviations={"climate.a": reason}))
    assert state is HeatingState.RESTRICTED
    assert reasons == (HeatingState.RESTRICTED, HeatingState.HEATING)


def test_restricted_from_silent_contact() -> None:
    state, reasons = heating_state(
        facts(silent_contacts=frozenset({"binary_sensor.w"}))
    )
    assert state is HeatingState.RESTRICTED
    assert reasons == (HeatingState.RESTRICTED, HeatingState.HEATING)


def test_emergency_before_restricted() -> None:
    assert heating_state(
        facts(internal_source=True, silent_contacts=frozenset({"binary_sensor.w"}))
    ) == (
        HeatingState.EMERGENCY,
        (HeatingState.EMERGENCY, HeatingState.RESTRICTED, HeatingState.HEATING),
    )


def test_placeholders_never_hold() -> None:
    combos = [
        facts(),
        facts(operation=OperatingMode.OFF),
        facts(operation=OperatingMode.FROST, window_hold=True, outdoor_hold=True),
        facts(window_pending=True, internal_source=True, failsafe_recent=True),
        facts(
            deviations={"climate.a": "error", "climate.b": "schedule"},
            silent_contacts=frozenset({"binary_sensor.w"}),
        ),
    ]
    for combo in combos:
        _, reasons = heating_state(combo)
        assert HeatingState.OFF_AWAY not in reasons
        assert HeatingState.BOOST not in reasons


def test_enum_order() -> None:
    assert [s.value for s in HeatingState] == [
        "fault",
        "off_manual",
        "off_window",
        "off_outdoor",
        "off_away",
        "window_pending",
        "emergency",
        "restricted",
        "boost",
        "frost",
        "heating",
    ]


def test_thermostat_deviations_order() -> None:
    st = RoomState.initial(CFG)
    st = with_trv(st, "climate.a", health=TrvHealth.ERROR, note=Note.SCHEDULE)
    st = with_trv(st, "climate.b", health=TrvHealth.OK, note=Note.WINDOW_PHASE)
    assert thermostat_deviations(st) == {
        "climate.a": "error",
        "climate.b": "window_phase",
    }


@pytest.mark.parametrize(
    ("health", "note", "expected"),
    [
        (TrvHealth.ERROR, None, "error"),
        (TrvHealth.UNAVAILABLE, Note.SCHEDULE, "unavailable"),
        (TrvHealth.UNSUPPORTED, Note.WINDOW_PHASE, "unsupported"),
        (TrvHealth.OK, Note.SCHEDULE, "schedule"),
        (TrvHealth.OK, Note.WINDOW_PHASE, "window_phase"),
    ],
)
def test_thermostat_deviation_reasons(
    health: TrvHealth, note: Note | None, expected: str
) -> None:
    st = with_trv(RoomState.initial(CFG), "climate.a", health=health, note=note)
    assert thermostat_deviations(st) == {"climate.a": expected}


def test_healthy_thermostat_is_absent() -> None:
    assert thermostat_deviations(RoomState.initial(CFG)) == {}


def test_state_facts_internal_source() -> None:
    st = replace(RoomState.initial(CFG), source=Source.INTERNAL)
    assert state_facts(
        st, CFG, NOW, REFRESH, window_pending=False, silent_contacts=frozenset()
    ).internal_source
    assert not state_facts(
        st, NO_SENSORS, NOW, REFRESH, window_pending=False, silent_contacts=frozenset()
    ).internal_source
    external = replace(st, source=Source.EXTERNAL)
    assert not state_facts(
        external, CFG, NOW, REFRESH, window_pending=False, silent_contacts=frozenset()
    ).internal_source


def test_state_facts_collects_the_room() -> None:
    st = replace(
        RoomState.initial(CFG),
        window_open=True,
        outdoor_hold=True,
        source=Source.EXTERNAL,
    )
    st = with_trv(st, "climate.b", note=Note.SCHEDULE)
    silent = frozenset({"binary_sensor.w"})
    assert state_facts(
        st, CFG, NOW, REFRESH, window_pending=True, silent_contacts=silent
    ) == StateFacts(
        operation=OperatingMode.HEAT,
        window_hold=True,
        outdoor_hold=True,
        window_pending=True,
        internal_source=False,
        failsafe_recent=False,
        deviations={"climate.b": "schedule"},
        silent_contacts=silent,
    )


def test_failsafe_recent_window() -> None:
    st = RoomState.initial(CFG)
    assert not failsafe_recent(st, NOW, REFRESH)
    edge = with_trv(st, "climate.a", failsafe_seen_at=NOW - REFRESH)
    assert failsafe_recent(edge, NOW, REFRESH)
    stale = with_trv(
        st, "climate.a", failsafe_seen_at=NOW - REFRESH - timedelta(seconds=1)
    )
    assert not failsafe_recent(stale, NOW, REFRESH)
