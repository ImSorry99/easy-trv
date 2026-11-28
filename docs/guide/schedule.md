# Schedule

[Easy TRV](../../README.md) › [Documentation](README.md)

## Schedule

A room can follow a Home Assistant **schedule helper** (Settings > Devices & services > Helpers > Schedule). Choose it in the room form, field **Schedule**. Each room has its own schedule, so rooms can run opposite to each other: the living room on in the day, the bedroom only in the evening.

- **Schedule on:** the room heats to its target.
- **Schedule off:** the room is lowered by **Setback outside the schedule** (default 3 K, up to 15 K), never below the frost protection temperature. A large setback therefore brings the room down to frost protection. The heating state is `setback_schedule`, and `away_target` shows the value sent.
- **Target per block:** give a block the additional data `temperature: 21` (in the helper's block dialog, *Advanced settings*). When that block starts, 21 °C becomes the room's target, as if you had set it. A block without it leaves the target alone.
- **Changing the target by hand:** while the schedule is on, a new target lasts until the next block with a `temperature`. While it is off, a new target (in Home Assistant or at a knob) is an override: the room heats to it until the schedule is on again (attribute `schedule_override`, survives a restart).
- **Preheating:** let the block start as early as the room needs to warm up. Bedtime at 22:00 and a bedroom that takes an hour: start the block at 21:00.
- **With presence:** away and schedule off do not add up; the larger setback applies. Away with **Off** holds the room off as before.
- The schedule acts only while the room's mode is **Heat**. A block temperature that arrives in **Frost protection** is the target the room returns to; one that arrives while the room is **Off** is its target when you switch it on.
- A schedule that is `unavailable`, `unknown` or missing counts as on, so a broken helper never leaves a room cold. After a restart the room keeps its remembered target; the next block sets it again.
- The room thermostat shows `schedule_entity`, `schedule` (`on` or `off`) and `schedule_override`.

This is a Home Assistant helper, not the thermostat's own weekly schedule (`auto`), which Easy TRV still leaves alone.
