# Changelog

All notable changes to Easy TRV are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- The sensor timeout is off by default (was 120 minutes). Battery Zigbee temperature sensors report only on a change of about 0.2 °C and can stay silent for many hours while still available; with the old default the room fell back to the thermostat's internal sensor for hours. A sensor that is unavailable or has no value is still unusable. The timeout remains an option for sensors that report regularly

## [0.7.1] - 2026-10-22

### Fixed

- A thermostat that is switched off (open window, outdoor or away hold, room off) reports its frost protection temperature as its setpoint. Easy TRV took that for a knob turn: during a hold the room's target became the frost temperature (raised to the room minimum) and was written when the hold ended; a room that was off was switched on again. That report is now ignored; a turn to any other value still counts

### Changed

- The default refresh interval is 30 minutes instead of 20, as in Better Thermostat. The TRVZB drops the external value only after about two hours without a write

## [0.7.0] - 2026-10-19

### Added

- Presence: a presence entity in the integration options (new section **Presence**: `presence_entity`), per room **Global**, **None** or its own entity (`presence`, `presence_entity`). A `person`, `device_tracker`, `zone`, `group`, `binary_sensor` or `input_boolean` decides whether someone is home
- Setting **While away** (`away_action`): **Lower** (default) lowers the target by **Setback** (`away_setback_kelvin`, 0.5 to 10 K, default 3 K, never below the frost protection temperature); **Off** holds the room off like an open window
- Setting **When presence is unknown** (`presence_unknown`): an unknown or unavailable presence counts as **Away** (default) or **Home**
- A new target while away, in Home Assistant or at a knob, overrides presence until someone is home; it survives a restart
- Heating state `setback_away`; `off_away` now occurs. The hint names the presence entity
- Room thermostat attributes `presence`, `away_target` and `away_override`; event `easy_trv_away`

### Changed

- The control deviation measures against the lowered target while a room is lowered

## [0.6.1] - 2026-10-16

### Added

- Setting **Collect statistics** (`statistics`) with the values **Off**, **Hidden (keeps collecting)** and **Visible**, global in the new options section **Statistics** and per room in the room settings. It decides for each room whether the statistics sensors of 0.6.0 exist and whether they are hidden from lists and automatic dashboards. Hidden sensors keep collecting and the recorder records them. **Off** creates none and removes the existing ones of the room, so their data stops. A sensor you show again by hand stays shown, and one you hid yourself is never touched

### Changed

- The statistics sensors are off by default for new installations. An update from 0.6.0 or older sets the setting to **Hidden** in the integration options, so the sensors of 0.6.0 keep collecting and drop out of lists and automatic dashboards (an installation older than 0.6.0 gets them, hidden, for the first time); set it to **Visible** to see them, or to **Off** to remove them
- The update raises the version of the stored configuration entry to 3. Easy TRV 0.6.0 can no longer load it, so going back to 0.6.0 is not supported and needs a restore of a backup from before the update

## [0.6.0] - 2026-10-15

### Added

- Diagnostic statistics sensors per room, recorded as long-term statistics: **Control deviation** (`sensor.<room>_deviation`, K: the active sensor minus the target while the room's mode is heat and no hold is active) and **Heating time** (`sensor.<room>_heating_time`, h: while at least one thermostat reports `hvac_action: heating`; unavailable until one has reported an `hvac_action`). Rooms with window contacts also get **Window openings** (`sensor.<room>_window_openings`, counted when the contacts go from all closed to any open, before the opening delay) and **Window open time** (`sensor.<room>_window_open_time`, h)
- Diagnostic statistics sensors per thermostat: `<thermostat> writes` (every write request sent, retries included), `<thermostat> failed writes` (an error, a timeout or a read-back that differs) and `<thermostat> dropouts` (the climate entity going from available to unavailable, which a ZHA reload also causes), for example `sensor.<trv>_writes` for `climate.<trv>`. They show data Home Assistant does not record by itself
- The counters and hours are totals that only grow and continue after a restart or a reload of the integration. Removing a thermostat from a room, or moving it, removes its statistics sensors from the old room; removing the last window contact removes the two window sensors

## [0.5.1] - 2026-10-15

### Added

- Attribute `hint` on the heating state sensor: plain text in the language of Home Assistant that names the thermostats and window contacts behind a fault or a restriction, for example "Sofa valve not reachable; Balcony door reports nothing (unavailable)"
- The heating state sensor shows the attributes `active_sensor`, `source`, `setpoint_source`, `in_sync` and `last_error` that the status sensor showed

### Changed

- The status sensor is merged into the heating state sensor, which is now a diagnostic entity (it sits in the diagnostic group of the room device). The status sensor is removed on update; automations on `sensor.<room>_heating_status` move to `sensor.<room>_heating_state`: `ok` is about `heating`, `frost` and the `off_*` states, `degraded` about `emergency` and the `restricted_*` states, `error` is `fault`. Its attributes `trv_health`, `window_open`, `outdoor_cutoff` and `schedule_thermostats` are gone; `thermostats` and `reasons` say the same
- The heating state `restricted` is split into five states that say what restricts the room: `restricted_unavailable`, `restricted_contact`, `restricted_unsupported`, `restricted_schedule` and `restricted_window_phase`. When several hold, the first of them is the state and all are in the `reasons`
- A write that does not arrive is retried every refresh interval for every value, settings included, until it arrives; the Repairs notice stays until every one of them arrived (or is not written to that thermostat any more, like the mode of a thermostat on its own schedule) and names the one that is still missing

### Fixed

- A write whose retry was no longer needed (the room went off, the thermostat went on its own schedule or away) kept its failed attempts, so the next failure of that value raised the Repairs notice at once without retries

## [0.5.0] - 2026-10-15

### Added

- Sensor **Heating state** per room (`sensor.<room>_heating_state`): one of eleven states (`fault`, `off_manual`, `off_window`, `off_outdoor`, `off_away`, `window_pending`, `emergency`, `restricted`, `boost`, `frost`, `heating`) with the attributes `reasons`, `since`, `window_off_at`, `thermostats` and `silent_contacts`; `off_away` and `boost` are reserved and do not occur yet
- Event `easy_trv_state_changed` (`room`, `state`, `previous`, `reasons`) when the heating state changes
- Diagnostics carry the room state: `heating_state`, `state_reasons`, `state_since`, `window_off_at`, `silent_contacts` and `deviations` for every room
- Repairs issue `window_contact_unavailable`: a window contact that has said nothing (unavailable, unknown or missing) for 10 minutes is reported; the room shows `restricted`, or lists it among the `reasons` when a state with higher priority holds
- Optional area for custom rooms and when editing a room; the room's device is put into the room's area at every start when it has none (so rooms from older versions get theirs), and moved when the area is changed in the edit form
- Setting **Hide thermostat entities** (global and per room, default off): hides the climate entity of each thermostat and the six device settings Easy TRV writes (a setting left as set in the thermostat stays visible); they keep working and are shown again when the setting is turned off, the thermostat or room is removed, or the integration is removed
- Profile key `entity` (`domain` and `translation_key`, both required) on a capability, naming the entity that shows it; only used for hiding

### Changed

- The status sensor is named **Heating status** ("Heizungsstatus") instead of "Status"; its entity id does not change
- A running window opening delay is visible in the room: heating state `window_pending` and the attribute `window_off_at`

### Fixed

- An unavailable window contact is no longer invisible: the room shows `restricted`, or lists it among the `reasons` when a state with higher priority holds, and Repairs reports it after 10 minutes. The behaviour is unchanged: the contact keeps its last state, and one never seen counts as closed

## [0.4.1] - 2026-10-15

### Changed

- Frost protection is selected in the room thermostat's mode list again (HA mode `auto`, shown as Frost protection); the separate operating mode select is removed. Automations using `select.select_option` on `select.<room>_heating_mode` switch to `climate.set_hvac_mode` with `auto`/`heat`/`off`

### Fixed

- `climate.set_temperature` with an `hvac_mode` applies that mode too; an unsupported mode is rejected

## [0.4.0] - 2026-10-15

### Added

- Operating mode per room (`select.<room>_heating_mode`): off, frost protection or heating enabled
- Setting **Setpoint range** (global and per room): automatic, the default, takes the minimum and maximum target temperature the thermostats show in Home Assistant; manual uses the minimum and maximum as before
- Setting **Warn only when it is colder outside** (global and per room, default on): the open-window warning waits while the outdoor temperature is not below the room temperature; the outdoor sensor is also read for rooms with window contacts and no cut-off

### Changed

- The room thermostat's range is automatic by default: rooms without their own choice take the thermostats' limits (4 to 35 °C for a TRVZB that shows none) instead of 5 to 30 °C. Options and rooms that already set their own minimum or maximum stay manual
- Frost protection is no longer the preset `frost` of the room thermostat but an operating mode; the room thermostat has no presets. Automations that call `climate.set_preset_mode` need `select.select_option` instead. A room on frost protection keeps it after the update
- Switching a room off ends frost protection; heating enabled afterwards brings back the target from before
- The global options form shows every number with its value, the built-in default until it is changed (for example 30 s window delay instead of an empty field); a number left at its default is not stored
- The sections of the global options form start collapsed

### Fixed

- A room whose profile is left at 'global' could not be set up ('profile 'global' does not exist'); it now uses the global profile choice

## [0.3.0] - 2026-10-15

### Added

- Room thermostat: one `climate` entity per room with heat and off, a frost preset and a setpoint range
- Knob adoption: a turn at one thermostat becomes the room's target and is spread to the others; thermostats on their own schedule are left alone
- Window contacts switch the room off while open and restore it when closed, with a long-open warning and a hint for contacts that look stuck
- Outdoor cut-off switches a room off above a global or per-room outdoor temperature, with a 0.5 K deadband
- Diagnostics list the holds and the latest observations per thermostat
- Setting `window_open_delay_seconds` (0 to 600, default 30, 0 = at once): a window contact must stay open this long before the room is switched off; closing is not delayed
- Bus events `easy_trv_setpoint_adopted`, `easy_trv_window_hold` and `easy_trv_outdoor_hold`
- New attributes: the room status sensor shows `window_open`, `outdoor_cutoff`, `setpoint_source`, `in_sync` and `schedule_thermostats`; the room thermostat shows `setpoint_source`, `in_sync`, `thermostats`, `window_open`, `outdoor_cutoff` and `note`
- Repairs issue `schedule_thermostat_off` for a thermostat on its schedule that a window or the outdoor cut-off switched off

### Changed

- No external-temperature writes while a room is off or held by a window or the outdoor cut-off
- A window or the outdoor cut-off switches every thermostat off, those on their weekly schedule included. Easy TRV never writes `auto`, so a thermostat that was on its schedule stays off after the hold until you switch it back in ZHA
- First start without a remembered state: the room takes the target of the first thermostat that reports one and writes it to the others (setpoints are unified), and takes over their mode: it starts off only if every thermostat is off, otherwise thermostats that report off are switched on
- A contact that becomes unavailable keeps its last state, so an open window stays open; a contact that never reported counts as closed
- A setpoint or mode write that waited in the queue is sent with the room's target at the moment it runs
- Setting the room to heat while it heats already writes nothing
- `in_sync` is also false while a thermostat is known to show something other than what the room wants
- Exhausted setpoint and mode writes are sent again at every refresh interval

## [0.2.0] - 2026-10-14

### Added

- Adaptive mode and frost protection temperature as profile capabilities
- Every setting has a global default and can be overridden per room
- Frost protection temperature defaults to 5 °C (range 4 to 16 °C)
- Diagnostics list the resolved settings of every room

### Changed

- Hysteresis follows the global value (default 0.4 K) and is written whenever the adaptive mode is not on; rooms that had 'leave unchanged' are migrated and get a Repairs notice
- Adaptive mode is on by default and is written to every thermostat with firmware 1.4.4 or newer; while it is on the thermostat ignores the hysteresis and Easy TRV does not write it. Thermostats below 1.4.4 get a firmware warning and the room shows degraded, unless adaptive mode is set to off or leave as set in the thermostat (globally or for that room)
- Room devices are named 'Heizung {room}' / '{room} heating'; new room devices are assigned to their area, existing devices keep their area setting
- Repairs messages name the setting and the thermostat; clearer texts for the thermostat's open-window detection and the adaptive mode

## [0.1.0] - 2026-10-13

### Added

- Rooms created from Home Assistant areas or as custom rooms, grouping thermostats and temperature sensors.
- Thermostat profiles as declarative YAML files: a shipped Sonoff TRVZB native profile and user profiles from the config folder.
- External temperature for the thermostats with periodic refresh and verified writes (write, wait, read back, retry).
- Fail-safe detection: a thermostat that fell back to its internal sensor is switched back to the external source.
- Repairs issues for failed writes, outdated firmware and invalid profiles, plus a status sensor per room.

[Unreleased]: https://github.com/ImSorry99/easy-trv/compare/v0.7.1...HEAD
[0.7.1]: https://github.com/ImSorry99/easy-trv/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/ImSorry99/easy-trv/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/ImSorry99/easy-trv/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/ImSorry99/easy-trv/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/ImSorry99/easy-trv/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/ImSorry99/easy-trv/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/ImSorry99/easy-trv/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/ImSorry99/easy-trv/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ImSorry99/easy-trv/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ImSorry99/easy-trv/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ImSorry99/easy-trv/releases/tag/v0.1.0
