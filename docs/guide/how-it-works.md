# How it works and limitations

[Easy TRV](../../README.md) › [Documentation](README.md)

## How it behaves

- **Active sensor.** A sensor is usable if it is available, shows a number in °C (°F is converted), lies between 0 and 99.9 °C, and, only if a sensor timeout is set (off by default), has reported within it. The first usable sensor in the room's order is the active one.
- **Writes.** When the active sensor's value changes by at least the minimum change, the value is written to every thermostat of the room after a 5 second pause that merges bursts of reports. Independently it is written again every refresh interval. Writes do not depend on the thermostat's own mode, but while the room is switched off or held by a window or the cut-off, no temperature is written; it follows when the room heats again.
- **Verification.** The thermostat's answer to a write confirms it. The room temperature, the source, the target and the mode are also read back after the read-back delay, for the fail-safe, the schedule and knob turns; a read-back that fails is no failure. A write that does not arrive is retried with a growing pause (5, 15, then 45 seconds) up to the configured number of attempts. zigpy already repeats each of these requests up to three times itself. A single write that does not get through is normal for Zigbee, so a thermostat is marked as failing (see the heating state `fault`, Repairs and events below) only when its writes have been failing for 60 minutes; until then it fails quietly while the retries run. Settings the thermostat already has (as zigpy last knew them) are not written again. Easy TRV never stops retrying: a setting, the temperature source, the mode or the setpoint that did not arrive gets a fresh set of attempts every refresh interval, and the room temperature is written again every refresh interval anyway. The problem clears by itself once every one of them was verified, or once the room no longer writes the failing value (for example while the room is off or the thermostat runs its own schedule). Writes within a room happen one at a time so the Zigbee network is not flooded; a thermostat that does not answer can therefore hold up the others of its room in each of those rounds, up to about 85 seconds per attempt (zigpy's own three tries), which with several values owed adds up to many minutes.
- **Fail-safe.** When a TRVZB gets no external value for about two hours it silently falls back to its internal sensor. ZHA still shows the external source as on. Easy TRV reads the source flag back together with every value write, notices the fallback, switches the external source on again, writes the value and fires `easy_trv_failsafe_detected`.
- **No usable sensor.** If none of the room's sensors is usable, the thermostats are switched to their internal sensor and the room's heating state is `emergency`. When a sensor is usable again, the external source is switched on again.
- **Unavailable thermostats.** A thermostat that Home Assistant reports as unavailable is skipped without an issue and brought up to date when it returns.

Rooms start once Home Assistant has fully started.

## Limitations

- **Hold by off and restore.** Window contacts and the cut-off switch the thermostats off and back on; they do not use a timer on the thermostat. If Home Assistant is down during a hold they stay off.
- **No thermostat schedules.** Schedules come from a Home Assistant helper (see [Schedule](schedule.md#schedule)); Easy TRV never writes a thermostat's `auto` mode; a thermostat someone puts into it is left alone, except that a window or the outdoor cut-off switches it off, and it then stays off until you switch it back (Repairs issue `schedule_thermostat_off`).
- **Writes while a room is off.** No temperature is written while the room is off or held. Whether a write could wake an `off` thermostat is one of the open checks in [`docs/testing-on-device.md`](../testing-on-device.md).
- **Sonoff TRVZB built in.** Other models need a profile: your own (Beta, through their entities), an uploaded one or one from the community.
- **ZHA's internal API.** Writing and verifying attributes needs ZHA's internal API. Easy TRV keeps all of it in one adapter module, so a future Home Assistant change is fixed in one place. If the API breaks, the `zha_api_incompatible` issue says so.
- **Presence:** one entity per room (the global one or its own), no vacation with a return time, no away delay per room, no forced presence per room.
- **Not used yet:** humidity sensors, presets, battery warnings.
