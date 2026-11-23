"""Radio health of a thermostat: what the Zigbee stack last knew about it.

Pure: the adapter fills :class:`LinkInfo` from its cache (no radio traffic),
this module only judges it. The thresholds are deliberately coarse. LQI and
RSSI mean different things on different coordinators, so "last heard" is the
main signal and a low LQI only a hint.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Final

#: At or below this battery level (percent) the battery is the likely cause.
BATTERY_LOW: Final = 15
#: Below this LQI the signal is the likely cause.
LQI_WEAK: Final = 50
#: Heard nothing for this long while writes fail: the thermostat is silent.
SILENT_WHILE_FAILING: Final = timedelta(minutes=30)
#: Heard nothing for this long at all: report it even when nothing is written.
#: Half of ZHA's own six hours after which it marks a battery device unavailable.
SILENT_AFTER: Final = timedelta(hours=3)

#: Placeholder of a value the stack does not know.
UNKNOWN: Final = "\N{EN DASH}"


@dataclass(frozen=True)
class LinkInfo:
    """What the Zigbee stack last knew about a thermostat; ``None``: unknown."""

    lqi: int | None = None
    rssi: int | None = None
    last_seen: datetime | None = None
    battery: int | None = None  # percent

    @property
    def known(self) -> bool:
        """Return whether the stack knew anything at all."""
        return any(
            value is not None
            for value in (self.lqi, self.rssi, self.last_seen, self.battery)
        )

    def silent_for(self, now: datetime) -> timedelta | None:
        """Return how long nothing was heard; ``None`` when unknown."""
        if self.last_seen is None:
            return None
        return max(now - self.last_seen, timedelta(0))

    def as_dict(self) -> dict[str, int | str | None]:
        """Return the values as JSON-able data (for diagnostics)."""
        return {
            "lqi": self.lqi,
            "rssi": self.rssi,
            "last_seen": None if self.last_seen is None else self.last_seen.isoformat(),
            "battery": self.battery,
        }


class LinkCause(Enum):
    """The likely reason a thermostat does not answer."""

    BATTERY = "battery"
    SILENT = "silent"
    SIGNAL = "signal"
    UNCLEAR = "unclear"  # values known, none stands out
    UNKNOWN = "unknown"  # the stack knew nothing


def likely_cause(info: LinkInfo, now: datetime) -> LinkCause:
    """Return the likely reason writes to a thermostat fail.

    An empty battery explains everything else, so it comes first; then a
    thermostat that has not been heard while writes failed; then a weak signal.
    """
    if not info.known:
        return LinkCause.UNKNOWN
    if info.battery is not None and info.battery <= BATTERY_LOW:
        return LinkCause.BATTERY
    silent = info.silent_for(now)
    if silent is not None and silent >= SILENT_WHILE_FAILING:
        return LinkCause.SILENT
    if info.lqi is not None and info.lqi < LQI_WEAK:
        return LinkCause.SIGNAL
    return LinkCause.UNCLEAR


def is_silent(info: LinkInfo, now: datetime) -> bool:
    """Return whether nothing was heard from the thermostat for too long."""
    silent = info.silent_for(now)
    return silent is not None and silent >= SILENT_AFTER


def placeholders(info: LinkInfo, now: datetime) -> dict[str, str]:
    """Return the radio values as Repairs placeholders, :data:`UNKNOWN` where unknown.

    ``silent`` is how long nothing was heard, in whole minutes.
    """
    silent = info.silent_for(now)
    return {
        "battery": UNKNOWN if info.battery is None else f"{info.battery} %",
        "lqi": UNKNOWN if info.lqi is None else str(info.lqi),
        "rssi": UNKNOWN if info.rssi is None else f"{info.rssi} dBm",
        "silent": UNKNOWN if silent is None else str(int(silent.total_seconds() // 60)),
    }
