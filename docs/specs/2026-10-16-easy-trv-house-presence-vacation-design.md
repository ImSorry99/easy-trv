# Easy TRV — House-wide presence and vacation (original design, not built)

> **Status: not built.** This is the owner-approved design of 2026-10-16, kept as the plan for a later version. Release 0.7.0 shipped a simpler per-room design instead (`2026-10-18-easy-trv-0.7-presence-design.md`): one presence entity globally or per room, lower or off while away, no vacation and no away delay. What this document adds on top of 0.7.0:
>
> | Topic | 0.7.0 (built) | This design (open) |
> |---|---|---|
> | Presence sources | one entity, global or per room | any number of sources (`person`, `device_tracker`, `binary_sensor`, `input_boolean`), house-wide |
> | Delay before away | none | `away_delay_minutes`, default 15 |
> | All sources unknown | setting `presence_unknown`, default away | counts as home |
> | Vacation | — | own entities: vacation switch, return date and time, preheat hours; frost protection until return − preheat, then normal heating; switches itself off at the return |
> | Override | new target while away, kept across a restart | cleared on every change of the house mode, not kept across a restart |
>
> Section 8 holds the rulings made after mapping the 0.6.1 code; check them against the 0.7 code before building.

Derived from the owner's decisions of 2026-10-16 and the earlier designs (Phase 2, 0.5, 0.6), which stay valid wherever this document is silent.

Owner's words: absence detection configurable from several sources (people in Home Assistant, device locations, Wi-Fi status); for a vacation, set the return and how long before it the heating starts again. Decisions: normal absence lowers the target by a few kelvin; vacation is set with Easy TRV's own entities.

## 1. Away (everyone gone)

### 1.1 Sources

Global option `presence_entities`: any number of entities. Each one says "someone is home" by its state:

| Domain | Home when |
|---|---|
| `person`, `device_tracker` | state `home` (Wi-Fi/router trackers, phone location, …) |
| `binary_sensor`, `input_boolean` | state `on` |

- The house is **away** when no source says home for `away_delay_minutes` (global, default 15, 0 = at once). It is **home** again as soon as one source says home (no delay).
- A source that is `unavailable` or `unknown` counts as neither. If every source is unavailable or unknown, the house counts as **home** (heating stays as it is; nobody gets cold because a router restarts).
- No sources configured: the away feature is off.

### 1.2 What a room does

Setting `away_setback_kelvin` (global with room override, default 3.0, range 0–10 in 0.5 steps, 0 = the room ignores absence).

- While the house is away, a room whose operating mode is heat writes `setpoint − setback` to its thermostats, but never below the room's frost protection temperature and never below the setpoint range minimum. The room's own setpoint (the climate entity's target) does not change; the climate entity keeps showing the wish, as during a window hold.
- A room that is off or on frost protection is not affected.
- Window and outdoor holds win: the thermostats stay off; when the hold ends during absence, the lowered target is written.
- **Someone at the radiator:** a knob turn or a target change from Home Assistant during absence ends the setback for that room until the house is home again (the room heats to the new target; the next absence lowers it again).
- On **home**, every room writes its normal target at once.

## 2. Vacation

### 2.1 Entities

Easy TRV gets one device "Easy TRV" (not a room) with three entities, restored across restarts:

| Entity | Name de / en | Type | Meaning |
|---|---|---|---|
| `switch.easy_trv_vacation` | Urlaub / Vacation | switch | vacation on/off |
| `datetime.easy_trv_vacation_return` | Rückkehr / Return | datetime | when someone is back; empty = open end |
| `number.easy_trv_vacation_preheat` | Vorheizen / Preheat | number, h, 0–48, step 0.5, default 4 | how long before the return the rooms heat again |

### 2.2 What happens

- **Vacation on, before `return − preheat`:** every room whose operating mode is heat holds its frost protection temperature (the room's frost setting). Rooms that are off stay off; rooms on frost stay on frost.
- **From `return − preheat`:** rooms heat to their normal target, ignoring absence (it is warm on arrival, although the phones are still away).
- **At `return`:** vacation switches itself off; away detection applies again from then on.
- No return set: frost until the switch is turned off.
- Turning vacation off early: rooms go back to normal at once (or to the away setback if the house is away).
- A knob turn or HA target change during vacation frost ends vacation for that room only, until vacation is next switched on.
- Window and outdoor holds win as always.

## 3. Heating state

The placeholder `off_away` is replaced by two states:

| Value | de | en | Holds when |
|---|---|---|---|
| `vacation` | Urlaub: Frostschutz | Vacation: frost protection | vacation frost applies to this room |
| `away` | Abwesend: abgesenkt | Away: lowered | the away setback applies to this room |

Order: fault, off_manual, off_window, off_outdoor, **vacation**, **away**, window_pending, emergency, restricted_*, boost, frost, heating. The hint names the details: „Abgesenkt um 3 K, bis jemand zurück ist“ / „Urlaub bis 20.10. 18:00, Vorheizen ab 14:00“ (en to match). During preheat the state is `heating` with the hint „Vorheizen für die Rückkehr um …“.

## 4. Events

`easy_trv_presence_changed` (`home`/`away`), `easy_trv_vacation_changed` (`on`/`off`, `return`), fired when the house-wide state changes.

## 5. Where the logic lives

- Pure: the presence decision (sources → home/away with delay) and the target a room writes (`setpoint`, setback, frost, range, vacation phase, overrides) in `core/`.
- HA side: a small presence tracker (state listeners on the sources, the delay timer), the vacation entities on the new Easy TRV device, the room controller taking `HouseMode` events (`home`, `away`, `vacation_frost`, `vacation_preheat`) like it takes window and outdoor events.

## 6. Tests

- Presence: each domain's home rule; away only after the delay; home at once; all sources unavailable → home; no sources → feature off.
- Room target: setback, frost floor, range floor, setback 0, room off/frost unaffected, holds win, knob/HA change ends setback for the room until home.
- Vacation: frost before preheat, normal from preheat (absence ignored), auto-off at return, open end, early off, per-room knob override, restore after restart.
- Heating state and hint for away, vacation, preheat; events.

## 7. Out of scope

Per-room presence (motion sensors in a room), learning return times, weekly schedules, night setback.

## 8. Rulings after mapping the code (2026-10-16)

- **Away shows only when it lowers.** The state `away` holds only when the written target is below the room's target; the hint names the real lowering (target minus written value), which the frost and range floors can make smaller than the setting.
- **One override flag per room.** A knob turn or an HA target change during away or vacation sets it; it is cleared on every change of the house mode (home, away, vacation frost, vacation preheat) and not kept across a restart. So a room someone used before a vacation is lowered again when the vacation ends and the house is still away.
- **A stale report is no override.** A thermostat that reports the room's normal target while it should show the lowered one (a sleepy device that missed the write) is written the lowered value again, as drift; only a value different from both counts as a knob turn.
- **Automations count as people.** `climate.set_temperature` during absence ends the setback for that room, even with the same value; the README says so.
- **Presence keeps running during a vacation**, so turning the vacation off early applies the away setback at once.
- **Settings:** a new options section "Presence" with the sources, the delay and the global setback; the room form gets the setback before the statistics field.
