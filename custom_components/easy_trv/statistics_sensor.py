"""Diagnostic sensors with the statistics of a room and its thermostats.

Home Assistant records what a state does but not what Easy TRV does: how often
it wrote to a thermostat, how often a write failed, how often a thermostat
dropped out, how long the window was open or the valves heated. The room's
controller counts and times that; these sensors publish it, so the recorder
keeps it as long-term statistics.

The counters and durations are totals that only grow (``total_increasing``) and
carry on after a restart: each sensor hands its last value back to the
controller before the room starts, and shows the highest value it has shown.
"""

import math
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntityDescription,
    SensorExtraStoredData,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback, split_entity_id
from homeassistant.util import slugify

from custom_components.easy_trv.labels import thermostat_name

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.helpers.device_registry import DeviceInfo

    from custom_components.easy_trv.controller import (
        RoomController,
        RoomStatistics,
        ThermostatStatistics,
    )

# What a sensor reads from the room: a count, hours, a temperature difference,
# or nothing.
type Reading = float | int | None


@dataclass(frozen=True, kw_only=True)
class RoomStatistic(SensorEntityDescription):
    """A sensor of the room: how to read it and when the room has it."""

    read: Callable[[RoomStatistics], Reading]
    # Only rooms with window contacts have the value.
    needs_contacts: bool = False


@dataclass(frozen=True, kw_only=True)
class ThermostatStatistic(SensorEntityDescription):
    """A sensor of one thermostat: how to read it."""

    read: Callable[[ThermostatStatistics], Reading]


ROOM_STATISTICS: tuple[RoomStatistic, ...] = (
    RoomStatistic(
        key="deviation",
        native_unit_of_measurement=UnitOfTemperature.KELVIN,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        read=lambda stats: stats.deviation,
    ),
    RoomStatistic(
        key="heating_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        read=lambda stats: stats.heating_hours,
    ),
    RoomStatistic(
        key="window_openings",
        state_class=SensorStateClass.TOTAL_INCREASING,
        needs_contacts=True,
        read=lambda stats: stats.window_openings,
    ),
    RoomStatistic(
        key="window_open_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        needs_contacts=True,
        read=lambda stats: stats.window_open_hours,
    ),
)

THERMOSTAT_STATISTICS: tuple[ThermostatStatistic, ...] = (
    ThermostatStatistic(
        key="writes",
        state_class=SensorStateClass.TOTAL_INCREASING,
        read=lambda stats: stats.writes,
    ),
    ThermostatStatistic(
        key="write_failures",
        state_class=SensorStateClass.TOTAL_INCREASING,
        read=lambda stats: stats.write_failures,
    ),
    ThermostatStatistic(
        key="dropouts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        read=lambda stats: stats.dropouts,
    ),
)


def room_unique_id(subentry_id: str, key: str) -> str:
    """Return the unique id of a statistic of the room."""
    return f"{subentry_id}_{key}"


def thermostat_unique_id(subentry_id: str, climate_entity_id: str, key: str) -> str:
    """Return the unique id of a statistic of one thermostat of the room.

    Raises:
        ValueError: If ``climate_entity_id`` is not an entity id.

    """
    object_id = split_entity_id(climate_entity_id)[1]
    return f"{subentry_id}_{object_id}_{key}"


def statistics_unique_ids(
    subentry_id: str, thermostats: Iterable[str], *, contacts: bool
) -> set[str]:
    """Return the unique ids of the statistics sensors a room has.

    A room has the window sensors only with ``contacts``, and one set of sensors
    for each of its ``thermostats``. A thermostat that is not an entity id (the
    data of a room that cannot be set up may hold anything) has none.
    """
    unique_ids = {
        room_unique_id(subentry_id, description.key)
        for description in ROOM_STATISTICS
        if contacts or not description.needs_contacts
    }
    for climate_entity_id in thermostats:
        with suppress(ValueError):
            unique_ids.update(
                thermostat_unique_id(subentry_id, climate_entity_id, description.key)
                for description in THERMOSTAT_STATISTICS
            )
    return unique_ids


def room_statistics_unique_ids(subentry_id: str, stats: RoomStatistics) -> set[str]:
    """Return the unique ids of the statistics sensors of a room that is set up."""
    return statistics_unique_ids(
        subentry_id, stats.per_thermostat, contacts=stats.window_openings is not None
    )


def is_statistics_unique_id(subentry_id: str, unique_id: str) -> bool:
    """Return whether a unique id is one a statistics sensor of the room can have.

    Whether the sensor is wanted is another question: this only tells the
    statistics sensors from the room's other entities.
    """
    prefix = f"{subentry_id}_"
    if not unique_id.startswith(prefix):
        return False
    own = unique_id.removeprefix(prefix)
    if own in {description.key for description in ROOM_STATISTICS}:
        return True
    return any(
        own.endswith(f"_{description.key}") and own != f"_{description.key}"
        for description in THERMOSTAT_STATISTICS
    )


def statistics_sensors(
    hass: HomeAssistant,
    controller: RoomController,
    subentry: ConfigSubentry,
    device_info: DeviceInfo,
) -> list[StatisticSensor]:
    """Return the statistics sensors of a room: its own and one set per thermostat.

    A room without window contacts has no window sensors, as the value does not
    exist for it. The heating time is always there; it is unavailable while no
    thermostat of the room reports what it is doing.
    """
    stats = controller.statistics()
    sensors: list[StatisticSensor] = [
        RoomStatisticSensor(controller, description, subentry, device_info)
        for description in ROOM_STATISTICS
        if not description.needs_contacts or stats.window_openings is not None
    ]
    for climate_entity_id in stats.per_thermostat:
        label = thermostat_name(hass, climate_entity_id)
        sensors.extend(
            ThermostatStatisticSensor(
                controller,
                description,
                subentry,
                device_info,
                climate_entity_id,
                label,
            )
            for description in THERMOSTAT_STATISTICS
        )
    return sensors


class StatisticSensor(RestoreSensor):
    """A statistic of the room's controller, shown as a diagnostic sensor.

    A total (a counter or a duration) is restored from the value it showed
    before a restart and never shows less than it did before. A measurement,
    the control deviation, takes nothing back: it is only meaningful while the
    room heats.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(
        self,
        controller: RoomController,
        description: RoomStatistic | ThermostatStatistic,
        device_info: DeviceInfo,
        read: Callable[[], Reading],
        restore: Callable[[float], None],
    ) -> None:
        """Link the sensor to the room's controller and device.

        ``read`` returns what the room reports for this sensor now, ``restore``
        hands the controller the total the sensor showed before a restart.
        """
        self._controller = controller
        self.entity_description = description
        self._read = read
        self._restore = restore
        self._attr_translation_key = description.key
        self._attr_device_info = device_info
        self._total = description.state_class is SensorStateClass.TOTAL_INCREASING
        self._duration = description.device_class is SensorDeviceClass.DURATION
        # What the sensor shows: for a total the highest value it has shown.
        self._shown: Reading = None

    @property
    def native_value(self) -> Reading:
        """Return the value shown, which for a total never falls."""
        return self._shown

    @property
    def available(self) -> bool:
        """Return whether there is something to show.

        A total the room cannot measure (the heating time while no thermostat
        reports an action) is unavailable, not stuck at what it was.
        """
        return not self._total or self._read() is not None

    @property
    def extra_restore_state_data(self) -> SensorExtraStoredData:
        """Return the value to restore: what the room counts now, if more.

        Taken at the moment of the dump, so a duration that is running loses no
        time between its last update and a reload or a shutdown.
        """
        return SensorExtraStoredData(self._latest(), self.native_unit_of_measurement)

    def _latest(self) -> Reading:
        """Return the value to show now: for a total, never less than shown."""
        fresh = self._read()
        if not self._total:
            return fresh
        known = [value for value in (self._shown, fresh) if value is not None]
        return max(known, default=None)

    async def async_added_to_hass(self) -> None:
        """Take back the last total, then follow the room.

        The restore reaches the controller before the room starts and before
        the first state is written, so the room counts on from the total.
        """
        await super().async_added_to_hass()
        if self._total:
            await self._take_back_total()
        self._shown = self._latest()
        self.async_on_remove(self._controller.add_update_listener(self._handle_update))

    async def _take_back_total(self) -> None:
        """Hand the last value to the controller and show it, as it was."""
        data = await self.async_get_last_sensor_data()
        if data is None:
            return
        stored = data.native_value
        if (
            not isinstance(stored, (int, float))
            or isinstance(stored, bool)
            or not math.isfinite(stored)
        ):
            return
        self._restore(float(stored))
        # The controller keeps a duration in seconds: converted back it can be a
        # float step below the value that was shown, which must not drop.
        if self._duration:
            self._shown = float(stored)

    @callback
    def _handle_update(self) -> None:
        """Take the room's value, then write the state."""
        self._shown = self._latest()
        self.async_write_ha_state()


class RoomStatisticSensor(StatisticSensor):
    """A statistic of the room as a whole."""

    def __init__(
        self,
        controller: RoomController,
        description: RoomStatistic,
        subentry: ConfigSubentry,
        device_info: DeviceInfo,
    ) -> None:
        """Name the sensor after the room and its key."""
        super().__init__(
            controller,
            description,
            device_info,
            read=lambda: description.read(controller.statistics()),
            restore=lambda total: controller.restore_statistic(description.key, total),
        )
        self._attr_unique_id = room_unique_id(subentry.subentry_id, description.key)
        # Suggest the entity id: HA would put the area in front of the device
        # name. Only used while the unique id is not registered.
        self.entity_id = f"sensor.{slugify(subentry.title)}_{description.key}"


class ThermostatStatisticSensor(StatisticSensor):
    """A statistic of one thermostat of the room."""

    def __init__(  # noqa: PLR0913 - the room, the thermostat and its device
        self,
        controller: RoomController,
        description: ThermostatStatistic,
        subentry: ConfigSubentry,
        device_info: DeviceInfo,
        climate_entity_id: str,
        label: str,
    ) -> None:
        """Name the sensor after the thermostat and its key."""
        super().__init__(
            controller,
            description,
            device_info,
            read=lambda: description.read(
                controller.statistics().per_thermostat[climate_entity_id]
            ),
            restore=lambda total: controller.restore_statistic(
                description.key, total, climate_entity_id
            ),
        )
        self._attr_translation_placeholders = {"thermostat": label}
        self._attr_unique_id = thermostat_unique_id(
            subentry.subentry_id, climate_entity_id, description.key
        )
        object_id = split_entity_id(climate_entity_id)[1]
        self.entity_id = f"sensor.{object_id}_{description.key}"
