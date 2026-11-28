# Window contacts and outdoor cut-off

[Easy TRV](../../README.md) › [Documentation](README.md)

## Window contacts

Choose any number of window, door, opening or garage door `binary_sensor` entities in the room form. They may belong to any area: the kitchen's window can belong to the living room when the two are joined.

- **Opening delay.** A contact must stay open for the delay (default 30 seconds, 0 = immediately) before the window counts as open: airing for a moment switches nothing off. Closing is never delayed. A contact that is open when Easy TRV starts counts at once.
- **Any contact open:** all thermostats are switched off (`system_mode off`), those on their weekly schedule and in their own open-window phase included, and the room remembers its mode and target. `window_open` is true, the event `easy_trv_window_hold` fires.
- **All contacts closed:** the previous mode and target come back. Changes made in Home Assistant during the hold are applied now. A thermostat that was on its schedule stays off, because Easy TRV never writes `auto`: the Repairs issue `schedule_thermostat_off` asks you to switch it back to its schedule in ZHA and disappears when it reports `auto` or `heat` again.
- **No time limit.** After the open-window warning (default 120 minutes, 0 = never) a Repairs issue `window_open_long` is raised. By default it is raised only while it is colder outside than the room's active sensor shows (setting **Warn only when it is colder outside**, needs the outdoor temperature sensor of the integration options); if it gets colder later, it is raised then, and with the outdoor temperature unknown it is raised as before. Set it to **Always** to warn regardless of the outdoor temperature. The heating is switched off while the window is open either way.
- **Stuck contact hint.** If the room temperature has not fallen by the expected cooling (default 0.3 K) after the window contact check (default 30 minutes, 0 = off), the Repairs issue `window_contact_suspect` says the contact may be stuck or its battery flat. The hold continues; you decide.
- **A contact that becomes unavailable or unknown keeps its last state:** one that was open stays open (nobody said it was closed), so the hold and its Repairs issues stay. A contact that has never reported a state counts as closed. `in_sync` is false while a contact says neither open nor closed; the log notes the change once at debug level. Easy TRV does not act on the silence, but it shows it: the room's heating state is `restricted_contact` (or `restricted_contact` is listed among the `reasons` when a state with higher priority holds), with the contact in the attribute `silent_contacts` and named in the `hint`, and after 10 minutes of silence the Repairs issue `window_contact_unavailable` names the contacts. The issue disappears when every contact reports a state again.

<details>
<summary>Restart and Home Assistant down</summary>

- After a restart during a hold the room comes back in the hold: it looks at the contacts, and no heat is written while one is open.
- If Home Assistant is down during a hold, the thermostats stay off with their own frost protection until it is back. This is the trade-off of "off and restore" (as opposed to a timer on the thermostat).
- The thermostat's own open-window detection is independent of the contacts and stays as set.

</details>

## Outdoor cut-off

Choose an outdoor temperature sensor in the integration options (**Windows and outdoor**) and a cut-off temperature, globally or per room (a room can also choose **Off**). A room is switched off at or above the threshold, like with an open window, and switched on again when the outdoor temperature falls below the threshold minus 0.5 K. There is no averaging. An unavailable sensor ends the cut-off and writes a warning to the log. The state shows as `outdoor_cutoff`, the event `easy_trv_outdoor_hold` fires. A window hold and the cut-off are independent: the room returns when both are clear.
