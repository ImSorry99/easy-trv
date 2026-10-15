# Easy TRV

Easy TRV is a Home Assistant integration that lets Sonoff TRVZB radiator thermostats regulate on the temperature of the room instead of the temperature at the radiator. It groups thermostats and temperature sensors into rooms, writes the room temperature to the thermostats' own external-sensor input, and checks by read-back that every write arrived.

It works native first: the thermostat keeps regulating by itself, Home Assistant only feeds it a value and watches over it. There is no regulator and no entity per setting. Each room gets one status sensor, one thin room thermostat (`climate`) that sets the same target and mode on all its thermostats, and an operating mode (`select`: off, frost protection, heating enabled).

Status: version 0.4.0 is released (room thermostat with operating mode, window contacts, outdoor cut-off); see the [`CHANGELOG`](CHANGELOG.md) for what each version brought. See [`REQUIREMENTS.md`](REQUIREMENTS.md) for what Easy TRV must do and [`docs/README.md`](docs/README.md) for the documentation map.

## Supported devices

| Device | Integration | Firmware | Notes |
|---|---|---|---|
| Sonoff TRVZB (Gen 1) | ZHA | 1.2.1 or newer | Hysteresis needs 1.3.0 or newer, the adaptive mode 1.4.4 or newer. Targeted firmware: 1.3.0 and 1.4.4. The first hardware run is pending (see [`docs/testing-on-device.md`](docs/testing-on-device.md)). |

Home Assistant 2026.8 or newer is required. The thermostats must already be paired with ZHA. Use ZHA's built-in quirk for the TRVZB; do not add a custom quirk for the same model, the two conflict.

Other thermostats, Zigbee2MQTT and other Zigbee integrations are not supported.

## Installation

1. In HACS, open the menu (three dots) and choose **Custom repositories**.
2. Add `https://github.com/ImSorry99/easy-trv` with the type **Integration**.
3. Download **Easy TRV** and restart Home Assistant.
4. Go to **Settings > Devices & services > Add integration** and add **Easy TRV**. It can be added once; it has no setup form.

## Configuration

Rooms live inside the single Easy TRV entry. On the integration page choose **Add room**, then pick how the room is created:

- **From area**: the room takes the name of the area. The next page is prefilled with the area's ZHA thermostats and its temperature and humidity sensors (the thermostats' own temperature readings are left out). Change whatever does not fit.
- **Custom room**: enter a name, then choose everything yourself.

The room form asks for:

| Field | Meaning |
|---|---|
| Thermostats (ZHA) | One or more ZHA thermostats of a supported model. A thermostat can belong to one room only. |
| Temperature sensors | The room's sensors in priority order (drag to reorder). The first usable one is used. Without any sensor Easy TRV does not touch the thermostats' temperature source. |
| Humidity sensors | Stored for later use. Not evaluated yet. |
| Window and door contacts | Optional. `binary_sensor` entities of the class window, door, opening or garage door, from any area. See [Window contacts](#window-contacts). |

A further page, **Room settings**, lets the room deviate from the global settings (see below). To change a room later, or to remove it, open the room's menu on the integration page (edit or delete). Saving a change reloads the integration.

A room is a device named **Heizung <room>** in German and **<room> heating** in English, for example "Heizung Wohnzimmer" or "Living room heating". A room created from an area is placed in that area; for custom rooms, or to move an existing device, set the area on the device page. A name you gave the device yourself is kept. The status sensor's entity id is suggested as `sensor.<room>_heating_status`, for example `sensor.living_room_heating_status` (an existing entity keeps its id).

### Settings

Every setting exists in three layers, and the most specific one that is set wins:

1. **Built-in default**, the value in the table below.
2. **Global**: the integration's options (**Configure** on the integration page). They apply to all rooms. Every number field shows its value, the built-in default until you change it. A field left at its default or emptied is not stored and follows the built-in default. The sections of the form start collapsed.
3. **Room**: the **Room settings** page of a room. Every choice there has the first option **Global**, and a number field left empty means global too. Only what you change is stored for the room, so a later change of the global value reaches every room that does not set its own.

| Setting | Range | Built-in default | Meaning |
|---|---|---|---|
| Refresh interval | 1 to 120 minutes | 20 | The room temperature is written again at least this often, even if it has not changed. The TRVZB drops the external value after about two hours without a write. |
| Read-back delay | 2 to 60 seconds | 10 | Wait this long after a write before reading the value back. The thermostat is a sleepy device and may need a moment. |
| Attempts | 1 to 10 | 3 | How often a failing write is tried before the thermostat is reported as not responding. |
| Minimum change | 0.0 to 1.0 K | 0.1 | A new room temperature is written only if it differs from the last written value by at least this much. |
| Sensor timeout | 0 (off), or 10 to 1440 minutes | 120 | A sensor that has not reported for this long is ignored although Home Assistant still shows it as available. `0` switches the timeout off. |
| Thermostat's open-window detection | detection on, detection off, leave as set in the thermostat | on | Switches the thermostat's own detection (see below). |
| Hysteresis | 0.2, 0.4, 0.6, 0.8 or 1.0 K | 0.4 K | The thermostat's temperature hysteresis. Needs firmware 1.3.0 or newer. Written only while the adaptive mode is not on (see below). |
| Adaptive mode | adaptive on, adaptive off, leave as set in the thermostat | on | The thermostat adapts to the room itself. Needs firmware 1.4.4 or newer. |
| Frost protection temperature | 4 to 16 °C | 5 | The temperature the thermostat keeps as its own frost protection. It is also the target of the room's operating mode **Frost protection**. |
| Setpoint range | automatic (from the thermostat), manual | automatic | The range of the room thermostat's target (see [Room thermostat](#room-thermostat)). Automatic takes the minimum and maximum target temperature the thermostats show in Home Assistant when the room is set up. |
| Minimum / maximum target temperature | 4 to 35 °C in 0.5 K steps, minimum below maximum | 5 / 30 | Only used with the setpoint range **manual**. |
| Delay before a window counts as open (seconds) | 0 (immediately) to 600 | 30 | A contact must stay open this long before the heating is switched off; closing is never delayed (see [Window contacts](#window-contacts)). |
| Open-window warning (minutes) | 0 (never) to 1440 | 120 | A Repairs issue after a window has been open this long. |
| Warn only when it is colder outside | only when it is colder outside, always | only when it is colder outside | The open-window warning waits while the outdoor temperature is not below the room temperature (see [Window contacts](#window-contacts)). |
| Window contact check (minutes) | 0 (off) to 240 | 30 | After this long, check whether the room cools (see [Window contacts](#window-contacts)). |
| Cooling expected by the check (K) | 0.1 to 3.0 | 0.3 | How far the room temperature must have fallen by then. |
| Outdoor cut-off temperature | -10 to 30 °C | empty (off) | The room is switched off from this outdoor temperature on (see [Outdoor cut-off](#outdoor-cut-off)). A room can choose **Global**, **Off** or its own threshold. |
| Outdoor temperature sensor (global only) | a temperature sensor | none | The sensor the cut-off reads. |
| Profile (rooms only) | `auto` or a profile id | `auto` | `auto` takes the first profile that matches all thermostats of the room. Or choose a shipped or [user profile](#profiles). The profile is not part of the integration options. |

The thermostat settings (open-window detection, adaptive mode, hysteresis, frost protection temperature) are written once when a room starts, which includes every start after you saved a change. The "leave as set in the thermostat" choices write nothing.

**The thermostat's open-window detection.** This is a function of the TRVZB itself, not of Easy TRV, and it has nothing to do with window contacts in Home Assistant. The thermostat treats a temperature drop of more than 1.5 K within 4.5 minutes as an open window: it closes the valve and holds 12 °C, and it ends this pause after 30 minutes at the latest. Easy TRV only switches the detection on or off.

**Adaptive mode and hysteresis.** With the adaptive mode on, the thermostat ignores the hysteresis, so Easy TRV does not write it; it writes the hysteresis only while the room's adaptive mode is not on ("off" or "leave as set in the thermostat"). The adaptive mode is on by default and written to every thermostat with firmware 1.4.4 or newer; a thermostat below 1.4.4 gets the hysteresis instead, but also a firmware warning and the room shows degraded, until you update the firmware or set the adaptive mode to off or leave (globally or for that room).

**Update note.** The hysteresis no longer has a "leave unchanged" choice: rooms that had it follow the global value now, and a Repairs notice (`settings_migrated`) lists them once after the update, which you can dismiss. "Leave as set in the thermostat" still exists for the open-window detection and the adaptive mode.

Hide the ZHA climate entities of the single thermostats (entity settings > Visible off), so that only the room's thermostat is left to operate. Do not disable them: a disabled ZHA climate entity has no state and Easy TRV skips that thermostat. Easy TRV never changes the entity registry itself, so the ZHA entities keep working until you hide them.

## Room thermostat

Each room has one `climate.<room>_heating` (for example `climate.living_room_heating`) as soon as the room's profile can set the target temperature and the mode. The shipped profile can. It is a pass-through: it stores what a person asked for and writes it to every thermostat of the room, with the same verified writes as everything else. It does not regulate and it shows no `hvac_action`.

- **Modes:** `heat` and `off`. Off switches every thermostat off (their own frost protection keeps the radiator safe) and stops the external-temperature writes; heat resyncs mode, source, temperature and target.
- **No presets.** Frost protection is an operating mode of the room (below); in it the room thermostat shows `heat` at the frost protection temperature.
- **Range:** with the setpoint range **automatic** (the default), the `min_temp` and `max_temp` the thermostats show in Home Assistant when the room is set up; with several thermostats the narrowest range that all of them accept. A thermostat that shows no limits is left out, and without any the profile's range applies (4 to 35 °C for the TRVZB). With **manual**, the room's minimum and maximum target temperature. Either way the range is narrowed to the profile's limits, in 0.5 K steps for the TRVZB. A changed limit on a thermostat is read at the next reload of the integration.
- **`current_temperature`:** the value last sent to the thermostats, else the active sensor's.
- **Attributes:** `setpoint_source` (`ha`, `device:<entity_id>`, `restore`, `frost` or `initial`), `in_sync`, `thermostats`, `window_open`, `outdoor_cutoff` and `note`.
- **`in_sync`** is true when every thermostat is verified at what it should show now (off during a hold).

### Operating mode

Each room with a room thermostat also has `select.<room>_heating_mode` (for example `select.living_room_heating_mode`, named **Operating mode** / **Betriebsart**) with three options:

| Option | What the room does |
|---|---|
| `off` (Off / Aus) | The room thermostat is `off`: every thermostat is switched off. |
| `frost` (Frost protection / Frostschutz) | The room heats only to its frost protection temperature (default 5 °C, can be set per room). The target from before is remembered. |
| `heat` (Heating enabled / Heizbetrieb freigegeben) | The room heats to its target. Coming from frost protection, the target from before is back. |

Off ends frost protection, so heating enabled afterwards brings back the target from before, not 5 °C. A new target, in Home Assistant or at a knob, ends frost protection too (the selection shows `heat`). A window or the outdoor cut-off holds the room as before; the selection shows what the room returns to. The operating mode survives a restart.

**Update note.** Up to 0.3.0 frost protection was the preset `frost` of the room thermostat. Automations that call `climate.set_preset_mode` need `select.select_option` on the room's operating mode instead. A room that was on frost protection keeps it after the update.

<details>
<summary>Knob turns, schedules and restarts</summary>

- **The last person who changes it wins**, in Home Assistant or at a knob. A knob turn on one thermostat is taken over as the room's target and spread to the other thermostats once the knob has rested for 3 seconds (turning through several steps is one change). A value outside the range is clamped, also on the thermostat that was turned. The event `easy_trv_setpoint_adopted` fires and `setpoint_source` becomes `device:<entity_id>`. The 3 second spread and the rule below that a knob turn while the room is off switches it on are not yet verified on hardware (spike S8 in [`docs/testing-on-device.md`](docs/testing-on-device.md)).
- **Knob turn while the room is off** switches the room on (`heat`), but only a turn made after the room went off. A turn made just before is not taken as a wish.
- **During an open window or the outdoor cut-off** a knob turn changes only the wish: the thermostats stay off (the room thermostat keeps showing the wish) and the new target is written when the hold ends.
- **Schedule.** A thermostat that is switched to its weekly schedule (`auto`) is shown (`note: schedule`, listed in the status sensor), left alone and excluded from the room's writes until it is back on `heat` or `off`, when it is brought up to date. Easy TRV never writes `auto`. A window or the outdoor cut-off is the exception: it switches such a thermostat off too (also one that is switched to its schedule during the hold), and it stays off afterwards (see [Window contacts](#window-contacts)).
- **Open-window phase of the thermostat.** A thermostat that shows exactly 12 °C while the room wants something else and the room's open-window detection is on is treated as being in its own window phase (`note: window_phase`). It is left alone until it shows another value or 30 minutes have passed.
- **Restart.** The remembered target, mode and operating mode win; a thermostat that reports something else is corrected. A thermostat whose mode is not known yet is only switched off, and only when a hold is active. If the room thermostat entity is disabled in Home Assistant, there is nothing to restore from: the room starts as on a first start.
- **First start** (nothing remembered): the room takes the target of the first thermostat that reports one (`setpoint_source: initial`) and writes it to the others, so the setpoints are unified. It starts off only if every thermostat reports off; otherwise the thermostats that report off are switched on.

</details>

## Window contacts

Choose any number of window, door, opening or garage door `binary_sensor` entities in the room form. They may belong to any area: the kitchen's window can belong to the living room when the two are joined.

- **Opening delay.** A contact must stay open for the delay (default 30 seconds, 0 = immediately) before the window counts as open: airing for a moment switches nothing off. Closing is never delayed. A contact that is open when Easy TRV starts counts at once.
- **Any contact open:** all thermostats are switched off (`system_mode off`), those on their weekly schedule and in their own open-window phase included, and the room remembers its mode and target. `window_open` is true, the event `easy_trv_window_hold` fires.
- **All contacts closed:** the previous mode and target come back. Changes made in Home Assistant during the hold are applied now. A thermostat that was on its schedule stays off, because Easy TRV never writes `auto`: the Repairs issue `schedule_thermostat_off` asks you to switch it back to its schedule in ZHA and disappears when it reports `auto` or `heat` again.
- **No time limit.** After the open-window warning (default 120 minutes, 0 = never) a Repairs issue `window_open_long` is raised. By default it is raised only while it is colder outside than the room's active sensor shows (setting **Warn only when it is colder outside**, needs the outdoor temperature sensor of the integration options); if it gets colder later, it is raised then, and with the outdoor temperature unknown it is raised as before. Set it to **Always** to warn regardless of the outdoor temperature. The heating is switched off while the window is open either way.
- **Stuck contact hint.** If the room temperature has not fallen by the expected cooling (default 0.3 K) after the window contact check (default 30 minutes, 0 = off), the Repairs issue `window_contact_suspect` says the contact may be stuck or its battery flat. The hold continues; you decide.
- **A contact that becomes unavailable or unknown keeps its last state:** one that was open stays open (nobody said it was closed), so the hold and its Repairs issues stay. A contact that has never reported a state counts as closed. `in_sync` is false while a contact says neither open nor closed; the log notes the change once at debug level.

<details>
<summary>Restart and Home Assistant down</summary>

- After a restart during a hold the room comes back in the hold: it looks at the contacts, and no heat is written while one is open.
- If Home Assistant is down during a hold, the thermostats stay off with their own frost protection until it is back. This is the trade-off of "off and restore" (as opposed to a timer on the thermostat).
- The thermostat's own open-window detection is independent of the contacts and stays as set.

</details>

## Outdoor cut-off

Choose an outdoor temperature sensor in the integration options (**Windows and outdoor**) and a cut-off temperature, globally or per room (a room can also choose **Off**). A room is switched off at or above the threshold, like with an open window, and switched on again when the outdoor temperature falls below the threshold minus 0.5 K. There is no averaging. An unavailable sensor ends the cut-off and writes a warning to the log. The state shows as `outdoor_cutoff`, the event `easy_trv_outdoor_hold` fires. A window hold and the cut-off are independent: the room returns when both are clear.

## How it behaves

- **Active sensor.** A sensor is usable if it is available, shows a number in °C (°F is converted), lies between 0 and 99.9 °C, and has reported within the sensor timeout. The first usable sensor in the room's order is the active one.
- **Writes.** When the active sensor's value changes by at least the minimum change, the value is written to every thermostat of the room after a 5 second pause that merges bursts of reports. Independently it is written again every refresh interval. Writes do not depend on the thermostat's own mode, but while the room is switched off or held by a window or the cut-off, no temperature is written; it follows when the room heats again.
- **Verification.** Every write is read back after the read-back delay and compared. A write that does not arrive is retried with a growing pause (5, 15, then 45 seconds) up to the configured number of attempts. After that the thermostat is marked as failing (see the status sensor, Repairs and events below). The write that failed is sent again at every refresh interval, and a verified write of that capability clears the problem by itself. A failing thermostat never blocks the others in the room, and writes within a room happen one at a time so the Zigbee network is not flooded.
- **Fail-safe.** When a TRVZB gets no external value for about two hours it silently falls back to its internal sensor. ZHA still shows the external source as on. Easy TRV reads the source flag back together with every value write, notices the fallback, switches the external source on again, writes the value and fires `easy_trv_failsafe_detected`.
- **No usable sensor.** If none of the room's sensors is usable, the thermostats are switched to their internal sensor and the room is `degraded`. When a sensor is usable again, the external source is switched on again.
- **Unavailable thermostats.** A thermostat that Home Assistant reports as unavailable is skipped without an issue and brought up to date when it returns.

Rooms start once Home Assistant has fully started.

## What you see

### Status sensor

Each room is a device ("Heizung Wohnzimmer", see above) with one diagnostic sensor, for example `sensor.living_room_heating_status`.

| State | Meaning |
|---|---|
| `ok` | All thermostats work and, if the room has sensors, the external source is active. |
| `degraded` | No usable sensor although some are configured, a thermostat is unavailable or lacks a firmware feature, or a fail-safe was handled within the last refresh interval. |
| `error` | At least one thermostat did not accept a write after all attempts. |

Attributes:

| Attribute | Content |
|---|---|
| `active_sensor` | Entity id of the sensor in use, or empty. |
| `source` | `external`, `internal` or `none` (no sensors configured, nothing switched yet). |
| `trv_health` | Per thermostat: `ok`, `unavailable`, `unsupported` or `error`. |
| `last_error` | The last write error of a failing thermostat, or empty. |
| `window_open`, `outdoor_cutoff` | Whether a window or the outdoor cut-off holds the room. |
| `setpoint_source`, `in_sync` | As on the room thermostat. |
| `schedule_thermostats` | Thermostats that run their own schedule. |

### Repairs

| Issue | Severity | Raised when | Cleared |
|---|---|---|---|
| `trv_unreachable` | Error | A thermostat did not accept a write after all attempts. The message names the setting (for example "Hysteresis") and the thermostat as you know it from the device page. Check battery and Zigbee signal. | When the write that failed is verified (Easy TRV sends it again at every refresh interval, and also when the thermostat returns after being unavailable), or on a reload of the integration (raised again if the problem persists). |
| `trv_firmware` | Warning | The thermostat's firmware is too old for a setting (hysteresis below 1.3.0, adaptive mode below 1.4.4), or the thermostat reports the setting as unsupported. The message names the setting, the thermostat and the required and installed firmware. Easy TRV does not retry it. | On a reload of the integration, for example after a firmware update (raised again if the problem persists). |
| `settings_migrated` | Warning | After the update to Phase 2a: rooms that had "leave unchanged" for the hysteresis now follow the global hysteresis. The notice lists them. | When you dismiss it. |
| `window_open_long` | Error | A window has been open longer than the window warning. | When the contacts report closed, or on a reload of the integration (raised again if the window is still open). |
| `window_contact_suspect` | Warning | A contact reports open but the room did not cool by the drop within the check time. | When the contacts report closed, or on a reload of the integration. |
| `schedule_thermostat_off` | Warning | A window or the outdoor cut-off switched off a thermostat that was running its own schedule. It stays off after the hold, because Easy TRV never writes `auto`. The message names the thermostat and the room. | When the thermostat reports `auto` or `heat` again, or on a reload of the integration (the room then brings it to `heat`). |
| `zha_api_incompatible` | Error | Easy TRV cannot use the installed ZHA's internal API. Setup is retried. | When setup works. |
| `profile_invalid` | Warning | A user profile file is invalid and is ignored. The issue names the file and the problem. | When the file loads cleanly after you fix it and reload the integration. |
| `room_invalid` | Warning | A room could not be set up (for example a thermostat that is not a ZHA device, no profile that fits it, or room data that cannot be read). The other rooms keep running. | When the room sets up cleanly after you fix it and reload the integration. |

Issues of thermostats that no longer belong to any room are removed when the integration loads.

### Events

Events are fired on the Home Assistant bus (Developer tools > Events) and can trigger automations.

| Event | When |
|---|---|
| `easy_trv_write_failed` | A thermostat did not accept a write after all attempts. |
| `easy_trv_recovered` | A thermostat that was failing accepted a write again. |
| `easy_trv_failsafe_detected` | A thermostat had fallen back to its internal sensor and was switched back. |

Payload of these three: `room`, `room_id`, `trv_entity_id`, `ieee`. `easy_trv_write_failed` adds `attribute` (the capability that failed, for example `external_temperature`), `attempts` and `error`.

Three more events report the room thermostat and the holds. `easy_trv_setpoint_adopted` (`room`, `trv`, `value`) fires when a knob turn or mode change at a thermostat becomes the room's. `easy_trv_window_hold` (`room`, `state`: `open` or `closed`) and `easy_trv_outdoor_hold` (`room`, `state`: `on` or `off`) fire when a hold starts or ends.

### Debug log

With debug logging on for `custom_components.easy_trv` (Settings > Devices & services > Easy TRV > three dots > Enable debug logging), every operation produces one line when its result is known, for example `Living room: climate.trv_1 external_temperature attr 0x600D raw=2140 -> ok`: room, thermostat, capability, attribute, raw value written and the result (`ok`, `mismatch expected [...] got ...`, `failed: ...`, `signal failsafe`). An operation that is not sent at all (firmware too old, for instance) is logged as `... not sent -> unsupported ...`.

### Diagnostics

**Settings > Devices & services > Easy TRV > Download diagnostics** contains the options, the loaded profiles and skipped profile files, and for every room its configuration, the resolved settings (what the room runs with after the three layers), its full state (including timestamps and attempt counters), the holds (window and outdoor), the notes of thermostats on a schedule or in a window phase, and the latest observations per thermostat (kind, capability, raw value, time). It includes the thermostats' IEEE addresses and entity ids, so look through it before posting it publicly.

## Profiles

Easy TRV talks to a thermostat through a profile, a small YAML file that says which device it fits, from which firmware, and which attributes to write and read back. The shipped profile is `sonoff_trvzb_native` in [`custom_components/easy_trv/profiles/builtin/`](custom_components/easy_trv/profiles/builtin/). Shipped profiles are never edited in place. The `sonoff_trvzb_native` profile targets firmware 1.3.0 and 1.4.4; the first run on real hardware is pending (see [`docs/testing-on-device.md`](docs/testing-on-device.md)).

To adapt one:

1. Copy the shipped file to `<config>/easy_trv/profiles/<name>.yaml` (create the folder if it does not exist).
2. Change `id` to a new, unique id (it starts with a lowercase letter and may contain lowercase letters, digits and underscores). An id that is already taken by a shipped or earlier profile is rejected.
3. Make your changes and reload the Easy TRV integration.
4. Select the new profile in the room (Profile field).

An invalid file is ignored and raises a `profile_invalid` Repairs issue that names the file and the problem. The format is described in [`docs/concept/profiles.md`](docs/concept/profiles.md), the shipped profile in [`docs/profiles/sonoff-trvzb-native.md`](docs/profiles/sonoff-trvzb-native.md).

Firmware minimums are part of a profile. Below the profile's minimum firmware nothing is written to a thermostat and a Repairs issue names the required version. A single capability can have a higher minimum (hysteresis needs 1.3.0, the adaptive mode 1.4.4): that capability is skipped and reported, while the temperature writes continue. The skip lasts until the integration is reloaded, for example after a firmware update. A profile must declare the room temperature (`set_room_temperature`) and the source (`set_source`); one without them is rejected with a `profile_invalid` issue. A setting for which the profile declares no capability at all (for example a profile copied from 0.1.0 without the adaptive mode) is no firmware problem: it is skipped without a Repairs issue, logged once per room start at debug level and listed as `not_in_profile` in the diagnostics.

## Limitations

- **Hold by off and restore.** Window contacts and the cut-off switch the thermostats off and back on; they do not use a timer on the thermostat. If Home Assistant is down during a hold they stay off.
- **No schedules.** Easy TRV never writes a thermostat's `auto` mode; a thermostat someone puts into it is left alone, except that a window or the outdoor cut-off switches it off, and it then stays off until you switch it back (Repairs issue `schedule_thermostat_off`).
- **Writes while a room is off.** No temperature is written while the room is off or held. Whether a write could wake an `off` thermostat is one of the open checks in [`docs/testing-on-device.md`](docs/testing-on-device.md).
- **ZHA and the Sonoff TRVZB only.** Other models need their own profile.
- **ZHA's internal API.** Writing and verifying attributes needs ZHA's internal API. Easy TRV keeps all of it in one adapter module, so a future Home Assistant change is fixed in one place. If the API breaks, the `zha_api_incompatible` issue says so.
- **Not used yet:** humidity sensors, presence, presets, battery warnings.

## Removal

Delete the Easy TRV entry (**Settings > Devices & services > Easy TRV > three dots > Delete**), then remove the repository in HACS if you like. Easy TRV does not reset the thermostats. They keep their last settings; because nothing refreshes the external value any more, a TRVZB falls back to its internal sensor on its own after about two hours.

## Development

Python 3.14.2 or newer and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync                      # install the dependencies, including Home Assistant
uv run pytest                # tests with a coverage report
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # strict typing of core/, profiles/ and adapters/
```

CI runs the same checks plus hassfest and the HACS validation. Testing on real thermostats is covered by [`docs/testing-on-device.md`](docs/testing-on-device.md); the technical design is in [`docs/specs/2026-10-12-easy-trv-design.md`](docs/specs/2026-10-12-easy-trv-design.md).

## License

[MIT](LICENSE).
