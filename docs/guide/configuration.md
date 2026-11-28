# Configuration

[Easy TRV](../../README.md) › [Documentation](README.md)

## Configuration

Rooms live inside the single Easy TRV entry. On the integration page choose **Add room**, then pick how the room is created:

- **From area**: the room takes the name of the area. The next page is prefilled with the area's ZHA thermostats and its temperature and humidity sensors (the thermostats' own temperature readings are left out). Change whatever does not fit.
- **Custom room**: enter a name and, if you like, an area, then choose everything yourself.

The room form asks for:

| Field | Meaning |
|---|---|
| Thermostats | One or more thermostats of the same model that a profile fits. A thermostat can belong to one room only. |
| Temperature sensors | The room's sensors in priority order (drag to reorder). The first usable one is used. Without any sensor Easy TRV does not touch the thermostats' temperature source. |
| Humidity sensors | Stored for later use. Not evaluated yet. |
| Window and door contacts | Optional. `binary_sensor` entities of the class window, door, opening or garage door, from any area. See [Window contacts](windows-and-outdoor.md#window-contacts). |
| Schedule | Optional. A Home Assistant schedule helper: on heats, off lowers the room. See [Schedule](schedule.md#schedule). |

A further page, **Room settings**, lets the room deviate from the global settings (see below). To change a room later, or to remove it, open the room's menu on the integration page (edit or delete). Saving a change reloads the integration.

A room is a device named **Heizung <room>** in German and **<room> heating** in English, for example "Heizung Wohnzimmer" or "Living room heating". A name you gave the device yourself is kept. The entity id of the sensor is suggested as `sensor.<room>_heating_state`, for example `sensor.living_room_heating_state` (an existing entity keeps its id).

**Area.** A room created from an area has that area. For a custom room the area is an optional field of the form, and the form for editing a room has it for every room. The room's device is put into the area and its entities follow the device. Easy TRV does this when it creates the device and at every start for a device that has no area yet, so rooms from older versions get their area too. An area you set on the device page yourself stays, as long as you do not change the room's area. If you choose another area when editing the room, the device moves there; emptying the field leaves the device where it is. Easy TRV never sets the area of a single entity.

### Settings

Every setting exists in three layers, and the most specific one that is set wins:

1. **Built-in default**, the value in the table below.
2. **Global**: the integration's options (**Configure** on the integration page). They apply to all rooms. Every number field shows its value, the built-in default until you change it. A field left at its default or emptied is not stored and follows the built-in default. The sections of the form start collapsed.
3. **Room**: the **Room settings** page of a room. Every choice there has the first option **Global**, and a number field left empty means global too. Only what you change is stored for the room, so a later change of the global value reaches every room that does not set its own.

**Reset.** The last field of the options form, **Reset all settings to their defaults**, sets every global setting back to its built-in default when you submit; the outdoor temperature sensor and the presence entity stay. The room settings of a room you reconfigure end with **Reset all settings of this room to Global**; where the room's presence comes from stays.

| Setting | Range | Built-in default | Meaning |
|---|---|---|---|
| Refresh interval | 1 to 120 minutes | 30 | The room temperature is written again at least this often, even if it has not changed. The TRVZB drops the external value after about two hours without a write. |
| Read-back delay | 2 to 60 seconds | 10 | Wait this long after writing the room temperature, the source, the target or the mode before reading it back, for the fail-safe, the schedule and knob turns. The thermostat's answer to the write already confirms it; settings are not read back. |
| Attempts | 1 to 10 | 3 | How often a failing write is tried in quick succession before Easy TRV retries it once per refresh interval. A thermostat is reported only when its writes keep failing for an hour. |
| Minimum change | 0.0 to 1.0 K | 0.1 | A new room temperature is written only if it differs from the last written value by at least this much. |
| Sensor timeout | 0 (off), or 10 to 1440 minutes | 0 (off) | A sensor that has not reported for this long is ignored although Home Assistant still shows it as available. Off by default, because battery sensors often report only on a change and stay silent for hours; a silent sensor counts as long as it is available. Useful for sensors that report regularly. |
| Thermostat's open-window detection | detection on, detection off, leave as set in the thermostat | on | Switches the thermostat's own detection (see below). |
| Hysteresis | 0.2, 0.4, 0.6, 0.8 or 1.0 K | 0.4 K | The thermostat's temperature hysteresis. Needs firmware 1.3.0 or newer. Written only while the adaptive mode is not on (see below). |
| Adaptive mode | adaptive on, adaptive off, leave as set in the thermostat | on | The thermostat adapts to the room itself. Needs firmware 1.4.4 or newer. |
| Frost protection temperature | 4 to 16 °C | 5 | The temperature the thermostat keeps as its own frost protection. It is also the target of the room thermostat's mode **Frost protection**. |
| Hide thermostat entities | hidden, shown | shown | Hides the climate entity of each thermostat and the six device settings Easy TRV writes, so that only the room's thermostat is left to operate (see below). Hidden entities keep working. |
| Setpoint range | automatic (from the thermostat), manual | automatic | The range of the room thermostat's target (see [Room thermostat](room-thermostat.md#room-thermostat)). Automatic takes the minimum and maximum target temperature the thermostats show in Home Assistant when the room is set up. |
| Minimum / maximum target temperature | 4 to 35 °C in 0.5 K steps, minimum below maximum | 5 / 30 | Only used with the setpoint range **manual**. |
| Delay before a window counts as open (seconds) | 0 (immediately) to 600 | 30 | A contact must stay open this long before the heating is switched off; closing is never delayed (see [Window contacts](windows-and-outdoor.md#window-contacts)). |
| Open-window warning (minutes) | 0 (never) to 1440 | 120 | A Repairs issue after a window has been open this long. |
| Warn only when it is colder outside | only when it is colder outside, always | only when it is colder outside | The open-window warning waits while the outdoor temperature is not below the room temperature (see [Window contacts](windows-and-outdoor.md#window-contacts)). |
| Window contact check (minutes) | 0 (off) to 240 | 30 | After this long, check whether the room cools (see [Window contacts](windows-and-outdoor.md#window-contacts)). |
| Cooling expected by the check (K) | 0.1 to 3.0 | 0.3 | How far the room temperature must have fallen by then. |
| Outdoor cut-off temperature | -10 to 30 °C | empty (off) | The room is switched off from this outdoor temperature on (see [Outdoor cut-off](windows-and-outdoor.md#outdoor-cut-off)). A room can choose **Global**, **Off** or its own threshold. |
| Outdoor temperature sensor (global only) | a temperature sensor | none | The sensor the cut-off reads. |
| Presence entity | a person, device tracker, zone, group, binary sensor or input boolean | none | Where presence comes from (see [Presence](presence.md#presence)). A room chooses **Global**, **None** or its own entity. |
| When presence is unknown | away, home | away | How an unknown or unavailable state of the presence entity counts. |
| While away | lower, off | lower | Lower takes the target down by the setback; off switches the room off like an open window. |
| Away delay (global only) | 0 to 120 minutes | 15 | How long presence must read away before it counts. 0 = away counts at once. Home counts at once. |
| Setback | 0.5 to 10 K in 0.5 K steps | 3 | How far the target goes down while away, never below the frost protection temperature. |
| Setback outside the schedule | 0.5 to 15 K in 0.5 K steps | 3 | How far the target goes down while the room's [schedule](schedule.md#schedule) is off, never below the frost protection temperature. |
| Collect statistics | off, hidden, visible | off | Whether the room has the [statistics sensors](statistics.md#statistics): **off** creates none and removes existing ones, **hidden** creates them and keeps collecting but hides them from lists and automatic dashboards, **visible** shows them. See [Collecting statistics](statistics.md#collecting-statistics). |
| Profile (rooms only) | `auto` or a profile id | `auto` | `auto` takes the first profile that matches all thermostats of the room. Or choose a shipped or [user profile](profiles.md#profiles). The profile is not part of the integration options. |

The thermostat settings (open-window detection, adaptive mode, hysteresis, frost protection temperature) are written once when a room starts, which includes every start after you saved a change. The "leave as set in the thermostat" choices write nothing. Hiding the thermostat entities is the exception among the thermostat settings: nothing is written to the thermostats for it.

**The thermostat's open-window detection.** This is a function of the TRVZB itself, not of Easy TRV, and it has nothing to do with window contacts in Home Assistant. The thermostat treats a temperature drop of more than 1.5 K within 4.5 minutes as an open window: it closes the valve and holds 12 °C, and it ends this pause after 30 minutes at the latest. Easy TRV only switches the detection on or off.

**Adaptive mode and hysteresis.** With the adaptive mode on, the thermostat ignores the hysteresis, so Easy TRV does not write it; it writes the hysteresis only while the room's adaptive mode is not on ("off" or "leave as set in the thermostat"). The adaptive mode is on by default and written to every thermostat with firmware 1.4.4 or newer; a thermostat below 1.4.4 gets the hysteresis instead, but also a firmware warning and the room's heating state is `restricted_unsupported`, until you update the firmware or set the adaptive mode to off or leave (globally or for that room).

**Update note.** The hysteresis no longer has a "leave unchanged" choice: rooms that had it follow the global value now, and a Repairs notice (`settings_migrated`) lists them once after the update, which you can dismiss. "Leave as set in the thermostat" still exists for the open-window detection and the adaptive mode.

**Hiding the thermostat entities.** With **Hide thermostat entities** on (global or per room, off by default) Easy TRV hides, for each thermostat of the room, its ZHA climate entity and the six device settings it writes: external temperature, external temperature source, open-window detection, hysteresis, adaptive mode and frost protection temperature. Nothing else is hidden; child lock, the boost and timer buttons, valve opening, battery and the sensors stay as they are. Settings left "as set in the thermostat" stay visible: with the open-window detection or the adaptive mode on that choice, its entity is not hidden, because you control it there. Hidden entities keep working: Easy TRV reads and writes them as before and automations still see them. They only drop out of automatic dashboards. The setting is applied at every start of the integration, and saving it reloads the integration.

The entities come back when you turn the setting off, when the thermostat or the room is removed from Easy TRV, or when the integration is removed. An entity you showed again by hand stays visible while the setting is on, and one you hid yourself is never shown by Easy TRV. Do not *disable* the ZHA climate entities: a disabled ZHA climate entity has no state and Easy TRV skips that thermostat.
