# Testing on real thermostats

A manual checklist for the first run on real hardware: the owner's four Sonoff TRVZB, three in the living room ("Wohnzimmer") and one in the bedroom ("Schlafzimmer"). It also holds the spikes S1 to S4 from [`profiles/sonoff-trvzb-native.md`](profiles/sonoff-trvzb-native.md) plus S5, S8, S9 and S10 from the Phase 2 design (`specs/2026-10-14-easy-trv-phase2-design.md`, section 10), which the automated tests cannot answer. Section 7 checks what 0.5 adds: the heating state sensor, silent window contacts, the area of the room device and hiding the thermostat entities. Work through it top to bottom and write down what you see; the table at the end collects the results.

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
2. **Add room > From area**, choose "Schlafzimmer". One thermostat. Before you submit the first page, add one of the living-room thermostats to the thermostats field. Expected: the form says it already belongs to another room. Remove it again and submit. On **Room settings** leave every field on **Global** (so adaptive mode is on). This room checks the adaptive mode and is the one used for S10.
3. Expected: two devices, "Heizung Wohnzimmer" and "Heizung Schlafzimmer" (English: "Wohnzimmer heating", "Schlafzimmer heating"), each placed in its area and with two sensors: the diagnostic "Heating status" ("Heizungsstatus"), whose entity id is `sensor.wohnzimmer_heating_status` or `sensor.schlafzimmer_heating_status` (called the Status sensor below), and the visible "Heating state" ("Heizungszustand"), `sensor.wohnzimmer_heating_state` or `sensor.schlafzimmer_heating_state` (section 7).
4. Open **Configure** on the Easy TRV page. Expected: two sections, "Writes" and "Thermostat settings"; number fields can be left empty, and the description of each says what an empty field means (for the refresh interval: "How often the current room temperature is written to the thermostats again, even if it has not changed. 1 to 120 minutes. Empty = 20 min.").

## 3. The first writes

After Home Assistant is fully started (or after **Reload** on the Easy TRV page), each room syncs its thermostats one operation at a time. The room queues, in this order: the settings of each thermostat (window detection, then adaptive mode, then hysteresis, only while the adaptive mode is not on, then frost protection temperature), then the source of each thermostat, then the room temperature of each thermostat. The attributes written, all on cluster `0xFC11`, endpoint 1:

| Step | Attribute | Raw value |
|---|---|---|
| window detection (unless "leave") | `0x6000` | `1` (on) or `0` (off) |
| adaptive mode (firmware 1.4.4 or newer, unless "leave") | `0x6017` | `2` (on) or `0` (off) |
| hysteresis (firmware 1.3.0 or newer; only while adaptive mode is not on, or when the thermostat cannot do adaptive mode) | `0x6011` | minus hundredths of a kelvin, `-40` for 0.4 K |
| frost protection temperature | `0x6002` | hundredths of a degree Celsius, `500` for 5 °C |
| source: external | `0x600E` | `1`; reads back `1` or `3` |
| room temperature | `0x600D` | the room temperature rounded to 0.1 °C, times 100: `2130` for 21.3 °C, `2140` for 21.35 °C |

Every write is followed by a read about 10 seconds later, so the living room needs a few minutes to finish. The bedroom runs in parallel. In the bedroom (adaptive mode on, firmware 1.4.4) there is no `hysteresis attr 0x6011` line; in the living room (adaptive mode off) there is one.

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
| `frost_protection_temperature` | `0x6002` (24578) | `500` for 5 °C (hundredths of a degree) |
| `temperature_control_accuracy` | `0x6011` (24593) | `-40` for 0.4 K (firmware 1.3.0 or newer). In a room with adaptive mode on it keeps whatever the thermostat had; Easy TRV does not write it |

### Hardware check: adaptive mode and hysteresis

The two rooms from section 2 cover both sides of the hysteresis rule. Needs firmware 1.4.4 on the thermostats under test.

1. **Adaptive mode on (Schlafzimmer).** Read `0x6017` of the bedroom thermostat: expected `2`. In the debug log there is an `adaptive_mode attr 0x6017 raw=2 -> ok` line and no `hysteresis attr 0x6011` line for that room. Note the value `0x6011` holds anyway (the thermostat's own; Easy TRV did not write it).
2. **Adaptive mode off (Wohnzimmer).** Read `0x6017` of a living-room thermostat: expected `0` (a `1` also counts as off). `0x6011` reads `-40`, and the log has `adaptive_mode attr 0x6017 raw=0 -> ok` followed by `hysteresis attr 0x6011 raw=-40 -> ok`.
3. **Switch the bedroom to adaptive off.** Edit the room, set Adaptive mode to "Adaptive off", save. Expected after the reload: `0x6017` reads `0` or `1`, and `0x6011` reads `-40` (written now). Switch it back to **Global** afterwards (for S10).
4. **Firmware below 1.4.4** (only if one thermostat still runs 1.3.0): Repairs shows `trv_firmware` naming "Adaptive mode" and the thermostat, and the hysteresis is written to that thermostat anyway, even with adaptive mode on.
5. **Frost protection.** `0x6002` reads `500`. Change the global frost protection temperature to 9 °C in the options: after the reload `0x6002` reads `900` on every room that has no value of its own. Set it back.

### Check

- The Heating status sensor ("Status") of both rooms ends as `ok`. Open its attributes: `source` is `external`, `active_sensor` is your first temperature sensor, `trv_health` lists every thermostat as `ok`, `last_error` is empty.
- `climate.<thermostat>` shows the room temperature as `current_temperature` after a moment, not the radiator's own reading.
- Change the active sensor's value by 0.1 K or more (warm the sensor with your hand). About 5 seconds after the last report, the debug log shows one new `external_temperature attr 0x600D raw=... -> ok` line per thermostat, and `0x600D` in ZHA holds that raw value.
- Reload the integration, then check that nothing stays in Repairs (apart from `trv_firmware` for adaptive mode on a thermostat below 1.4.4).
- Switch Home Assistant to German and check that the states of the Heating status sensor (OK / Eingeschränkt / Fehler) and the room form are translated.

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

## 6. Room thermostat, knob and holds

Needs sections 2 and 3 done: Wohnzimmer (three thermostats) and Schlafzimmer run, Status `ok`. Do not hide the ZHA climate entities of the single thermostats, by hand or with the hide option (section 7.4), before this section; the checks below also read them. Raw values: setpoint `0x0012` and system mode `0x001C` on cluster `0x0201` (endpoint 1), read in ZHA like in section 3 (setpoint in hundredths of a degree; system mode `0` off, `4` heat, `1` auto).

### 6.1 The climate entity

1. Expected: `climate.wohnzimmer_heating` and `climate.schlafzimmer_heating` exist on the room devices, modes off, heat and frost protection (HA mode `auto`, shown as Frost protection), no presets and no `select.<room>_heating_mode`. Attributes: `setpoint_source`, `in_sync`, `thermostats`, `window_open`, `outdoor_cutoff`, `note`; no `hvac_action`.
2. Set the target to 21.5 °C. Expected: after the verify delay all three living-room thermostats read `0x0012 = 2150`, `in_sync` is true again, `setpoint_source` is `ha`, the debug log shows one setpoint write per thermostat and `-> ok`.
3. Try 3 °C and 36 °C in the more-info dialog or `climate.set_temperature`. Expected: the slider stops at 5 and 30 (the room's range); Home Assistant rejects a service call outside the range.
4. Mode **Frost protection** (`auto`). Expected: state `auto`, target 5 °C (the frost temperature), `setpoint_source` `frost`. Mode **heat**: back to 21.5 °C. Set a new target (19 °C) while on frost protection: expected the mode switches to `heat` at 19 °C. Repeat with frost on, then restart Home Assistant: the room comes back as `auto` and **heat** still returns to the target from before.
5. Mode **off**. Expected: all three read `0x001C = 0`, no `0x600D` line appears in the debug log while off (for S1). Mode **heat**: `0x001C = 4`, source, room temperature and target (`0x0012 = 2150`) follow.
6. Restart Home Assistant with the room at 22 °C: expected the room returns at 22 °C and heat, and no setpoint or mode write is sent unless a thermostat reported something else.
7. Put one thermostat into its schedule (`auto`, `0x001C = 1`, in ZHA or on the device). Expected after its report: `note` is `schedule`, the Heating status sensor lists it under `schedule_thermostats`, `in_sync` false, and a target change in HA does not touch that thermostat (the others get it). Set it back to heat: it is brought up to the room's target.

### 6.2 Knob adoption across three thermostats

1. Turn the knob of living-room thermostat A from 21.5 to 22.5 in 0.5 K steps. Expected: about 3 seconds after the last step the room target is 22.5 (`setpoint_source` `device:climate.<A>`), B and C read `0x0012 = 2250`, the event `easy_trv_setpoint_adopted` fires. Note how many steps arrived as reports (see S8).
2. Turn B and then, within a few seconds, C to different values. Expected: the last turn wins and all three end on that value.
3. Turn a knob below the minimum or above the maximum. Expected: the value is clamped and written back to that thermostat too.
4. Mode **off** in HA, then turn a knob on A. Expected: the room goes to heat at the new target, all three follow. Then turn a knob and switch the room off in HA within 3 seconds: the room stays off, the turn is not adopted.
5. Switch a thermostat off at the device itself (long press or the display, if the model allows it): expected the room goes off (`setpoint_source` `device:...`) and the other two are switched off.

### 6.3 Window contact

Use a real contact, or a test contact (a helper `input_boolean.easy_trv_test_window` and in `configuration.yaml`):

```yaml
template:
  - binary_sensor:
      - name: "Easy TRV test window"
        device_class: window
        state: "{{ is_state('input_boolean.easy_trv_test_window', 'on') }}"
```

Add it to Wohnzimmer (edit the room, field **Window and door contacts**; try a contact from a different area). Warm sensor, target 22 °C.

1. Open the window. Expected: `window_open` true, the event `easy_trv_window_hold`, all three thermostats `0x001C = 0` within the verify delay, the climate entity keeps showing heat and 22 °C, no `0x600D` or `0x0012` line in the debug log meanwhile.
2. During the hold change the target in HA to 20 °C. Expected: nothing is written to the thermostats. Close the window: they read heat and `0x0012 = 2000`.
3. Before you open the window, set **Window contact check (minutes, 0 = off)** to 5 and **Open-window warning (minutes, 0 = never)** to 10 in the room's settings (reset afterwards; saving reloads the room and would restart the hold timer). Then open it and let the check run. Expected: at 5 minutes with no drop of 0.3 K, Repairs shows `window_contact_suspect`; at 10 minutes `window_open_long`; both disappear on closing. If the room really cools by 0.3 K, no suspect issue appears; note which case happened.
4. Open the window and restart Home Assistant. Expected: the room is back in the hold, the thermostats stay off and no heat or target is written while the contact is open. Close it: the room resumes.
5. Make the contact unavailable: disable the template binary sensor itself (disabling the `input_boolean` helper only makes it report off, which is closed), or give the template an `availability` template that you can switch off. Expected: counts as closed, `in_sync` false, a debug line says the contact is counted as closed. What 0.5 adds to this (the state `restricted`, the Repairs issue after 10 minutes) is checked in section 7.2.
6. Optional (the trade-off): open the window, stop Home Assistant for a few minutes, start it. Note what the thermostats showed meanwhile (off with frost protection) and that the room returns correctly.

### 6.4 Outdoor cut-off via a template sensor

Create the helper `input_number.easy_trv_test_outdoor` (-10 to 30, step 0.5) and:

```yaml
template:
  - sensor:
      - name: "Easy TRV test outdoor"
        device_class: temperature
        unit_of_measurement: "°C"
        state: "{{ states('input_number.easy_trv_test_outdoor') | float(0) }}"
```

In the Easy TRV options choose it as **Outdoor temperature sensor** and set the cut-off to 15 °C (Wohnzimmer follows Global; in Schlafzimmer's room settings set **Outdoor cut-off** to Off).

1. Set the helper to 14.5: nothing happens. Then 15: Wohnzimmer goes off (`outdoor_cutoff` true, the event `easy_trv_outdoor_hold`, `0x001C = 0`); Schlafzimmer stays on.
2. Back to 14.5: still off (deadband). 14.0 (the helper steps by 0.5): the room is back on with its target; the hold ends below 14.5.
3. During the cut-off turn a knob: expected only the target changes (`setpoint_source` `device:...`), the room stays off until the cut-off ends.
4. Open the window during the cut-off and end the cut-off: the room stays off until the window is closed, and vice versa.
5. Cut-off on, then make the template sensor unavailable (disable its entity): the room goes back on and the log has a warning.

## 7. Heating state, silent contacts, areas and hiding (0.5)

Needs sections 2, 3 and 6.3 done: Wohnzimmer (with the test window contact from 6.3) and Schlafzimmer run, the Heating status is `ok`. Keep the Easy TRV debug log on. In Developer tools > Events listen to `easy_trv_state_changed`.

### 7.1 The heating state sensor

1. Expected: both rooms have the sensor "Heating state" ("Heizungszustand"), `sensor.wohnzimmer_heating_state` and `sensor.schlafzimmer_heating_state`, on the room device and not in its diagnostic group. With the room on heat the state is `heating`; the attributes are `reasons: [heating]`, `since` (a timestamp), `window_off_at` empty, `thermostats` empty and `silent_contacts` empty. The status sensor is now called "Heating status" ("Heizungsstatus"); its entity id has not changed.
2. Set Wohnzimmer to frost protection, then off, then heat. Expected: `frost` (reasons `[frost]`), `off_manual` (reasons `[off_manual]`), `heating`. Each change moves `since` and fires `easy_trv_state_changed` with `room`, `state`, `previous` and `reasons`.
3. **Window: open, delay, off.** In the room settings of Wohnzimmer set **Delay before a window counts as open** to 60 seconds (saving reloads the room; reset it afterwards). Open the test window.
   - Expected at once: `window_pending` (reasons `[window_pending, heating]`), `window_off_at` about 60 seconds ahead. The thermostats are still on heat.
   - Close the window within the 60 seconds: back to `heating` at once, `window_off_at` empty, nothing written to the thermostats.
   - Open it again and wait: after 60 seconds `off_window` (reasons `[off_window, heating]`), `window_off_at` empty, all three thermostats `0x001C = 0`. Close it: `heating`, the thermostats follow.
   - Switch the room off in HA (`off_manual`), then open and close the window. Expected: the state stays `off_manual` all the time and the room is still off after the window is closed.
4. **Outdoor cut-off** (needs 6.4): outdoor helper at 15 °C or above: `off_outdoor` (reasons `[off_outdoor, heating]`); below 14.5: `heating`.
5. **Deviating thermostat.** Put one living-room thermostat on its schedule (`auto`, as in 6.1 step 7). Expected: `restricted`, `thermostats` holds `climate.<A>: schedule`. Back to heat: `heating`, `thermostats` empty.
6. **Sensor lost.** Make the temperature sensor of a room with a single sensor unavailable (section 5, "Sensor lost"). Expected: `emergency` while the Heating status is `degraded`; back to `heating` when the sensor returns.
7. **Restart** Home Assistant. Expected: the state is unknown until the rooms have started, then the right state; `since` is the time of the first evaluation after the start and no `easy_trv_state_changed` event fires for it.
8. Switch Home Assistant to German. Expected: the sensor is "Heizungszustand" and its states read, for example, Heizbetrieb, Frostschutz, Aus (manuell), Fenster offen, schaltet gleich ab, Aus: Fenster/Tür offen, Eingeschränkt and Notbetrieb; the attribute names are translated.

### 7.2 A silent window contact

Uses the test contact from 6.3 in Wohnzimmer, window closed, room on heat.

1. Make the contact unavailable (disable the template binary sensor, as in 6.3 step 5). Expected at once: the state `restricted` (reasons `[restricted, heating]`), `silent_contacts` lists the contact, `in_sync` false. No Repairs issue yet, and the heating keeps running: nothing is switched off because of the silent contact.
2. Wait. Expected after about 10 minutes (note the time): Repairs shows "Window contact in Wohnzimmer is not reachable" (German "Fensterkontakt im Raum Wohnzimmer nicht erreichbar"), a warning that names the contact.
3. If you have a second contact in the room, make it silent too. Expected: the issue now names both contacts.
4. Bring the contacts back. Expected: `restricted` disappears at once and so does the issue. If you bring the contact back before the 10 minutes are over, the issue never appears; make it silent again and check that the 10 minutes start over.
5. Open the window and wait until the room is off, then make the contact silent. Expected: the hold stays, because the contact keeps its last state (`off_window`), and `reasons` lists `restricted` after it. Bring the contact back with the window closed: the room heats again.
6. Restart Home Assistant while the contact is silent. Expected: `restricted` after the start, the issue about 10 minutes after the start, not before.

### 7.3 The area of the room device

1. Expected after the update from 0.4.x (or on a new setup from an area): the devices "Heizung Wohnzimmer" and "Heizung Schlafzimmer" are in their areas, and their entities (state, status, climate) show that area in the entity list. A device without an area got its area at the first start.
2. **Add room > Custom room.** Expected: the form has the optional field **Area** next to the name. (A custom room needs a thermostat that belongs to no room; cancel the flow if all four are in use.)
3. **Edit room** Schlafzimmer. Expected: the field **Area** shows the area the room has. Choose another area (call it A), submit this page and the room settings page. Expected: the device moved to A, and its entities with it.
4. On the device page set the area of the device by hand to a third area (B), then reload Easy TRV. Expected: B stays; the device has an area, so nothing is changed.
5. Edit the room, empty the field **Area** and submit both pages. Expected: the device stays in B.
6. Edit the room and choose A again. Expected: the device moves to A. If your Home Assistant allows clearing the area on the device page, clear it and reload Easy TRV: the device is in the room's area (A) again.

### 7.4 Hiding the thermostat entities

Open the device page of one living-room thermostat and note its entities. Before you start, hide the frost protection temperature number of that thermostat by hand (entity settings > Visible off).

1. Expected by default: the global setting **Hide thermostat entities** (Configure > Thermostat settings) reads "Shown" ("Eingeblendet") and nothing but your hand-hidden entity is hidden.
2. Set it to "Hidden" ("Ausgeblendet") and save (this reloads the integration). Expected, for every thermostat of both rooms: the climate entity and the entities external temperature (`external_temperature_sensor_value`), external temperature source (`external_temperature_sensor`), open-window detection (`open_window`), hysteresis (`temperature_control_accuracy`), adaptive mode (`adaptive_mode`) and frost protection temperature (`frost_protection_temperature`) are hidden. In the entity list (Settings > Devices & services > Entities, filter for hidden entities) all but your own are marked as hidden by the integration. Child lock, the boost and timer buttons, valve opening, battery and the sensors are not hidden. Settings left "as set in the thermostat" stay visible too (not the case here while open-window detection and adaptive mode are on).
3. **They keep working.** Reload Easy TRV and read the debug log: the writes of section 3 verify with `-> ok` as before, the Heating status stays `ok`, and `climate.<thermostat>` is still readable in Developer tools > States. Change the room target: the thermostats follow.
4. Show the hysteresis number again by hand (entity settings > Visible on) and reload Easy TRV. Expected: it stays visible (your choice stays). The frost protection number that you hid yourself stays hidden and was not touched.
5. **Room override.** In the room settings of Wohnzimmer set **Hide thermostat entities** to "Shown" and save. Expected: the entities Easy TRV hid in Wohnzimmer are shown again (not your hand-hidden one); Schlafzimmer stays hidden. Set the room back to "Global".
6. **A thermostat leaves.** Edit Wohnzimmer and remove one thermostat from the room. Expected: its entities are shown again; add it back and they are hidden again.
7. Set the global setting back to "Shown". Expected: everything Easy TRV hid is shown again; the frost protection number you hid yourself stays hidden. Show it again by hand to finish.
8. Optional, at the very end (you have to add the rooms again): turn the setting on, then delete the Easy TRV entry. Expected: the entities Easy TRV hid are shown again.

## 8. Spikes

Each spike answers one question that decides whether a requirement holds. Keep the Easy TRV debug log on (section 1) and note timestamps; the `attr 0x...` lines show every write and its result.

### S1: Does a value write wake a thermostat that is off?

Why: requirement T6 is amended: no temperature writes while the room is off or held by a window or the outdoor cut-off. The answer tells whether that rule is needed (a write wakes the thermostat) or only a simplification.

Easy TRV cannot run this test itself any more: an `off` set through the ZHA entity is adopted by the room, which switches the others off and stops writing. So test with the entry disabled, as S4 does.

1. Disable the Easy TRV entry (Easy TRV page > three dots > Disable) so nothing writes or adopts.
2. Set one living-room thermostat to `off` (more-info dialog of its ZHA entity). Note `hvac_mode` and `hvac_action` and read the system mode in ZHA (cluster `0x0201`, attribute `0x001C`; expect `0`).
3. In ZHA > Manage Zigbee device > Clusters write `0x600E = 1` (cluster `0xFC11`, endpoint 1), wait 5 minutes, then write `0x600D = 2140` by hand. After each write wait at least 5 minutes (the thermostat polls infrequently) and watch `hvac_mode` and `hvac_action`, the display on the device and `0x001C`.
4. Record for each write: stayed `off` or switched on, and after how long. Set the thermostat back to its normal mode and enable the entry again.

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

### S5: Does the thermostat report `0x0012 = 1200` during its open-window phase?

Why: the room treats a thermostat that shows exactly 12 °C as being in its own window phase and leaves it alone for up to 30 minutes. If the device never reports that value, the branch is dead code and will be removed.

1. Use S3 (window detection on, external source active, a clearly colder outside). Make the room target something other than 12 °C (for example 21 °C).
2. Open the window next to a thermostat until it reacts (valve closes, display shows the window symbol). Read the setpoint `0x0012` in ZHA and watch the debug log and the climate entity's `note`.
3. Record: does `0x0012` read 1200 (and is it reported without you reading it)? Does `note` become `window_phase`? Does the room write the target meanwhile (it should not)? When the window is closed, how does the setpoint come back (the thermostat's own, or Easy TRV's write after the phase ended or after 30 minutes)?

### S8: Does every knob step arrive as a report?

Why: the room waits 3 seconds after the last report of a knob turn before it adopts the value. That assumes every step is reported; with ZHA's minimum reporting interval of 30 seconds only some might be.

1. Debug logging for Easy TRV on. Turn the knob of a thermostat by 0.5 K per step, 4 steps, one step per second; wait 10 seconds. Repeat with 4 steps, 5 seconds apart.
2. Record per run: the setpoint reports that reached the diagnostics (Download diagnostics > room > observations: kind, raw, time), how many arrived and how long after the last turn the room target changed. If steps arrive late or are missing, note the delay between knob and report.

### S9: Does a setpoint write while `system_mode = auto` switch the thermostat to heat?

Why: the room never writes into a thermostat on its schedule, so this only matters for what happens if a person writes to it from elsewhere. It checks the assumption behind the exclusion rule.

1. Put one thermostat into `auto` (`0x001C = 1`). Confirm the room shows `note: schedule` and does not write to it.
2. Write a setpoint to it by hand from ZHA (cluster `0x0201`, attribute `0x0012`, for example 2000) or with the ZHA climate entity.
3. Record: does `0x001C` stay `1` or change to `4`? Does the thermostat adopt the setpoint? What does the Easy TRV room do (note, adoption, writes)? Put the thermostat back to heat.

### S10: Adaptive mode with the external sensor

Why: with the adaptive mode on, the thermostat ignores the hysteresis and adapts to the room itself. Whether that works well with a value fed from an external sensor is not known.

1. Use the bedroom (adaptive mode on, external source active, Status `ok`, firmware 1.4.4). Note the target temperature of the thermostat.
2. Over one evening (at least 4 hours of heating), log the external sensor's value, for example with the history graph of the sensor, plus `climate.<thermostat>` `current_temperature`.
3. Does the room stay within ±0.5 K of the target? Note the largest deviation and when it happened.
4. Note the valve behaviour (audible motor, display, `hvac_action` if it is correct at all) and any swing: does the temperature oscillate, and with what period and amplitude?
5. If it swings or misses ±0.5 K, repeat with "Adaptive off" and a hysteresis of 0.4 K for comparison (hardware check above, step 3) and note the difference.

## 9. Where to look

| What | Where |
|---|---|
| Room status and per-thermostat health | Easy TRV page > room device > Heating status sensor and its attributes |
| What the room is doing and why | Easy TRV page > room device > Heating state sensor and its attributes (`reasons`, `since`, `window_off_at`, `thermostats`, `silent_contacts`) |
| Issues | **Settings > System > Repairs** (`trv_unreachable`, `trv_firmware`, `settings_migrated`, `window_open_long`, `window_contact_suspect`, `window_contact_unavailable`, `schedule_thermostat_off`, `zha_api_incompatible`, `profile_invalid`, `room_invalid`) |
| Events | **Developer tools > Events** > listen to `easy_trv_write_failed`, `easy_trv_recovered`, `easy_trv_failsafe_detected`, `easy_trv_setpoint_adopted`, `easy_trv_window_hold`, `easy_trv_outdoor_hold` or `easy_trv_state_changed` (one event name at a time) |
| Log | **Settings > System > Logs**, or the downloaded debug log; search for `easy_trv` |
| Full state | Easy TRV page > three dots > **Download diagnostics** (including holds and the latest observations per thermostat) |
| Raw device values | ZHA device page > Manage Zigbee device > Clusters (section 3) |

## 10. Reporting results

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
| Heating status sensor and attributes | | |
| German translation | | |
| F1: time until `0x600E = 2`; recovery on enable | | |
| F2: event seen at refresh 90 minutes (yes / no) | | |
| Sensor lost / regained | | |
| Thermostat gone / back, Repairs, events | | |
| Invalid profile | | |
| Room thermostat: target, frost preset, off / heat, restart | | |
| Knob adoption across three thermostats, knob while off | | |
| Schedule (`auto`) thermostat left alone | | |
| Window contact: off, restore, long-open and suspect issues, restart | | |
| Outdoor cut-off with template sensor, deadband | | |
| Heating state: heating / frost / off, window pending and off, event on change, restart | | |
| Silent window contact: `restricted` at once, Repairs issue after 10 minutes, clears on return | | |
| Area of the room device: set at start, moved on change, empty field, hand-set area kept | | |
| Hiding: six entities and the climate entity hidden, still working, hand choices kept, shown again on off / room override / thermostat removed | | |
| S1: off thermostat after reload / after value write | | |
| S2: unsolicited report of `2` (yes / no, when) | | |
| S3: window detection with external source | | |
| S4: exact read-back of 2135 and 2140, endpoint | | |
| S5: `0x0012 = 1200` during the thermostat's window phase (yes / no) | | |
| S8: knob steps arriving as reports (steps per run, delay) | | |
| S9: setpoint write in `auto` switches to heat (yes / no) | | |
| S10: adaptive mode with the external sensor (±0.5 K over an evening, valve behaviour, swing) | | |
