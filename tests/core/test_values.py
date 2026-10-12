import pytest

from custom_components.easy_trv.core import values as v


@pytest.mark.parametrize(
    ("celsius", "raw"),
    [
        (21.35, 2140),
        (21.34, 2130),
        (0.0, 0),
        (99.9, 9990),
        (19.9, 1990),
        (21.25, 2130),
    ],
)
def test_to_centi_rounds_half_up(celsius: float, raw: int) -> None:
    assert v.to_centi(celsius) == raw


@pytest.mark.parametrize(
    ("state", "unit", "expected"),
    [
        ("21.3", "°C", 21.3),
        ("70.0", "°F", pytest.approx(21.11, abs=0.01)),
        ("unknown", "°C", None),
        ("unavailable", "°C", None),
        ("abc", "°C", None),
        ("21.3", None, None),
        ("21.3", "K", None),
        ("21.3", "C", 21.3),
    ],
)
def test_parse_temperature_units(
    state: str, unit: str | None, expected: float | None
) -> None:
    assert v.parse_temperature(state, unit) == expected


@pytest.mark.parametrize(
    ("sw", "expected"),
    [
        ("0x00001404", (1, 4, 4)),
        ("0x1300", (1, 3, 0)),
        ("0x00001201", (1, 2, 1)),
        (None, None),
        ("garbage", None),
        ("1.4.4", (1, 4, 4)),
    ],
)
def test_parse_zha_sw_version(
    sw: str | None, expected: tuple[int, int, int] | None
) -> None:
    assert v.parse_zha_sw_version(sw) == expected


def test_parse_version_string() -> None:
    assert v.parse_version_string("1.2.1") == (1, 2, 1)
    with pytest.raises(ValueError):  # noqa: PT011
        v.parse_version_string("1.2")


def test_is_in_range_inclusive() -> None:
    assert v.is_in_range(0.0, 0.0, 99.9)
    assert v.is_in_range(99.9, 0.0, 99.9)
    assert not v.is_in_range(-0.1, 0.0, 99.9)
    assert not v.is_in_range(100.0, 0.0, 99.9)
