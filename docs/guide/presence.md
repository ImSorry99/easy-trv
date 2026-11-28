# Presence

[Easy TRV](../../README.md) › [Documentation](README.md)

## Presence

Choose a presence entity in the integration options (**Presence**); every room with **Global** follows it. A room can choose its own entity instead, or **None** to ignore presence.

| Domain | Home | Away |
|---|---|---|
| `person`, `device_tracker` | `home` | any other state (`not_home`, a zone name) |
| `zone` (for example `zone.home`) | a number above 0 | `0` |
| `group` | `home` or `on` | `not_home` or `off` |
| `binary_sensor`, `input_boolean` | `on` | `off` |

Any other state (`unknown`, `unavailable`, a missing entity) counts as set in **When presence is unknown**: away by default, or home. Away counts only after the **Away delay** (15 minutes by default; 0 = at once). Home counts at once and ends a running delay.

- **Lower** (default): the thermostats get the room's target minus the setback (default 3 K), never below the frost protection temperature; a target already below it stays. The room thermostat keeps showing your target; the attribute `away_target` shows the value sent, and the heating state is `setback_away`.
- **Off:** the room is held off like with an open window (heating state `off_away`). It is a third hold beside the window and the outdoor cut-off; the room returns when all are clear.
- **Away delay:** the entity must read away for the whole delay without a break. While it runs the room behaves as home, and the room's presence sensor has the attribute `away_from` with the time away will count. An entity that has been away for longer than the delay when Easy TRV starts counts as away at once. The delay is global; there is none per room.
- **Home again:** the target (and the mode) come back.
- Presence acts only while the room's mode is **Heat**; **Off** stays off and **Frost protection** is below any setback.
- **Override:** a new target while away, set in Home Assistant or turned at a knob, holds until presence reads home: the room heats normally (with **Off**, it ends the away hold). The next time everybody leaves, presence acts again. The override survives a restart (attribute `away_override`). A thermostat that still reports the target from before the room was lowered is not taken as a knob turn; it gets the lowered value again.
- The event `easy_trv_away` (`room`, `state`: `on` or `off`) fires when presence starts or stops acting on the room. The room thermostat shows `presence` (`home`, `away` or `none`).

### Force presence for the house

The select `select.easy_trv_presence` on the device **Easy TRV** has three options: **Automatic** (default), **Home** and **Away** (German: Automatisch, Zuhause, Abwesend).

- **Home** and **Away** override what every room with presence reads, at once and without the away delay. Rooms without presence are not affected.
- The select returns to **Automatic** by itself when the global presence entity changes between home and away. Without a global presence entity it stays until you set it back. A change of a room's own entity does not end it.
- After a restart the option stays, unless the global presence entity reads differently than when you set it. Then it returns to **Automatic**. While the global entity is `unknown`, `unavailable` or missing at start, the comparison waits for its first home or away state.
- Attributes: `presence_entity` and `forced_at` (the time the option was set; absent on Automatic).
- While it is forced, the heating state hint names "Easy TRV presence" instead of the entity.
- The event `easy_trv_presence_forced` (`option`: `auto`, `home` or `away`; `reason`: `user` or `presence_changed`) fires when the option changes.
