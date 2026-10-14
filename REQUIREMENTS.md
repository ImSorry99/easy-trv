# Easy TRV — Requirements

Owner: @ImSorry99. Living document. The technical design in `docs/specs/` is derived from this file, never the other way round. Covers Phase 1 and Phase 2; Phase 2 items are marked.

## Principles

- **P1 Native first.** Use what the thermostat can do by itself: its regulation, its inputs, later its schedules and timers. Home Assistant only feeds values and checks that they arrived. Fewer writes, more asynchrony, more robustness, less load on HA.
- **P2 Profiles, not hard-coded devices.** A thermostat is driven through a *profile*. A profile states which device and minimum firmware it fits, what the device can do natively, and how Easy TRV talks to it. Phase 1 ships exactly one profile: *Sonoff TRVZB native*. If a native path turns out not to work well, a second profile may drive the same device differently. The native profile is always the first choice.
- **P2a Profiles are data, shipped and tested.** Built-in profiles are declarative files, tested on real hardware with a stated firmware. Users do not click a device together; they take a shipped profile. Anyone can copy a shipped profile into their HA config folder, adapt it, and select it. Shipped profiles are never edited in place.
- **P3 Lean.** Minimal entities: one diagnostic status sensor and one thin pass-through `climate` entity per room. No regulating climate entity, no entity per setting.
- **P4 Verified, not assumed.** Every write is read back. A write that does not arrive becomes visible.
- **P5 Reuse.** Prefer HA and ZHA built-ins (availability, areas, Repairs, selectors, device registry) over own mechanisms.

## Rooms

- **R1** A room groups M thermostats, N temperature sensors (order = priority) and N humidity sensors (stored now, used later).
- **R2** A room can be created *from an HA area* ("this area is the room": name and entities are taken from the area and can be adjusted) or as a *custom room* with manual selection.
- **R3** One Easy TRV config entry is the single source of truth. Rooms are added, edited and removed inside it.
- **R4** A thermostat belongs to exactly one room.

## External temperature

- **T1** The thermostats of a room regulate on the room's *active* sensor: the first configured sensor that is available and delivers a valid value.
- **T2** The value is written when the room temperature changes (at least 0.1 K) **and** re-sent at least every N minutes (default 20, "Auffrischung"), because the TRVZB discards the external value after about two hours of silence.
- **T3** No usable sensor: the thermostats are switched to their internal sensor and the room is marked *degraded*. Sensor back: external again.
- **T4** Sensor availability comes from HA (`unavailable` / `unknown`). An optional extra timeout (default 120 min, 0 = off) covers sensors that stay "available" but stop reporting.
- **T5** If a thermostat silently fell back to its internal sensor, Easy TRV notices it on read-back and re-enables the external source.
- **T6** Writes are independent of the thermostat's own mode; while a *room* is `off`, no external-temperature writes are sent.

## Profiles

- **F1** A profile declares: device match (manufacturer, model), minimum firmware, capabilities, and for each capability the operation (what to write, what to read back, how to compare). Profiles are declarative (YAML), not code.
- **F2** A thermostat below the profile's minimum firmware is not driven. A Repairs issue names the required firmware.
- **F3** Per-room settings with a global default and a per-setting override: built-in open-window detection (on / off / leave), hysteresis (0.2–1.0 K, default 0.4, written only while adaptive mode is not on), adaptive mode (Phase 2; on / off / leave, firmware ≥ 1.4.4, default on), frost protection temperature (Phase 2; 4–16 °C, default 5).
- **F4** Per room, the profile is *auto* (the shipped profile matching the thermostats) or an explicit choice from all shipped and user profiles that match. Per-thermostat choice later.
- **F5** User profiles live in `<config>/easy_trv/profiles/*.yaml`. They are validated at start; an invalid file produces a Repairs issue naming the file and the problem, and is ignored.
- **F6 Room thermostat (Phase 2).** Each room offers one thin `climate` entity (heat / off, frost preset, setpoint range) that stores what a person asked for and feeds it to the thermostats, without regulating; see `docs/specs/2026-10-14-easy-trv-phase2-design.md`, section 5.
- **F7 Window contacts and outdoor cut-off (Phase 2).** Window contacts and an outdoor temperature threshold switch a room off and restore it afterwards, including a check for contacts that report open while the room does not cool; see `docs/specs/2026-10-14-easy-trv-phase2-design.md`, section 6.

## Robustness

- **B1** Write → wait (default 10 s) → read back → compare. Up to 3 attempts (configurable), backoff 5 / 15 / 45 s.
- **B2** After the last failed attempt: Repairs issue for that thermostat, bus event, room status *error*, one log warning. Cleared automatically on the next verified write.
- **B3** A failing thermostat never blocks the others. Thermostats that HA reports as unavailable are skipped without an issue.
- **B4** Writes per room are serialized. Zigbee is never flooded.

## Visibility

- **V1** One status sensor per room: `ok` / `degraded` / `error`, with few attributes (active sensor, source, per-thermostat health, last error).
- **V2** Diagnostics download with the full state. Events `easy_trv_write_failed`, `easy_trv_recovered`, `easy_trv_failsafe_detected`.
- **V3** UI in English and German.

## Out of scope for Phases 1 and 2

Native weekly schedules, presets beyond frost, night setback, presence, per-thermostat profiles or contacts, battery warnings, Zigbee2MQTT, other thermostat models.

## Roadmap

The ordered roadmap lives in the maintainer's planning notes outside this repo. Candidates, each as profile operations where possible: presence, native weekly schedules, validation that no weekly schedule lives on the device, presets, open-plan rooms, humidity use, learning.
