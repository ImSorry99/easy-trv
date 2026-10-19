import pytest

from custom_components.easy_trv.observation import PresenceInput, presence_home


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
    tracker = PresenceInput(hass, "person.a", unknown_is_away=True, feed=fed.append)

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
    away = PresenceInput(hass, "person.gone", unknown_is_away=True, feed=print)
    home = PresenceInput(hass, "person.gone", unknown_is_away=False, feed=print)
    assert away.start() is True
    assert home.start() is False
    away.stop()
    home.stop()


async def test_unknown_policy_home(hass) -> None:
    fed: list = []
    hass.states.async_set("person.a", "not_home")
    tracker = PresenceInput(hass, "person.a", unknown_is_away=False, feed=fed.append)
    assert tracker.start() is True
    hass.states.async_set("person.a", "unavailable")
    await hass.async_block_till_done()
    tracker.stop()
    assert [event.away for event in fed] == [False]
