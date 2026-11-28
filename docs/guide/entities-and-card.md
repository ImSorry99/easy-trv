# Entities and the dashboard card

[Easy TRV](../../README.md) › [Documentation](README.md)

## Entities

Besides the room thermostat and the sensors, Easy TRV has these entities.

On the house device **Easy TRV**:

| Entity | What it is |
|---|---|
| `select.easy_trv_presence` | Forces presence to home or away for the house (see [Force presence for the house](presence.md#force-presence-for-the-house)). |
| `number.easy_trv_away_setback` | The global setback, 0.5 to 10 K in 0.5 K steps. It is a configuration entity. It writes the option **Setback** and applies it at once, without a reload; rooms that are lowered get the new target. Rooms with their own setback keep it; the attribute `rooms_with_own_setback` lists them. |

On each room's device (read-only, not hidden):

| Entity | What it is |
|---|---|
| `binary_sensor.<room>_window` | On while any window contact of the room reads open (without the opening delay). Only for rooms with window contacts. Attribute `contacts_open`. |
| `binary_sensor.<room>_presence` | On while the room reads home (after the away delay, with forcing applied). Only for rooms with presence. Attributes `source` (`entity` or `forced`), `presence_entity` and `away_from`. |

## The Easy TRV card

The card comes with Easy TRV and is loaded automatically. There is nothing to add under dashboard resources. It appears in the card picker as **Easy TRV**. Or add it in YAML:

```yaml
type: custom:easy-trv-card
```

This shows the house row and every Easy TRV room. Options (all optional; the visual editor has them too):

```yaml
type: custom:easy-trv-card
rooms:                      # room thermostats; filters and orders the rooms
  - climate.living_room
  - climate.bedroom
house: false                # hide the house row (default: shown)
statistics: false           # never show the statistics part (default: shown)
```

- **House row:** the presence select as three buttons, and the setback with − and + in 0.5 K steps.
- **Each room:** name, temperature, target with − and +, the modes Heat, Frost and Off, icons for open window, away and lowered target, and the heating state. When the state is not `heating`, the hint says why. The edge of the block is tinted by the state group. A tap on the name opens the room thermostat.
- **Statistics:** collapsed per room; shown when the statistics sensors exist.

A part with no entity behind it (no contacts, no presence, statistics off) is simply not shown.
