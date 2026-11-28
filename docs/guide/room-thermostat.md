# Room thermostat

[Easy TRV](../../README.md) › [Documentation](README.md)

## Room thermostat

Each room has one `climate.<room>_heating` (for example `climate.living_room_heating`) as soon as the room's profile can set the target temperature and the mode. The shipped profile can. It is a pass-through: it stores what a person asked for and writes it to every thermostat of the room, with the same verified writes as everything else. It does not regulate and it shows no `hvac_action`.

- **Modes:** **Off** (`off`), **Heat** (`heat`) and **Frost protection** (`auto`), see [Modes](#modes). Off switches every thermostat off (their own frost protection keeps the radiator safe) and stops the external-temperature writes; heat resyncs mode, source, temperature and target.
- **No presets.** Frost protection is a mode of the room thermostat, not a preset.
- **Range:** with the setpoint range **automatic** (the default), the `min_temp` and `max_temp` the thermostats show in Home Assistant when the room is set up; with several thermostats the narrowest range that all of them accept. A thermostat that shows no limits is left out, and without any the profile's range applies (4 to 35 °C for the TRVZB). With **manual**, the room's minimum and maximum target temperature. Either way the range is narrowed to the profile's limits, in 0.5 K steps for the TRVZB. A changed limit on a thermostat is read at the next reload of the integration.
- **`current_temperature`:** the value last sent to the thermostats, else the active sensor's.
- **Attributes:** `setpoint_source` (`ha`, `device:<entity_id>`, `restore`, `frost`, `schedule` or `initial`), `in_sync`, `thermostats`, `window_open`, `outdoor_cutoff`, `note`, `presence`, `away_target` and `away_override` (see [Presence](presence.md#presence)), `schedule_entity`, `schedule` and `schedule_override` (see [Schedule](schedule.md#schedule)).
- **`in_sync`** is true when every thermostat is verified at what it should show now (off during a hold).

### Modes

The mode list of the room thermostat has three entries:

| Mode | HA mode | What the room does |
|---|---|---|
| Off / Aus | `off` | Every thermostat is switched off. |
| Heat / Heizen | `heat` | The room heats to its target. Coming from frost protection, the target from before is back. |
| Frost protection / Frostschutz | `auto` | The room heats only to its frost protection temperature (default 5 °C, can be set per room). The target from before is remembered. |

Home Assistant offers only its fixed modes, so frost protection sits in the slot of `auto` and is shown as **Frost protection** (with its own icon where the frontend uses state icons). For voice assistants, HomeKit and automations the frost mode is therefore the HA mode `auto`: use `climate.set_hvac_mode` with `auto` for frost protection, `heat` to heat and `off` to switch off. Easy TRV does not use the thermostat's own schedule, which `auto` otherwise means on a thermostat.

Off ends frost protection, so heat afterwards brings back the target from before, not 5 °C. A new target, in Home Assistant or at a knob, ends frost protection too (the mode shows `heat` at the new target). A window or the outdoor cut-off holds the room as before; the mode shows what the room returns to. The mode survives a restart.

**Update note.** Up to 0.3.0 frost protection was the preset `frost` of the room thermostat; 0.4.0 moved it to `select.<room>_heating_mode`. Automations that call `climate.set_preset_mode` or `select.select_option` on that select now call `climate.set_hvac_mode` with `auto` (frost protection), `heat` or `off`. The select entity is removed at the first start after the update. A room that was on frost protection keeps it.

<details>
<summary>Knob turns, schedules and restarts</summary>

- **The last person who changes it wins**, in Home Assistant or at a knob. A knob turn on one thermostat is taken over as the room's target and spread to the other thermostats once the knob has rested for 3 seconds (turning through several steps is one change). A value outside the range is clamped, also on the thermostat that was turned. The event `easy_trv_setpoint_adopted` fires and `setpoint_source` becomes `device:<entity_id>`. The 3 second spread and the rule below that a knob turn while the room is off switches it on are not yet verified on hardware (spike S8 in [`docs/testing-on-device.md`](../testing-on-device.md)).
- **Knob turn while the room is off** switches the room on (`heat`), but only a turn made after the room went off. A turn made just before is not taken as a wish.
- **During an open window or the outdoor cut-off** a knob turn changes only the wish: the thermostats stay off (the room thermostat keeps showing the wish) and the new target is written when the hold ends.
- **Schedule.** A thermostat that is switched to its weekly schedule (`auto`) is shown (`note: schedule`, shown as `restricted_schedule` by the heating state sensor), left alone and excluded from the room's writes until it is back on `heat` or `off`, when it is brought up to date. Easy TRV never writes `auto`. A window or the outdoor cut-off is the exception: it switches such a thermostat off too (also one that is switched to its schedule during the hold), and it stays off afterwards (see [Window contacts](windows-and-outdoor.md#window-contacts)).
- **Open-window phase of the thermostat.** A thermostat that shows exactly 12 °C while the room wants something else and the room's open-window detection is on is treated as being in its own window phase (`note: window_phase`). It is left alone until it shows another value or 30 minutes have passed.
- **Restart.** The remembered target and mode (including frost protection) win; a thermostat that reports something else is corrected. A thermostat whose mode is not known yet is only switched off, and only when a hold is active. If the room thermostat entity is disabled in Home Assistant, there is nothing to restore from: the room starts as on a first start.
- **First start** (nothing remembered): the room takes the target of the first thermostat that reports one (`setpoint_source: initial`) and writes it to the others, so the setpoints are unified. It starts off only if every thermostat reports off; otherwise the thermostats that report off are switched on.

</details>
