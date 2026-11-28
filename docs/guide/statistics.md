# Statistics

[Easy TRV](../../README.md) › [Documentation](README.md)

## Statistics

Home Assistant records what a state does, but not what Easy TRV does. The statistics sensors fill that gap. They count the writes Easy TRV sends to a thermostat, the writes that failed and the times a thermostat dropped out. They also add up the heating time and the time a window was open, and show how far the room is from its target. This is data for later analysis of control quality, Zigbee stability and heating habits.

Home Assistant keeps the long-term statistics of a sensor with a state class for ever. The statistics sensors have one, so their values stay long after the raw history is purged.

All of them are diagnostic sensors on the room's device. They exist only where the setting **Collect statistics** asks for them (see below).

### Collecting statistics

The setting **Collect statistics** decides for each room whether the statistics sensors exist and whether they are hidden. Set it globally in the integration options (section **Statistics**) or for one room in its room settings, where **Global** follows the options. Saving it reloads the integration.

| Value | What the room does |
|---|---|
| Off / Aus | The room has no statistics sensors and collects nothing. Sensors that exist are removed, and their data stops: the long-term statistics already recorded stay in the database (Developer tools > Statistics offers to delete them), but nothing is added. The Heating state sensor is not affected. This is the default. |
| Hidden (keeps collecting) / Ausgeblendet (sammelt weiter) | The sensors exist and collect, and the recorder records them, but they are hidden from lists and automatic dashboards. You can still use them by their entity id, in YAML cards, templates and automations. |
| Visible / Sichtbar | The sensors exist, collect and are shown like any other entity. |

A sensor you show again by hand stays shown while the setting is **hidden**, and one you hid yourself, or that another integration hid (a group with **Hide members**, say), is never changed by Easy TRV, the same as with the [thermostat entities](configuration.md#settings). Switching from **hidden** to **visible** shows the sensors Easy TRV hid; switching to **off** and later back creates them again, see [Notes](#notes) for what happens to their totals.

**Update note.** Version 0.6.0 created the statistics sensors for every room. The update from 0.6.0 or older to 0.6.1 sets **Collect statistics** to **hidden** in the integration options, so the sensors of 0.6.0 keep collecting and drop out of lists and automatic dashboards; choose **visible** to see them again. An installation older than 0.6.0 gets the sensors, hidden, for the first time; choose **off** to remove them. A new installation starts with **off**. The update also raises the version of the stored configuration entry, so Easy TRV 0.6.0 can no longer load it: going back to 0.6.0 is not supported and needs a restore of a backup from before the update.

### Room sensors

| Sensor | Entity id | Unit | Value |
|---|---|---|---|
| Control deviation / Regelabweichung | `sensor.<room>_deviation` | K | The temperature of the active sensor minus the target, to 0.1 K, while the room's mode is heat and no window or outdoor hold is active. Positive: the room is warmer than the target. At any other time it is unknown. Not a total: Home Assistant keeps its mean, minimum and maximum. |
| Heating time / Heizzeit | `sensor.<room>_heating_time` | h | Hours in which at least one thermostat of the room reports `hvac_action: heating`. This is what the thermostat reports, not the time its valve was open (see the first note below). A thermostat that is unavailable does not heat. The sensor is unavailable until a thermostat of the room has reported an `hvac_action`. |
| Window openings / Fensteröffnungen | `sensor.<room>_window_openings` | count | How often the window contacts went from all closed to at least one open. It counts at once, before the opening delay, so a short airing counts too. A contact that is open when Easy TRV starts is not an opening. |
| Window open time / Fenster-offen-Zeit | `sensor.<room>_window_open_time` | h | Hours in which at least one window contact was open, also before the opening delay. |

The two window sensors exist only for rooms with window contacts. A silent contact counts with its last state, as everywhere else (see [Window contacts](windows-and-outdoor.md#window-contacts)).

### Thermostat sensors

Each thermostat of a room has three sensors. `<thermostat>` is the name of the thermostat's device as on its device page, the same name as in the hint of the heating state. In the entity ids, `<trv>` is the part of the thermostat's climate entity id after `climate.`: the sensors of `climate.trv_1` are `sensor.trv_1_writes`, `sensor.trv_1_write_failures` and `sensor.trv_1_dropouts`. Like the room sensors, an existing entity keeps its id.

| Sensor | Entity id | Value |
|---|---|---|
| `<thermostat>` writes / `<thermostat>` Schreibvorgänge | `sensor.<trv>_writes` | Every write request Easy TRV sends to the thermostat: room temperature, source, mode, target and settings. A retry counts again. The read-back after a write does not count, and neither does a write that is not sent because ZHA reports the device as unavailable. |
| `<thermostat>` failed writes / `<thermostat>` Schreibfehler | `sensor.<trv>_write_failures` | Writes the thermostat did not accept: an error, a timeout, or a read-back that shows another value than the one written. A write the device does not support and a fail-safe fallback found by the read-back are not failed writes. |
| `<thermostat>` dropouts / `<thermostat>` Ausfälle | `sensor.<trv>_dropouts` | How often the thermostat's climate entity went from available to unavailable. |

### Totals

Everything except the control deviation is a total (state class `total_increasing`): it only grows. It continues after a restart of Home Assistant and after a reload of the integration, from the last value the sensor showed. A sensor that is created new starts at zero, unless it takes over the entity id of a sensor that was removed in the last 7 days; then it continues from that sensor's last value (see [Notes](#notes)). Easy TRV counts only while Home Assistant runs. A duration that is running is updated about once a minute.

### Notes

- **Heating time is what the thermostats report, not valve-open time.** The heating time counts the time in which a thermostat's climate entity reports `hvac_action: heating`. ZHA derives that from the TRVZB's `running_state`, which the project's research found unreliable: in adaptive mode it can report heating while the valve is closed ([`docs/research/02-trvzb-zha-profil.md`](../research/02-trvzb-zha-profil.md), in German). Adaptive mode is on by default, so the heating time is an upper bound of the time the valves were open, not a measure of it. The valve opening of the thermostat would be a possible source for a later version. Whether the Sonoff TRVZB reports `hvac_action` through ZHA at all, and how it compares with the valve in adaptive mode, is not yet verified on hardware (spike S11 in [`docs/testing-on-device.md`](../testing-on-device.md)). If it reports none, the heating time stays unavailable; the other sensors do not depend on it.
- **A ZHA reload counts as a dropout.** Reloading ZHA sets the thermostats unavailable for a moment. Each of them gets one dropout.
- **A renamed thermostat.** The name of a thermostat is read when the sensors are created. If you rename its device, the sensor names show the new name after the next reload of the integration. Entity ids do not change.
- **A thermostat that leaves a room.** If you remove a thermostat from a room, or move it to another room, its three sensors are removed from the old room when the integration reloads (saving the room does that). The same holds for the two window sensors when the last window contact is removed from a room. The sensors that come back, in the new room or when a window contact is added again, get the same entity ids as before. Home Assistant restores a sensor's last value by entity id and keeps the value of a removed sensor for up to 7 days. So a thermostat that moves to another room, or a window contact that is added back, within 7 days continues its old totals under the same entity id; after more than 7 days its sensors start at zero.

### Using the values

**Statistics graph card.** Put a sensor on a dashboard with the statistics graph card. For a total, choose the statistic `change` to see the value per period, for example the heating time per day:

```yaml
type: statistics-graph
title: Heating time per day
entities:
  - sensor.living_room_heating_time
chart_type: bar
period: day
days_to_show: 30
stat_types:
  - change
```

For the control deviation choose `mean`, `min` and `max` instead.

**Utility meter.** The helper **Utility meter** (Settings > Devices & services > Helpers > Create helper) turns a total into a value that starts at zero every day, week or month. Choose the sensor as the source and the meter reset cycle. The helper counts from the moment you create it; the statistics graph card also shows the time before.

### Keeping the raw history

By default the recorder keeps the raw history of every entity for 10 days (`purge_keep_days`). Long-term statistics stay for ever, but the raw history does not: the exact state changes of the room temperatures, the thermostats' target, mode and `hvac_action`, the window contacts and the heating state. To keep them for analysis, raise the value in `configuration.yaml`, for example:

```yaml
recorder:
  purge_keep_days: 365
```

The database grows with it. For raw data over many years, the optional InfluxDB integration of Home Assistant is one way. It is not part of Easy TRV.
