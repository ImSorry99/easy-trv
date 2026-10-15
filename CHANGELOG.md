# Changelog

All notable changes to Easy TRV are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Operating mode per room (`select.<room>_heating_mode`): off, frost protection or heating enabled
- Setting **Setpoint range** (global and per room): automatic, the default, takes the minimum and maximum target temperature the thermostats show in Home Assistant; manual uses the minimum and maximum as before

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

[Unreleased]: https://github.com/ImSorry99/easy-trv/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/ImSorry99/easy-trv/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ImSorry99/easy-trv/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ImSorry99/easy-trv/releases/tag/v0.1.0
