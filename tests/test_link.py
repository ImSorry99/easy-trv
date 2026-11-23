from datetime import UTC, datetime, timedelta

from custom_components.easy_trv.core.link import (
    UNKNOWN,
    LinkCause,
    LinkInfo,
    is_silent,
    likely_cause,
    placeholders,
)

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


def test_nothing_known_is_unknown() -> None:
    assert likely_cause(LinkInfo(), NOW) is LinkCause.UNKNOWN
    assert not is_silent(LinkInfo(), NOW)


def test_an_empty_battery_comes_first() -> None:
    info = LinkInfo(lqi=10, last_seen=NOW - timedelta(hours=5), battery=15)
    assert likely_cause(info, NOW) is LinkCause.BATTERY


def test_silence_comes_before_a_weak_signal() -> None:
    info = LinkInfo(lqi=10, last_seen=NOW - timedelta(minutes=30), battery=16)
    assert likely_cause(info, NOW) is LinkCause.SILENT


def test_a_weak_signal() -> None:
    info = LinkInfo(lqi=49, last_seen=NOW - timedelta(minutes=29))
    assert likely_cause(info, NOW) is LinkCause.SIGNAL


def test_nothing_stands_out() -> None:
    info = LinkInfo(lqi=50, rssi=-80, last_seen=NOW, battery=90)
    assert likely_cause(info, NOW) is LinkCause.UNCLEAR


def test_silent_after_three_hours() -> None:
    assert not is_silent(LinkInfo(last_seen=NOW - timedelta(minutes=179)), NOW)
    assert is_silent(LinkInfo(last_seen=NOW - timedelta(hours=3)), NOW)


def test_a_clock_ahead_of_now_counts_as_just_heard() -> None:
    info = LinkInfo(last_seen=NOW + timedelta(minutes=5))
    assert placeholders(info, NOW)["silent"] == "0"


def test_placeholders() -> None:
    info = LinkInfo(lqi=80, rssi=-72, last_seen=NOW - timedelta(minutes=90))
    assert placeholders(info, NOW) == {
        "battery": UNKNOWN,
        "lqi": "80",
        "rssi": "-72 dBm",
        "silent": "90",
    }
