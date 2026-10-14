# Testing on real thermostats

A manual checklist for the first run on real hardware: the owner's four Sonoff TRVZB, three in the living room ("Wohnzimmer") and one in the bedroom ("Schlafzimmer"). It also holds the five spikes (S1 to S5) from [`profiles/sonoff-trvzb-native.md`](profiles/sonoff-trvzb-native.md) that the automated tests cannot answer. Work through it top to bottom and write down what you see; the table at the end collects the results.

## What you need

- Home Assistant 2026.8 or newer with ZHA, and the four thermostats paired. Note the firmware of each one (device page > Device info > Firmware).
- At least one temperature sensor per room, ideally two in the living room to see the priority order.
- Admin access to Home Assistant, the ZHA device page, Settings > System > Logs and Developer tools.
- A clock. Several checks wait for 2 hours.

## 1. Install and add the integration

1. HACS > three dots > **Custom repositories** > add `https://github.com/ImSorry99/easy-trv`, type **Integration**.
2. Download **Easy TRV**, restart Home Assistant.
3. **Settings > Devices & services > Add integration > Easy TRV.** The entry is created without a form.
4. Turn on debug logging for the test: Easy TRV page > three dots > **Enable debug logging**. Turn it off and download the log when you are done. Alternatively set it in `configuration.yaml` and restart:

   ```yaml
   logger:
     logs:
       custom_components.easy_trv: debug
   ```

   Every operation Easy TRV carries out produces one debug line once its outcome is known:

   ```
   Wohnzimmer: climate.living_trv_1 external_temperature attr 0x600D raw=2140 -> ok
   ```

   That is: room, thermostat entity, capability, attribute, the raw value written, and the result (`ok`, `mismatch expected [2140] got 2000`, `failed: <error>`, `signal failsafe`). The capability names in these lines are `external_temperature`, `source_select`, `window_detection_flag`, `adaptive_mode`, `hysteresis` and `frost_protection`. An operation that is not sent at all is logged as `... adaptive_mode not sent -> unsupported ...` when the firmware is too old, and as `... not sent -> failed: ...` when the value is out of range. Filter the log for `easy_trv` or `attr 0x`.
5. Expected: the integration appears with no rooms and no Repairs issue.

## 2. Add the rooms

1. **Add room > From area**, choose "Wohnzimmer". Expected: the next page is prefilled with the three thermostats and the area's temperature and humidity sensors (the thermostats' own readings are not among them). Order the temperature sensors by priority and submit. On the next page, **Room settings**, set **Adaptive mode** to "Adaptive off" and leave everything else on **Global** (open-window detection on, hysteresis 0.4 K, frost protection 7 °C, profile auto). This room checks the hysteresis write (section 3).
2. **Add room > From area**, choose "Schlafzimmer". One thermostat. Before you submit the first page, add one of the living-room thermostats to the thermostats field. Expected: the form says it already belongs to another room. Remove it again and submit. On **Room settings** leave every field on **Global** (so adaptive mode is on). This room checks the adaptive mode and is the one used for S5.
3. Expected: two devices, "Heizung Wohnzimmer" and "Heizung Schlafzimmer" (English: "Wohnzimmer heating", "Schlafzimmer heating"), each placed in its area and with one diagnostic sensor "Status" whose entity id is `sensor.wohnzimmer_heating_status` or `sensor.schlafzimmer_heating_status`.
4. Open **Configure** on the Easy TRV page. Expected: two sections, "Writes" and "Thermostat settings"; number fields can be left empty ("Empty = built-in default" in the description).

## 3. The first writes

After Home Assistant is fully started (or after **Reload** on the Easy TRV page), each room syncs its thermostats one operation at a time. The room queues, in this order: the settings of each thermostat (window detection, then adaptive mode, then hysteresis, only while the adaptive mode is not on, then frost protection temperature), then the source of each thermostat, then the room temperature of each thermostat. The attributes written, all on cluster `0xFC11`, endpoint 1:

| Step | Attribute | Raw value |
|---|---|---|
| window detection (unless "leave") | `0x6000` | `1` (on) or `0` (off) |
| adaptive mode (firmware 1.4.4 or newer, unless "leave") | `0x6017` | `2` (on) or `0` (off) |
| hysteresis (firmware 1.3.0 or newer; only while adaptive mode is not on, or when the thermostat cannot do adaptive mode) | `0x6011` | minus hundredths of a kelvin, `-40` for 0.4 K |
| frost protection temperature | `0x6002` | hundredths of a degree Celsius, `700` for 7 °C |
| source: external | `0x600E` | `1`; reads back `1` or `3` |
| room temperature | `0x600D` | the room temperature rounded to 0.1 °C, times 100: `2130` for 21.3 °C, `2140` for 21.35 °C |

Every write is followed by a read about 10 seconds later, so the living room needs a few minutes to finish. The bedroom runs in parallel. In the bedroom (adaptive mode on) there is no `hysteresis attr 0x6011` line; in the living room (adaptive mode off) there is one.

### Read the raw values in ZHA

For each thermostat: device page > three dots > **Manage Zigbee device** > **Clusters**.

1. Pick endpoint 1 and the cluster with ID `0xFC11` (64529). Leave the manufacturer code empty.
2. Choose an attribute and press **Get Zigbee Attribute**:

| Attribute | ID | Expect |
|---|---|---|
| `external_temperature_sensor_value` | `0x600D` (24589) | the active sensor's value rounded to 0.1 °C, times 100 (`2140` for 21.35 °C) |
| `external_temperature_sensor_enable` | `0x600E` (24590) | `1` or `3` (external); `0` internal; `2` fail-safe |
| `open_window` | `0x6000` (24576) | `1` if window detection is on |
| `smart_temperature_control` | `0x6017` (24599) | `2` if adaptive mode is on, `0` if off. The device also reports `1` as an alias of off, so a `1` after switching it off is fine (firmware 1.4.4 or newer) |
| `frost_protection_temperature` | `0x6002` (24578) | `700` for 7 °C (hundredths of a degree) |
| `temperature_control_accuracy` | `0x6011` (24593) | `-40` for 0.4 K (firmware 1.3.0 or newer). In a room with adaptive mode on it keeps whatever the thermostat had; Easy TRV does not write it |

### Hardware check: adaptive mode and hysteresis

The two rooms from section 2 cover both sides of the hysteresis rule. Needs firmware 1.4.4 on the thermostats under test.

1. **Adaptive mode on (Schlafzimmer).** Read `0x6017` of the bedroom thermostat: expected `2`. In the debug log there is an `adaptive_mode attr 0x6017 raw=2 -> ok` line and no `hysteresis attr 0x6011` line for that room. Note the value `0x6011` holds anyway (the thermostat's own; Easy TRV did not write it).
2. **Adaptive mode off (Wohnzimmer).** Read `0x6017` of a living-room thermostat: expected `0` (a `1` also counts as off). `0x6011` reads `-40`, and the log has `adaptive_mode attr 0x6017 raw=0 -> ok` followed by `hysteresis attr 0x6011 raw=-40 -> ok`.
3. **Switch the bedroom to adaptive off.** Edit the room, set Adaptive mode to "Adaptive off", save. Expected after the reload: `0x6017` reads `0` or `1`, and `0x6011` reads `-40` (written now). Switch it back to **Global** afterwards (for S5).
4. **Firmware below 1.4.4** (only if one thermostat still runs 1.3.0): Repairs shows `trv_firmware` naming "Adaptive mode" and the thermostat, and the hysteresis is written to that thermostat anyway, even with adaptive mode on.
5. **Frost protection.** `0x6002` reads `700`. Change the global frost protection temperature to 9 °C in the options: after the reload `0x6002` reads `900` on every room that has no value of its own. Set it back.

### Check

- The Status sensor of both rooms ends as `ok`. Open its attributes: `source` is `external`, `active_sensor` is your first temperature sensor, `trv_health` lists every thermostat as `ok`, `last_error` is empty.
- `climate.<thermostat>` shows the room temperature as `current_temperature` after a moment, not the radiator's own reading.
- Change the active sensor's value by 0.1 K or more (warm the sensor with your hand). About 5 seconds after the last report, the debug log shows one new `external_temperature attr 0x600D raw=... -> ok` line per thermostat, and `0x600D` in ZHA holds that raw value.
- Reload the integration, then check that nothing stays in Repairs (apart from `trv_firmware` for adaptive mode on a thermostat below 1.4.4).
- Switch Home Assistant to German and check that the Status states (OK / Eingeschränkt / Fehler) and the room form are translated.

## 4. Fail-safe

The TRVZB discards the external value after about two hours without a write and falls back to its internal sensor (`0x600E = 2`). Easy TRV writes at least every refresh interval (default 20 minutes), so in normal operation this never happens. Two checks look at it from both sides.

### F1: How long does the device hold the value, and does Easy TRV recover?

1. Pick the bedroom thermostat. Download the diagnostics and note `last_value_write` of the room.
2. **Disable** the Easy TRV entry (Easy TRV page > three dots > Disable). Nothing writes to the thermostats now. Disabling does not reset them.
3. From 60 minutes on, read `0x600E` and `0x600D` of that thermostat every 30 minutes without writing anything. (S2 below needs a run without these reads.) Note the time when `0x600E` first shows `2`, and whether `climate.current_temperature` changes to the radiator's own reading at that moment.
4. After more than 2 hours, `0x600E` should read `2`. Note it.
5. **Enable** the entry again. Expected: the room starts, `0x600E` reads `1` or `3` after the first writes, `0x600D` holds the current room temperature, and the Status is `ok`.

### F2: Does Easy TRV notice a fallback while it runs?

Easy TRV can only see the fallback if it happens between two writes. Set **Refresh interval** to 90 minutes in the options and leave the integration running for at least 6 hours with a steady sensor value.

- If the device really holds the value for about 2 hours, every refresh keeps it alive and nothing happens. That is the expected result; note it.
- If the device falls back earlier than 90 minutes, the next refresh reads `0x600E = 2`. Expected then: the event `easy_trv_failsafe_detected` fires, `0x600E` returns to `1` or `3`, and the Status is `degraded` for one refresh interval, then `ok` again.

Set the refresh interval back to 20 afterwards (empty field = built-in default).

## 5. Robustness (optional)

- **Sensor lost.** Make the active sensor unavailable (disable its entity or take its battery out). Expected within a few minutes: Status `degraded`, `source` `internal`, `0x600E` reads `0` on every thermostat of the room. With a second sensor in the room the thermostats stay on `external` and `active_sensor` switches to the second sensor. Restore the sensor: back to `external`, Status `ok`.
- **Thermostat gone.** Take the batteries out of one thermostat. Writes only happen when there is something to write, so trigger one: change the room sensor by 0.1 K or more (warm it with your hand), or wait for the next refresh (default 20 minutes; set the refresh interval to 5 for this check). Expected: while ZHA still shows the thermostat as available, the write fails (`failed: ...` or `mismatch ...` lines in the debug log) and after three attempts Repairs shows `trv_unreachable` (the message names the setting, for example "room temperature", and the thermostat by its device name, not its entity id), the event `easy_trv_write_failed` fires and the Status is `error`. Once ZHA marks it unavailable it is skipped and the Status is `degraded`. Put the batteries back: as soon as the room temperature, the write that failed, is verified, the issue clears and `easy_trv_recovered` fires.
- **Invalid profile.** Put a broken YAML file into `<config>/easy_trv/profiles/` and reload the integration. Expected: Repairs shows `profile_invalid` naming the file, and the rooms keep working. Remove the file and reload; the issue goes away.

## 6. Spikes

Each spike answers one question that decides whether a requirement holds. Keep the Easy TRV debug log on (section 1) and note timestamps; the `attr 0x...` lines show every write and its result.

### S1: Does a value write wake a thermostat that is off?

Why: requirement T6 keeps writing regardless of the thermostat's HVAC mode.

1. Set one living-room thermostat to `off` (more-info dialog or `climate.set_hvac_mode`). Note `hvac_mode` and `hvac_action` and read the system mode in ZHA (cluster `0x0201`, attribute `0x001C`).
2. Trigger writes: reload the integration (settings, source and value are written), then change the active sensor's value by 0.1 K or more (value only).
3. Wait at least 5 minutes after each (the thermostat polls infrequently). Watch `hvac_mode` and `hvac_action`, the display on the device, and the system mode in ZHA.
4. Record for each trigger: stayed `off` or switched on, and after how long. Set the thermostat back to its normal mode.

### S2: Does the thermostat report `0x600E = 2`, or only answer reads?

Why: if it reports, the fail-safe could be caught immediately rather than at the next refresh.

1. Enable debug logging for ZHA as well (ZHA page > three dots > Enable debug logging).
2. Run F1 from section 4 again as a separate run, without the half-hourly reads of F1 step 3: every manual read could itself make the device report. Read `0x600E` by hand only once, at the end of the wait (more than 2 hours).
3. After the wait, search the log for `0x600E` and `external_temperature_sensor_enable`. Record whether an attribute report with value `2` arrived on its own, and when.

### S3: Does the built-in window detection trigger on the external source?

Why: validates the Phase 1 decision to use the thermostat's own detection with a slowly reporting sensor.

1. Window detection `on` in the room, external source active, room Status `ok`. Pick a day when it is clearly colder outside.
2. Open the window next to a thermostat for at least 10 minutes. Note when the thermostat reacts (valve closes, the target changes to 12 °C, `0x6000` stays `1`). According to the profile notes the device's own rule is a drop of more than 1.5 K within 4.5 minutes.
3. Note the external sensor's reported values during that time and how often it reports. Also note whether Easy TRV writes a value meanwhile (`attr 0x600D` lines in the debug log) and whether the Status stays `ok`.
4. Close the window. Note when and how the thermostat returns to the old target (on a rise, on a manual change, or after 30 minutes).
5. Optional baseline: repeat with the entry disabled and the thermostat on its internal source (write `0` to `0x600E` in ZHA).

### S4: Endpoint and exact read-back of `0x600D`

Why: verification compares exact integers.

1. Disable the Easy TRV entry so nothing else writes.
2. In ZHA > Manage Zigbee device > Clusters: confirm that the thermostat lists endpoint 1 with cluster `0xFC11`.
3. Write `0x600E = 1`. Then write `0x600D = 2135` and read it back; then write `2140` and read it back; then `2135` again. Always read after about 10 seconds. These raw values are written by hand; Easy TRV itself rounds to 0.1 °C and only writes multiples of 10 (`2140` for 21.35 °C).
4. Record whether each read returns exactly the written value, and `climate.current_temperature` after each (21.4 for 2135 or 2140?).
5. Enable the entry again and confirm that its first value write verifies (Status `ok`, no `easy_trv_write_failed`).

### S5: Adaptive mode with the external sensor

Why: with the adaptive mode on, the thermostat ignores the hysteresis and adapts to the room itself. Whether that works well with a value fed from an external sensor is not known.

1. Use the bedroom (adaptive mode on, external source active, Status `ok`, firmware 1.4.4). Note the target temperature of the thermostat.
2. Over one evening (at least 4 hours of heating), log the external sensor's value, for example with the history graph of the sensor, plus `climate.<thermostat>` `current_temperature`.
3. Does the room stay within ±0.5 K of the target? Note the largest deviation and when it happened.
4. Note the valve behaviour (audible motor, display, `hvac_action` if it is correct at all) and any swing: does the temperature oscillate, and with what period and amplitude?
5. If it swings or misses ±0.5 K, repeat with "Adaptive off" and a hysteresis of 0.4 K for comparison (hardware check above, step 3) and note the difference.

## 7. Where to look

| What | Where |
|---|---|
| Room status and per-thermostat health | Easy TRV page > room device > Status sensor and its attributes |
| Issues | **Settings > System > Repairs** (`trv_unreachable`, `trv_firmware`, `settings_migrated`, `zha_api_incompatible`, `profile_invalid`, `room_invalid`) |
| Events | **Developer tools > Events** > listen to `easy_trv_write_failed`, `easy_trv_recovered` or `easy_trv_failsafe_detected` (one event name at a time) |
| Log | **Settings > System > Logs**, or the downloaded debug log; search for `easy_trv` |
| Full state | Easy TRV page > three dots > **Download diagnostics** |
| Raw device values | ZHA device page > Manage Zigbee device > Clusters (section 3) |

## 8. Reporting results

Open an issue at <https://github.com/ImSorry99/easy-trv/issues> or add the table below to your notes. Attach:

- the diagnostics file and the debug log of the test period,
- the thermostats' firmware versions,
- the filled-in table.

The diagnostics contain the thermostats' IEEE addresses and entity ids. Remove or mask them before posting publicly.

| Check | Result | Notes |
|---|---|---|
| Install, add entry, rooms | | |
| First writes, raw values (`0x6000`, `0x6017`, `0x6011`, `0x6002`, `0x600E`, `0x600D`) | | |
| Adaptive on: `0x6017` = 2, no `0x6011` write; adaptive off: `0x6011` = -40 | | |
| Status sensor and attributes | | |
| German translation | | |
| F1: time until `0x600E = 2`; recovery on enable | | |
| F2: event seen at refresh 90 minutes (yes / no) | | |
| Sensor lost / regained | | |
| Thermostat gone / back, Repairs, events | | |
| Invalid profile | | |
| S1: off thermostat after reload / after value write | | |
| S2: unsolicited report of `2` (yes / no, when) | | |
| S3: window detection with external source | | |
| S4: exact read-back of 2135 and 2140, endpoint | | |
| S5: adaptive mode with the external sensor (±0.5 K over an evening, valve behaviour, swing) | | |
