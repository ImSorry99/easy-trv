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
    internal_source,
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
        "thermostat_reasons": frozenset(),
        "silent_contacts": frozenset(),
    }
    return StateFacts(**{**base, **overrides})  # type: ignore[arg-type]


def with_trv(st: RoomState, entity_id: str, **changes: object) -> RoomState:
    trvs = {**st.trvs, entity_id: replace(st.trvs[entity_id], **changes)}
    return replace(st, trvs=trvs)


def room_facts(st: RoomState) -> StateFacts:
    """The facts of a room in ``st`` with the default config and nothing outside."""
    return state_facts(
        replace(st, source=Source.EXTERNAL),
        CFG,
        NOW,
        REFRESH,
        window_pending=False,
        silent_contacts=frozenset(),
    )


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
    ("reason", "expected"),
    [
        ("unavailable", HeatingState.RESTRICTED_UNAVAILABLE),
        ("unsupported", HeatingState.RESTRICTED_UNSUPPORTED),
        ("schedule", HeatingState.RESTRICTED_SCHEDULE),
        ("window_phase", HeatingState.RESTRICTED_WINDOW_PHASE),
    ],
)
def test_restricted_from_each_thermostat_reason_alone(
    reason: str, expected: HeatingState
) -> None:
    state, reasons = heating_state(facts(thermostat_reasons=frozenset({reason})))
    assert state is expected
    assert reasons == (expected, HeatingState.HEATING)


def test_restricted_from_silent_contact() -> None:
    state, reasons = heating_state(
        facts(silent_contacts=frozenset({"binary_sensor.w"}))
    )
    assert state is HeatingState.RESTRICTED_CONTACT
    assert reasons == (HeatingState.RESTRICTED_CONTACT, HeatingState.HEATING)


def test_the_five_restrictions_in_priority_order() -> None:
    state, reasons = heating_state(
        facts(
            thermostat_reasons=frozenset(
                {"window_phase", "schedule", "unsupported", "unavailable"}
            ),
            silent_contacts=frozenset({"binary_sensor.w"}),
        )
    )
    assert state is HeatingState.RESTRICTED_UNAVAILABLE
    assert reasons == (
        HeatingState.RESTRICTED_UNAVAILABLE,
        HeatingState.RESTRICTED_CONTACT,
        HeatingState.RESTRICTED_UNSUPPORTED,
        HeatingState.RESTRICTED_SCHEDULE,
        HeatingState.RESTRICTED_WINDOW_PHASE,
        HeatingState.HEATING,
    )


def test_a_contact_restricts_before_a_schedule() -> None:
    state, reasons = heating_state(
        facts(
            thermostat_reasons=frozenset({"schedule"}),
            silent_contacts=frozenset({"binary_sensor.w"}),
        )
    )
    assert state is HeatingState.RESTRICTED_CONTACT
    assert reasons == (
        HeatingState.RESTRICTED_CONTACT,
        HeatingState.RESTRICTED_SCHEDULE,
        HeatingState.HEATING,
    )


@pytest.mark.parametrize(
    ("health", "note", "expected"),
    [
        (TrvHealth.UNAVAILABLE, None, HeatingState.RESTRICTED_UNAVAILABLE),
        (TrvHealth.UNSUPPORTED, None, HeatingState.RESTRICTED_UNSUPPORTED),
        (TrvHealth.OK, Note.SCHEDULE, HeatingState.RESTRICTED_SCHEDULE),
        (TrvHealth.OK, Note.WINDOW_PHASE, HeatingState.RESTRICTED_WINDOW_PHASE),
    ],
)
def test_restricted_from_each_deviation(
    health: TrvHealth, note: Note | None, expected: HeatingState
) -> None:
    st = with_trv(
        RoomState.initial(CFG),
        "climate.a",
        health=health,
        note=note,
        unsupported=(
            frozenset({"hysteresis"})
            if health is TrvHealth.UNSUPPORTED
            else frozenset()
        ),
    )
    assert heating_state(room_facts(st)) == (
        expected,
        (expected, HeatingState.HEATING),
    )


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        (Note.SCHEDULE, HeatingState.RESTRICTED_SCHEDULE),
        (Note.WINDOW_PHASE, HeatingState.RESTRICTED_WINDOW_PHASE),
    ],
)
def test_fault_and_note_both_in_reasons(note: Note, expected: HeatingState) -> None:
    st = with_trv(
        RoomState.initial(CFG), "climate.a", health=TrvHealth.ERROR, note=note
    )
    assert heating_state(room_facts(st)) == (
        HeatingState.FAULT,
        (HeatingState.FAULT, expected, HeatingState.HEATING),
    )


def test_fault_and_schedule_note_both_in_reasons() -> None:
    st = RoomState.initial(CFG)
    st = with_trv(st, "climate.a", health=TrvHealth.ERROR, note=Note.SCHEDULE)
    st = with_trv(st, "climate.b", health=TrvHealth.OK)
    facts_ = room_facts(st)
    assert facts_.deviations == {"climate.a": "error"}
    assert facts_.thermostat_reasons == {"schedule"}
    assert heating_state(facts_) == (
        HeatingState.FAULT,
        (HeatingState.FAULT, HeatingState.RESTRICTED_SCHEDULE, HeatingState.HEATING),
    )


def test_fault_alone_does_not_restrict() -> None:
    st = with_trv(RoomState.initial(CFG), "climate.a", health=TrvHealth.ERROR)
    assert room_facts(st).thermostat_reasons == frozenset()
    assert heating_state(room_facts(st)) == (
        HeatingState.FAULT,
        (HeatingState.FAULT, HeatingState.HEATING),
    )


def test_a_thermostat_in_error_that_lacks_a_function_is_restricted_too() -> None:
    st = with_trv(
        RoomState.initial(CFG),
        "climate.a",
        health=TrvHealth.ERROR,
        unsupported=frozenset({"hysteresis"}),
    )
    facts_ = room_facts(st)
    assert facts_.deviations == {"climate.a": "error"}
    assert facts_.thermostat_reasons == {"unsupported"}
    assert heating_state(facts_) == (
        HeatingState.FAULT,
        (HeatingState.FAULT, HeatingState.RESTRICTED_UNSUPPORTED, HeatingState.HEATING),
    )


def test_an_unavailable_thermostat_that_lacks_a_function_is_restricted_twice() -> None:
    st = with_trv(
        RoomState.initial(CFG),
        "climate.a",
        health=TrvHealth.UNAVAILABLE,
        unsupported=frozenset({"hysteresis"}),
    )
    assert room_facts(st).thermostat_reasons == {"unavailable", "unsupported"}


def test_a_function_the_profile_never_had_restricts_nothing() -> None:
    st = with_trv(
        RoomState.initial(CFG),
        "climate.a",
        unsupported=frozenset({"hysteresis"}),
        not_in_profile=frozenset({"hysteresis"}),
    )
    assert room_facts(st).thermostat_reasons == frozenset()
    assert heating_state(room_facts(st)) == (
        HeatingState.HEATING,
        (HeatingState.HEATING,),
    )


def test_every_reason_of_a_thermostat_counts() -> None:
    # Unavailable with a note: both reasons hold, though ``deviations`` shows one.
    st = with_trv(
        RoomState.initial(CFG),
        "climate.a",
        health=TrvHealth.UNAVAILABLE,
        note=Note.SCHEDULE,
    )
    facts_ = room_facts(st)
    assert facts_.deviations == {"climate.a": "unavailable"}
    assert facts_.thermostat_reasons == {"unavailable", "schedule"}
    assert heating_state(facts_) == (
        HeatingState.RESTRICTED_UNAVAILABLE,
        (
            HeatingState.RESTRICTED_UNAVAILABLE,
            HeatingState.RESTRICTED_SCHEDULE,
            HeatingState.HEATING,
        ),
    )


def test_the_reasons_of_all_thermostats_are_collected() -> None:
    st = RoomState.initial(CFG)
    st = with_trv(
        st, "climate.a", health=TrvHealth.UNSUPPORTED, unsupported=frozenset({"x"})
    )
    st = with_trv(st, "climate.b", note=Note.WINDOW_PHASE)
    assert room_facts(st).thermostat_reasons == {"unsupported", "window_phase"}


def test_emergency_before_restricted() -> None:
    assert heating_state(
        facts(internal_source=True, silent_contacts=frozenset({"binary_sensor.w"}))
    ) == (
        HeatingState.EMERGENCY,
        (
            HeatingState.EMERGENCY,
            HeatingState.RESTRICTED_CONTACT,
            HeatingState.HEATING,
        ),
    )


def test_placeholders_never_hold() -> None:
    combos = [
        facts(),
        facts(operation=OperatingMode.OFF),
        facts(operation=OperatingMode.FROST, window_hold=True, outdoor_hold=True),
        facts(window_pending=True, internal_source=True, failsafe_recent=True),
        facts(
            deviations={"climate.a": "error", "climate.b": "schedule"},
            thermostat_reasons=frozenset({"schedule"}),
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
        "restricted_unavailable",
        "restricted_contact",
        "restricted_unsupported",
        "restricted_schedule",
        "restricted_window_phase",
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


def test_internal_source_helper() -> None:
    st = RoomState.initial(CFG)
    assert internal_source(replace(st, source=Source.INTERNAL), CFG)
    assert internal_source(replace(st, source=Source.NONE), CFG)
    assert not internal_source(replace(st, source=Source.EXTERNAL), CFG)
    assert not internal_source(replace(st, source=Source.INTERNAL), NO_SENSORS)


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
        thermostat_reasons=frozenset({"schedule"}),
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
