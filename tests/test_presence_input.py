from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.easy_trv.observation import PresenceInput, presence_home

NOW = timedelta(0)
DELAY = timedelta(minutes=15)


@pytest.mark.parametrize(
    ("domain", "state", "home"),
    [
        ("person", "home", True),
        ("person", "not_home", False),
        ("device_tracker", "Work", False),
        ("device_tracker", "home", True),
        ("zone", "2", True),
        ("zone", "0", False),
        ("group", "home", True),
        ("group", "on", True),
        ("group", "not_home", False),
        ("group", "off", False),
        ("binary_sensor", "on", True),
        ("binary_sensor", "off", False),
        ("input_boolean", "on", True),
        ("input_boolean", "off", False),
        ("person", "unavailable", None),
        ("person", "unknown", None),
        ("zone", "lots", None),
        ("binary_sensor", "unavailable", None),
        ("light", "on", None),
        ("person", None, None),
    ],
)
def test_presence_home(domain, state, home) -> None:
    assert presence_home(domain, state) is home


async def test_presence_input_feeds_changes_only(hass) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = PresenceInput(
        hass, "person.a", unknown_is_away=True, feed=fed.append, delay=NOW
    )

    assert tracker.start() is False
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    hass.states.async_set("person.a", "Work")
    await hass.async_block_till_done()
    hass.states.async_set("person.a", "home")
    await hass.async_block_till_done()
    tracker.stop()
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()

    assert [event.away for event in fed] == [True, False]


async def test_missing_presence_entity_counts_as_unknown(hass) -> None:
    away = PresenceInput(
        hass, "person.gone", unknown_is_away=True, feed=print, delay=NOW
    )
    home = PresenceInput(
        hass, "person.gone", unknown_is_away=False, feed=print, delay=NOW
    )
    assert away.start() is True
    assert home.start() is False
    away.stop()
    home.stop()


async def test_unknown_policy_home(hass) -> None:
    fed: list = []
    hass.states.async_set("person.a", "not_home")
    tracker = PresenceInput(
        hass, "person.a", unknown_is_away=False, feed=fed.append, delay=NOW
    )
    assert tracker.start() is True
    hass.states.async_set("person.a", "unavailable")
    await hass.async_block_till_done()
    tracker.stop()
    assert [event.away for event in fed] == [False]


async def _tick(hass, freezer, minutes: float) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _delayed(hass, fed, *, entity="person.a", unknown_is_away=True, house=None):
    return PresenceInput(
        hass,
        entity,
        unknown_is_away=unknown_is_away,
        feed=fed.append,
        delay=DELAY,
        house=house,
    )


async def test_away_counts_after_the_delay(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed)
    assert tracker.start() is False

    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 14)
    assert fed == []
    await _tick(hass, freezer, 1)
    assert [event.away for event in fed] == [True]
    tracker.stop()


async def test_home_cancels_the_delay(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed)
    tracker.start()

    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 10)
    hass.states.async_set("person.a", "home")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 30)
    assert fed == []
    assert tracker.away_from is None
    tracker.stop()


async def test_home_after_counted_away_is_at_once(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed)
    tracker.start()
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 15)
    hass.states.async_set("person.a", "home")
    await hass.async_block_till_done()
    assert [event.away for event in fed] == [True, False]
    tracker.stop()


async def test_unknown_as_away_is_delayed(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed, unknown_is_away=True)
    tracker.start()

    hass.states.async_set("person.a", "unavailable")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 14)
    assert fed == []
    await _tick(hass, freezer, 1)
    assert [event.away for event in fed] == [True]
    tracker.stop()


async def test_start_away_since_long_counts_at_once(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    freezer.tick(timedelta(hours=3))
    tracker = _delayed(hass, fed)
    assert tracker.start() is True
    assert tracker.away_from is None
    tracker.stop()


async def test_start_away_since_short_waits_for_the_rest(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=10))
    tracker = _delayed(hass, fed)
    assert tracker.start() is False

    await _tick(hass, freezer, 4)
    assert fed == []
    await _tick(hass, freezer, 1)
    assert [event.away for event in fed] == [True]
    tracker.stop()


async def test_zero_delay_is_at_once(hass) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = PresenceInput(
        hass, "person.a", unknown_is_away=True, feed=fed.append, delay=NOW
    )
    tracker.start()
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    assert [event.away for event in fed] == [True]
    tracker.stop()


async def test_stop_cancels_the_timer(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed)
    tracker.start()
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    tracker.stop()
    await _tick(hass, freezer, 30)
    assert fed == []
    assert tracker.away_from is None


async def test_away_from_is_set_while_waiting(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed)
    tracker.start()
    assert tracker.away_from is None

    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    assert tracker.away_from == dt_util.utcnow() + DELAY
    await _tick(hass, freezer, 15)
    assert tracker.away_from is None
    tracker.stop()


async def test_set_delay_applies_to_the_next_away(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed)
    tracker.start()
    tracker.set_delay(timedelta(minutes=5))
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    assert [event.away for event in fed] == [True]
    tracker.stop()


class _House:
    def __init__(self) -> None:
        self.forced: bool | None = None
        self.callbacks: list = []

    def forced_away(self) -> bool | None:
        return self.forced

    def subscribe(self, callback):
        self.callbacks.append(callback)
        return lambda: self.callbacks.remove(callback)

    def set(self, *, forced: bool | None) -> None:
        self.forced = forced
        for callback in list(self.callbacks):
            callback()


async def test_forced_presence_wins_at_once(hass, freezer) -> None:
    fed: list = []
    house = _House()
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed, house=house)
    assert tracker.start() is False

    house.set(forced=True)
    assert [event.away for event in fed] == [True]
    house.set(forced=None)
    assert [event.away for event in fed] == [True, False]
    house.set(forced=False)
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 20)
    assert [event.away for event in fed] == [True, False]

    tracker.stop()
    assert house.callbacks == []


async def test_release_after_a_long_away_counts_at_once(hass, freezer) -> None:
    fed: list = []
    house = _House()
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    freezer.tick(timedelta(hours=1))
    tracker = _delayed(hass, fed, house=house)
    assert tracker.start() is True

    house.set(forced=False)
    house.set(forced=None)
    assert [event.away for event in fed] == [False, True]
    assert tracker.away_from is None
    tracker.stop()


async def test_release_measures_the_delay_from_the_flip(hass, freezer) -> None:
    fed: list = []
    house = _House()
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed, house=house)
    tracker.start()
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    house.set(forced=False)
    await _tick(hass, freezer, 5)
    house.set(forced=None)
    await _tick(hass, freezer, 4)
    assert fed == []
    await _tick(hass, freezer, 1)
    assert [event.away for event in fed] == [True]
    tracker.stop()


async def test_set_delay_applies_to_a_running_delay(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed)
    tracker.start()
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 6)
    tracker.set_delay(timedelta(minutes=5))
    await hass.async_block_till_done()
    assert [event.away for event in fed] == [True]
    tracker.stop()


async def test_set_delay_longer_moves_a_running_delay(hass, freezer) -> None:
    fed: list = []
    hass.states.async_set("person.a", "home")
    tracker = _delayed(hass, fed)
    tracker.start()
    hass.states.async_set("person.a", "not_home")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 6)
    tracker.set_delay(timedelta(minutes=30))
    await _tick(hass, freezer, 20)
    assert fed == []
    await _tick(hass, freezer, 3)
    assert fed == []
    await _tick(hass, freezer, 1)
    assert [event.away for event in fed] == [True]
    tracker.stop()
