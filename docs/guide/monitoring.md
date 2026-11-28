# Heating state, Repairs, events and diagnostics

[Easy TRV](../../README.md) › [Documentation](README.md)

## What you see

Each room is a device ("Heizung Wohnzimmer", see [Configuration](configuration.md)) with the sensor **Heating state** (German "Heizungszustand"), which says what the room is doing and why, and, if you collect them, the diagnostic [statistics sensors](statistics.md#statistics).

### Heating state

The sensor, for example `sensor.living_room_heating_state`, sits in the diagnostic group of the room's device and is meant for dashboards and automations. It has one state: the first of the following conditions that holds. Until the room has started (Home Assistant fully started) the state is unknown.

| State | Shown as | Holds when |
|---|---|---|
| `fault` | Fault / Störung | A thermostat did not accept a write after all attempts. |
| `off_manual` | Off (manual) / Aus (manuell) | The room's mode is off. It comes before the holds: while you have switched the room off, a window decides nothing, and the room stays off after the window closes. |
| `off_window` | Off: window/door open / Aus: Fenster/Tür offen | An open window contact holds the room off (the opening delay has passed). |
| `off_outdoor` | Off: outdoor temperature / Aus: Außentemperatur | The outdoor cut-off holds the room off. |
| `off_away` | Off: away / Aus: Abwesenheit | Presence reads away and the room's **While away** is off (see [Presence](presence.md#presence)). |
| `window_pending` | Window open, switching off soon / Fenster offen, schaltet gleich ab | A contact is open and the opening delay is still running. |
| `emergency` | Emergency mode / Notbetrieb | The room has temperature sensors but the thermostats are not on the external source (no usable sensor, for example), or a thermostat signalled its fail-safe within the last refresh interval. |
| `restricted_unavailable` | Restricted: thermostat not reachable / Eingeschränkt: TRV nicht erreichbar | A thermostat is unavailable. |
| `restricted_contact` | Restricted: window contact silent / Eingeschränkt: Fensterkontakt meldet nichts | A window contact says nothing (`unavailable`, `unknown` or missing). |
| `restricted_unsupported` | Restricted: thermostat lacks a function / Eingeschränkt: TRV-Funktion fehlt | A thermostat lacks a function the profile has: its firmware is too old for a setting or the device reports it as unsupported. |
| `restricted_schedule` | Restricted: thermostat on its schedule / Eingeschränkt: TRV im Wochenplan | A thermostat runs its own weekly schedule (`auto`). |
| `restricted_window_phase` | Restricted: thermostat's window detection active / Eingeschränkt: TRV-Fenstererkennung aktiv | A thermostat is in its own open-window phase (it shows 12 °C). |
| `boost` | Manual (boost) / Handbetrieb (Boost) | Does not occur yet; reserved for boost. |
| `setback_away` | Lowered: away / Abgesenkt: abwesend | Presence reads away and the room is lowered below its target. |
| `setback_schedule` | Lowered: schedule / Abgesenkt: Zeitplan | The room's [schedule](schedule.md#schedule) is off and the room is lowered below its target. |
| `frost` | Frost protection / Frostschutz | The room's mode is frost protection. |
| `heating` | Heating / Heizbetrieb | The room's mode is heat. |

The five `restricted_*` states are in the order of the table: when several hold, the first one is the state and all of them are in the `reasons`.

Attributes, in this order:

| Attribute | Content |
|---|---|
| `hint` | Plain text naming the thermostats and window contacts behind a `fault` or `restricted_*` reason, in the language of Home Assistant; empty (`None`) when there is nothing to name and before the room has started. See below. |
| `reasons` | Every condition above that holds, in the same order. The first one is the state. The room's mode (`off_manual`, `frost` or `heating`) is always in the list. |
| `since` | When the state last changed, as an ISO timestamp. After a restart it is the time of the first evaluation. |
| `window_off_at` | While the opening delay runs: when it ends and the room is switched off. Otherwise empty. |
| `thermostats` | Thermostat climate entity and why it deviates: `error`, `unavailable`, `unsupported`, `schedule` or `window_phase` (the first that applies). Only thermostats that deviate are listed. |
| `silent_contacts` | Window contacts that say nothing (`unavailable`, `unknown` or missing), sorted. |
| `active_sensor` | Entity id of the sensor in use, or empty. |
| `source` | `external`, `internal` or `none` (no sensors configured, nothing switched yet). |
| `setpoint_source`, `in_sync` | As on the room thermostat. |
| `last_error` | The last write error of a failing thermostat, or empty. |

**The hint.** The `hint` has one phrase for each thermostat or contact behind a reason that holds, in the order of the `reasons`, joined by `; `. A thermostat is named as on its device page, a window contact by its name (its entity id if the entity does not exist). The phrases, here in English:

| Reason | Phrase |
|---|---|
| `fault` | `<thermostat>: error` |
| `restricted_unavailable` | `<thermostat> not reachable` |
| `restricted_contact` | `<contact> reports nothing (<why>)` with `unavailable`, `unknown` or `does not exist` as the reason |
| `restricted_unsupported` | `<thermostat>: lacks a function (<functions>)` with the plain-language names of the settings it lacks |
| `restricted_schedule` | `<thermostat> runs its own schedule` |
| `restricted_window_phase` | `<thermostat>: own window detection active` |
| `setback_schedule` | `<schedule> off` |

For example "Sofa valve not reachable; Balcony door reports nothing (unavailable)" or, in German, "Sofa valve nicht erreichbar; Balkontür meldet nichts (nicht verfügbar)".

The status sensor of 0.5.0 (`sensor.<room>_heating_status`) is gone; the heating state replaces it (see the [`CHANGELOG`](../../CHANGELOG.md)).

The event `easy_trv_state_changed` (`room`, `state`, `previous`, `reasons`) fires whenever the state changes from one value to another. It does not fire for the first evaluation after a start.

### Repairs

| Issue | Severity | Raised when | Cleared |
|---|---|---|---|
| `trv_unreachable` | Warning | A thermostat's writes have been failing for 60 minutes. The message names the setting (for example "Hysteresis") and the thermostat as you know it from the device page. The message also shows what ZHA last knew about the thermostat's radio (battery, LQI, RSSI, minutes since it was last heard, read from ZHA's cache without radio traffic) and names the likely cause: an almost empty battery (15 % or less), a thermostat that has not been heard for 30 minutes, or a weak signal (LQI below 50; how meaningful the LQI is depends on the coordinator). | When every write that failed is verified, or is not written to that thermostat any more (Easy TRV sends them again at every refresh interval, and also when the thermostat returns after being unavailable), or on a reload of the integration (raised again if the problem persists). |
| `trv_silent` | Warning | ZHA has not heard anything from a thermostat for 3 hours (half of the six hours after which ZHA marks a battery device unavailable), whether or not Easy TRV is writing to it. The message names the thermostat and shows battery, LQI and RSSI. Not raised while the thermostat already has `trv_unreachable`. | As soon as ZHA hears from the thermostat again, or on a reload of the integration. |
| `trv_firmware` | Warning | The thermostat's firmware is too old for a setting (hysteresis below 1.3.0, adaptive mode below 1.4.4), or the thermostat reports the setting as unsupported. The message names the setting, the thermostat and the required and installed firmware. Easy TRV does not retry it. | On a reload of the integration, for example after a firmware update (raised again if the problem persists). |
| `settings_migrated` | Warning | After the update to Phase 2a: rooms that had "leave unchanged" for the hysteresis now follow the global hysteresis. The notice lists them. | When you dismiss it. |
| `window_open_long` | Error | A window has been open longer than the window warning. | When the contacts report closed, or on a reload of the integration (raised again if the window is still open). |
| `window_contact_suspect` | Warning | A contact reports open but the room did not cool by the drop within the check time. | When the contacts report closed, or on a reload of the integration. |
| `window_contact_unavailable` | Warning | A window contact has said nothing (`unavailable`, `unknown` or missing) for 10 minutes. The message names the contacts that are silent at that moment and says how Easy TRV treats them: a contact last seen closed (or never seen) counts as closed, one last seen open keeps the heating off until it reports again. The room shows `restricted_contact`, or lists it among the `reasons` when a state with higher priority holds. | When every contact reports a state again, or on a reload of the integration (raised again after 10 minutes if the problem persists). |
| `schedule_thermostat_off` | Warning | A window or the outdoor cut-off switched off a thermostat that was running its own schedule. It stays off after the hold, because Easy TRV never writes `auto`. The message names the thermostat and the room. | When the thermostat reports `auto` or `heat` again, or on a reload of the integration (the room then brings it to `heat`). |
| `zha_api_incompatible` | Error | Easy TRV cannot use the installed ZHA's internal API. Setup is retried. | When setup works. |
| `profile_invalid` | Warning | A user profile file is invalid and is ignored. The issue names the file and the problem. | When the file loads cleanly after you fix it and reload the integration. |
| `room_invalid` | Warning | A room could not be set up (for example a thermostat that is not a ZHA device, no profile that fits it, or room data that cannot be read). The other rooms keep running. | When the room sets up cleanly after you fix it and reload the integration. |

Issues of thermostats that no longer belong to any room are removed when the integration loads.

### Events

Events are fired on the Home Assistant bus (Developer tools > Events) and can trigger automations.

| Event | When |
|---|---|
| `easy_trv_write_failed` | A thermostat's writes have been failing for 60 minutes. |
| `easy_trv_recovered` | A thermostat that was reported (`easy_trv_write_failed`) accepted every write it owed again. A failure that ends within the hour fires nothing. |
| `easy_trv_failsafe_detected` | A thermostat had fallen back to its internal sensor and was switched back. |

Payload of these three: `room`, `room_id`, `trv_entity_id`, `ieee`. `easy_trv_write_failed` adds `attribute` (the capability that failed, for example `external_temperature`), `minutes` (how long it has been failing) and `error`.

Four more events report the room thermostat, the holds and the heating state. `easy_trv_setpoint_adopted` (`room`, `trv`, `value`) fires when a knob turn or mode change at a thermostat becomes the room's. `easy_trv_window_hold` (`room`, `state`: `open` or `closed`) and `easy_trv_outdoor_hold` (`room`, `state`: `on` or `off`) fire when a hold starts or ends, and `easy_trv_away` (`room`, `state`: `on` or `off`) when presence starts or stops acting on the room. `easy_trv_state_changed` (`room`, `state`, `previous`, `reasons`) fires when the [heating state](#heating-state) changes.

### Debug log

With debug logging on for `custom_components.easy_trv` (Settings > Devices & services > Easy TRV > three dots > Enable debug logging), every operation produces one line when its result is known, for example `Living room: climate.trv_1 external_temperature attr 0x600D raw=2140 -> ok`: room, thermostat, capability, attribute, raw value written and the result (`ok`, `mismatch expected [...] got ...`, `failed: ...`, `signal failsafe`). An operation that is not sent at all (firmware too old, for instance) is logged as `... not sent -> unsupported ...`.

### Diagnostics

**Settings > Devices & services > Easy TRV > Download diagnostics** contains the options, the forced presence of the house (`house.option`, `house.forced_at`), the loaded profiles and skipped profile files, and for every room its configuration, the resolved settings (what the room runs with after the three layers), its full state (including timestamps and attempt counters), the holds (window and outdoor), the heating state with its reasons, the time of its last change, the deadline of a running open-window delay, the silent window contacts and the thermostats that deviate, whether it reads home (`presence_home`) and when away will count (`away_from`), the notes of thermostats on a schedule or in a window phase, and the latest observations per thermostat (kind, capability, raw value, time). It includes the thermostats' IEEE addresses and entity ids, so look through it before posting it publicly.
