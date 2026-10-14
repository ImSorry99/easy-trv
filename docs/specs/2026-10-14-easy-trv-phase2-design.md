# Easy TRV Phase 2 — Design

Derived from the owner's decisions of 2026-10-13/14 (see `docs/reviews/` and the planning notes outside the repo) and from the Phase 1 design in `2026-10-12-easy-trv-design.md`, which stays valid wherever this document is silent. Device facts: `docs/profiles/sonoff-trvzb-native.md`, `docs/research/02-trvzb-zha-profil.md`. Profile vocabulary: `docs/concept/profiles.md`.

Phase 2 turns Easy TRV from "feeds the external temperature" into "the room's thermostat without a regulator": one thin `climate` entity per room, every setting global with a per-room override, the adaptive mode as a setting, window contacts and an outdoor cut-off that switch the room off and back on, clearer names and texts. It ships as three plans that each land on `main` on their own:

| Plan | Scope | Depends on |
|---|---|---|
| **2a** | settings model (global + override), hysteresis always written, adaptive mode, frost protection temperature, device naming, texts, Repairs wording, migration | — |
| **2b** | room `climate` entity: setpoint, mode heat/off, preset frost, setpoint range, device observation and echo filtering | 2a |
| **2c** | window contacts (off + restore, stuck-contact detection), outdoor cut-off | 2b |

## 1. Principles carried over and changed

P1 (feed, never regulate), P2/P2a (native first, profiles are data), P4 (every write verified), P5 (lean) and B1–B4 stay as they are.

**P3 is amended** ("minimal entities, no climate entity"): *no regulating climate entity and no entity per setting; one thin pass-through `climate` entity per room is Home Assistant's native representation of the room setpoint and is allowed.* The entity never computes a temperature of its own; it stores what a person asked for and feeds it to the thermostats.

**T6 is amended** ("writes independent of HVAC mode"): writes are independent of the *thermostat's* mode, but while the *room* is `off` Easy TRV sends no external-temperature writes (the thermostat is off, the fail-safe is irrelevant, and a `0x600D` write is suspected of waking an `off` TRVZB — spike S1). Switching the room back to `heat` resyncs source and value.

The requirement changes go into `REQUIREMENTS.md` as the first task of plan 2a (sections P3, T6, F3, a new F6/F7 for the room entity and the contacts, and the roadmap list).

## 2. Settings model (plan 2a)

### 2.1 Three layers

| Layer | Where | Who edits |
|---|---|---|
| Built-in defaults | `const.py` `DEFAULT_*` | nobody |
| Global | `entry.options` (options flow) | user, once |
| Room override | `subentry.data` | user, per room, per setting |

Every setting exists in all three layers. The room logic receives one resolved `RoomSettings` object and does not know whether a value came from the room or from the global layer. Resolution lives in a pure function `resolve_settings(defaults, options, subentry_data) -> RoomSettings` in `core/settings.py`, tested on its own.

Settings and their types (built-in default in brackets):

| Key | Type | Default | Used by |
|---|---|---|---|
| `refresh_minutes` | int 1–120 | 20 | T2 |
| `verify_delay_seconds` | int 2–60 | 10 | B1 |
| `max_attempts` | int 1–10 | 3 | B2 |
| `min_delta_kelvin` | float 0.0–1.0 | 0.1 | T1 |
| `sensor_timeout_minutes` | int 0–1440 (0 = off) | 120 | T4 |
| `window_detection` | `on` / `off` / `leave` | `on` | 0x6000 |
| `hysteresis` | `0_2` … `1_0` (no `leave`) | `0_4` | 0x6011, only while adaptive is not `on` |
| `adaptive_mode` | `on` / `off` / `leave` | `on` | 0x6017 (FW ≥ 1.4.4) |
| `frost_temperature` | float 4.0–16.0 step 0.5 | 7.0 | 0x6002 and preset `frost` (2b) |
| `setpoint_min` / `setpoint_max` | float 4.0–35.0 step 0.5, min < max | 5.0 / 30.0 | 2b |
| `profile` | `auto` / profile id | `auto` | — |
| `window_warn_minutes` | int 0–1440 (0 = off) | 120 | 2c |
| `window_check_minutes` | int 0–240 (0 = off) | 30 | 2c stuck-contact check |
| `window_check_drop_kelvin` | float 0.1–3.0 | 0.3 | 2c |
| `outdoor_cutoff_celsius` | float −10.0–30.0, or empty = off | empty | 2c |

Global-only (no room override, because they belong to the whole installation): `outdoor_sensor` (entity id of the outdoor temperature sensor, optional).

### 2.2 Forms

**Options flow** (global): one page, grouped with `section()` into "Writes", "Thermostat settings", "Room thermostat", "Windows and outdoor". Same keys as above.

**Room flow** (add and reconfigure): the existing two steps (source, entities) plus a third step "Settings". Each select gets the first option `global`, labelled with the current global value through `description_placeholders` ("Global (currently 0.4 K)"). Each numeric field is `vol.Optional` without default; an empty field means global, and the form description says so. A stored room value of `"global"` (selects) or absence of the key (numbers) means *use global*. Validation: a room `setpoint_min` must be below the resolved `setpoint_max` and vice versa.

The existing room keys `window_detection` and `hysteresis` keep their names. `hysteresis` loses the option `leave`.

### 2.3 Migration

Config entry `VERSION` goes from 1 to 2 with `async_migrate_entry`:

- rooms with `hysteresis: "leave"` → `"global"`
- rooms with `window_detection` → unchanged (`on`/`off`/`leave` stay valid room values)
- new keys absent → global
- one Repairs issue `settings_migrated` (severity warning, fixable by dismissing) listing the rooms whose hysteresis changed from "leave" to the global 0.4 K, because this changes what the thermostats do.

## 3. Profile additions (plan 2a and 2b)

New intents, encoders stay as they are except one addition:

| Capability (profile key) | Intent | Attribute | Encoder | Verify | Firmware |
|---|---|---|---|---|---|
| `adaptive_mode` | `set_adaptive_mode(enabled)` | 0xFC11 / 0x6017 | `map` `true: 2, false: 0` | `one_of` `true: [2]`, `false: [0, 1]` | ≥ 1.4.4 |
| `frost_protection` | `set_frost_temperature(celsius)` | 0xFC11 / 0x6002 | `temperature_centi` | `equals` | all |
| `setpoint` (2b) | `set_setpoint(celsius)` | 0x0201 / 0x0012 | `temperature_centi` with `step: 0.5` | `equals` | all |
| `hvac_mode` (2b) | `set_hvac_mode(mode)` | 0x0201 / 0x001C | `map` `heat: 4, off: 0` | `one_of` `heat: [4]`, `off: [0]`; `auto` (1) is a *signal* `schedule` | all |

`0x6017` reads back `1` as a read-only alias of "off" (Z2M PR #11466), hence `false: [0, 1]`.

Schema additions: `write.step` (round the intent value to this step before encoding), `observe: true` on a capability (2b: the adapter subscribes to reports of this attribute), `signals` extended to the standard cluster. The built-in profile declares all of them; `tested_firmware` stays `["1.3.0", "1.4.4"]` until the owner's hardware run confirms.

**Hysteresis rule.** The room logic applies `set_hysteresis` only when the resolved `adaptive_mode` is not `on`. With `adaptive_mode: leave` the hysteresis is written regardless (the device ignores it while adaptive is active, keeps it, and uses it once adaptive is switched off).

**Settings order per thermostat** (resync and start): window detection, adaptive mode, hysteresis (conditional), frost temperature, then source and value (Phase 1), then (2b) hvac mode and setpoint.

## 4. Naming and texts (plan 2a)

- Device: `DeviceInfo(translation_key="room", translation_placeholders={"room": <room name>}, suggested_area=<area name when the room came from an area>)`. `de`: "Heizung {room}", `en`: "{room} heating". Rooms created before 2a keep a user-renamed device name (HA keeps `name_by_user`); otherwise the name updates on the next load.
- Status sensor: unchanged key `room_status`; friendly name becomes "Heizung Wohnzimmer Status".
- Window detection: field label "Fenster-offen-Erkennung des Thermostats" / "Thermostat's open-window detection"; description explains the 1.5 K / 4.5 min trigger, the 30 min maximum and that it has nothing to do with window contacts in Home Assistant; options "Erkennung einschalten / ausschalten / im Thermostat unverändert lassen".
- Adaptive mode: field label "Adaptiver Modus (ab Firmware 1.4.4)"; description says the hysteresis is ignored while it is on.
- Repairs issues name the setting with the same label the form uses and the thermostat with its registry name. Implementation: `issues.py` gets `setting_label(hass, capability)` which resolves through `homeassistant.helpers.translation.async_get_translations(hass, hass.config.language, "config_subentries", {DOMAIN})` with a mapping capability → form key (`window_detection_flag` → `window_detection`, `hysteresis` → `hysteresis`, `adaptive_mode` → `adaptive_mode`, `frost_protection` → `frost_temperature`, `external_temperature` → the literal "Raumtemperatur"/"room temperature" from a `selector.capability.options` block in `strings.json` (a plain selector block, which hassfest accepts), `source_select` → "Temperaturquelle"/"temperature source", `setpoint` → "Solltemperatur"/"target temperature", `hvac_mode` → "Modus"/"mode"); falls back to the capability key. The thermostat name comes from `entity_registry` → `device_registry` (`name_by_user` or `name`), falling back to the entity id.

## 5. Room thermostat (plan 2b)

### 5.1 Entity

One `ClimateEntity` per room, attached to the room device, created only when the resolved profile has the capabilities `setpoint` and `hvac_mode`.

| Property | Value |
|---|---|
| `hvac_modes` | `[HEAT, OFF]` |
| `preset_modes` | `["none", "frost"]` |
| `supported_features` | `TARGET_TEMPERATURE | TURN_ON | TURN_OFF | PRESET_MODE` |
| `min_temp` / `max_temp` / `target_temperature_step` | resolved `setpoint_min` / `setpoint_max` / 0.5 |
| `current_temperature` | the value last sent to the thermostats (`state.last_written`), else the active sensor's value, else `None` |
| `target_temperature` | the room setpoint |
| `hvac_action` | not provided (TRVZB `running_state` is unreliable, wrong in adaptive mode) |
| extra attributes | `setpoint_source` (`ha`, `device:<entity_id>`, `restore`, `frost`), `in_sync` (all thermostats verified at the room setpoint and mode), `thermostats` (list), `window_open` (2c), `outdoor_cutoff` (2c), `note` (`None`, `window_phase`, `schedule`) |

The entity is a `RestoreEntity`: setpoint, mode and preset survive restarts.

No `hvac_action`, no humidity, no fan. The ZHA climate entities of the single thermostats stay; the README recommends hiding them, Easy TRV never touches the entity registry (P5).

### 5.2 Ownership

The room owns setpoint and mode. Rules, in order:

1. **HA writes** (`set_temperature`, `set_hvac_mode`, `set_preset_mode`) set the room value, clamp the setpoint into `[setpoint_min, setpoint_max]` and to the 0.5 step, and are written to every writable thermostat with the Phase 1 pipeline (write → verify after `verify_delay` → retry with backoff → Repairs).
2. **Knob turns** on one thermostat are adopted: the reported value becomes the room setpoint (clamped; a clamped value is written back to that thermostat too) and is written to the other thermostats. A reported `system_mode` change (heat ↔ off at the device) is adopted the same way. A knob turn while the room is `off` sets the room to `heat` (the person wants heat). Reports are debounced 3 s per thermostat so that turning through 20.5 → 21 → 21.5 produces one write.
3. **Echoes** are never adopted: each thermostat keeps the set of raw values Easy TRV wrote to it in the last 120 s (`written_recently`, never cleared on timeout); a report whose raw value is in that set is an echo. The adapter also tags its own in-flight writes and verify reads so they never surface as observations.
4. **Device-driven values** are shown, not adopted and not overwritten:
   - `system_mode = auto` (1) reported → `note: schedule`, this thermostat is excluded from setpoint/mode writes until it reports heat or off again, `in_sync = false`. Easy TRV never writes `auto` in Phase 2.
   - a reported setpoint of exactly 12.0 °C while the room setpoint is not 12.0 and the thermostat's window detection is enabled → `note: window_phase` for that thermostat; nothing is written until the next report differs from 12.0 or 30 min pass (then the room value is written). Spike S5 decides whether this branch is ever taken; the design does not depend on it.
5. **Preset `frost`** sets the room setpoint to the resolved `frost_temperature` and remembers the previous setpoint; preset `none` restores it. Mode `off` writes `system_mode = off` to every thermostat; the thermostat's own frost protection (0x6002, written in 2a) protects the radiator. While the room is `off`, no external-temperature writes are sent (T6 amended); returning to `heat` resyncs source, value, mode and setpoint.
6. **Restart:** the restored room setpoint and mode win; thermostats that report a different value after start get it written. A thermostat that reports a *change* after the first resync is a knob turn (rule 2).
7. **Verify mismatch** on an observed capability (`setpoint`, `hvac_mode`) with a value that is not in `written_recently` is an *observation* (someone turned the knob between write and read-back): adopt it (rule 2) instead of retrying. Only mismatches that repeat our own value count as failures.

### 5.3 Adapter: observation

`TrvGateway` gets `subscribe(ieee, endpoint, cluster, attribute, callback) -> unsubscribe`. The ZHA implementation listens on the zigpy cluster events `attribute_report`, `attribute_written`, `attribute_read` (the same events ZHA's own climate entity uses) and delivers `(kind, raw, at)`; writes and reads issued by Easy TRV itself are filtered in the adapter through an in-flight set, so the room logic only sees foreign writes (`written`, e.g. an automation on the ZHA entity) and device reports (`report`). The `FakeGateway` in the tests can inject all three kinds.

A new event `TrvObserved(trv, capability, raw, kind)` enters `RoomLogic.handle`; decoding raw → value uses the profile's encoder in reverse (`decode(capability, raw)`, added to the engine for `temperature_centi` and `map`).

### 5.4 Write volume

Event-driven only: one write plus one verify read per thermostat per human change, plus the Phase 1 refresh. No periodic re-assert of setpoint or mode. Battery impact: unchanged for a quiet room.

## 6. Window contacts and outdoor cut-off (plan 2c)

### 6.1 Window contacts

Room setting `window_contacts`: zero or more `binary_sensor` entities (device classes `window`, `door`, `opening`, `garage_door`), any entity from any area (a window of the adjoining kitchen may belong to the living room). The resolved state is `open` when any contact is `on`, `closed` when all are `off`; `unavailable` or `unknown` contacts count as closed and set `in_sync = false` with a debug log line.

Behaviour, room level, all thermostats:

- **Open:** remember the room's mode and setpoint, switch the room to an internal *hold* (`window_open = true`), write `system_mode = off` to every thermostat (device frost protection applies). The climate entity keeps showing the remembered mode and setpoint; HA changes during the hold are stored and applied at close.
- **Closed:** write the remembered mode (and the setpoint when `heat`) again, `window_open = false`.
- **No time limit.** After `window_warn_minutes` (default 120, 0 = off) a Repairs issue `window_open_long` (severity error) says "window open for N minutes in <room>"; it clears when the window closes.
- **Stuck-contact check:** after `window_check_minutes` (default 30, 0 = off) of hold, if the room's active sensor has not fallen by at least `window_check_drop_kelvin` (default 0.3) relative to its value at opening, a Repairs issue `window_contact_suspect` (severity warning) says the contact reports open but the room is not cooling. The hold continues; the person decides. The two time and drop values are settings (global with room override); a later version may make them dynamic.
- Home Assistant down during a hold: the thermostats stay `off` with their frost protection. Documented in the README as a known trade-off of "off + restore" (the owner's choice over the TRVZB timer mode).
- The device's own open-window detection (0x6000) is independent and stays as configured.

### 6.2 Outdoor cut-off

Global `outdoor_sensor` plus per-room `outdoor_cutoff_celsius` (empty = off). When the outdoor temperature is at or above the threshold, the room enters the same hold as a window (`outdoor_cutoff = true`, thermostats `off`); it leaves the hold when the outdoor temperature falls 0.5 K below the threshold (fixed deadband, no averaging — "the plain cut-off"; damping is a roadmap item). Window hold and outdoor hold are independent flags; the thermostats are `off` while either is set and are restored when both are clear. An unavailable outdoor sensor ends the outdoor hold and logs a warning.

## 7. Status sensor and diagnostics

`RoomStatus` stays `ok` / `degraded` / `error`. New attributes on the status sensor: `window_open`, `outdoor_cutoff`, `setpoint_source`, `in_sync`, `schedule_thermostats` (those in `auto`). Diagnostics include the resolved settings per room, the hold states and the last ten observations per thermostat (kind, capability, raw, timestamp).

## 8. Errors and visibility

Phase 1 issues stay. New issues: `settings_migrated` (2a), `window_open_long`, `window_contact_suspect` (2c). Firmware below a capability's minimum (adaptive mode on FW < 1.4.4) keeps producing the Phase 1 `trv_firmware` issue once per thermostat; the room runs on.

Events: `easy_trv_setpoint_adopted` (room, trv, value) when a knob turn is taken over, `easy_trv_window_hold` (room, open/closed), `easy_trv_outdoor_hold`.

## 9. Tests

- `core/settings.py`: resolution matrix (default / global / room, every key, invalid combinations).
- `core/room_logic.py`: ownership rules 1–7 as state-machine tests; hysteresis gated by adaptive; hold enter/leave for window and outdoor with restore; stuck-contact check; restart reconciliation; echo filtering with late reports.
- `profiles/`: new capabilities validate, encode, decode and verify; `write.step`; schema rejects `hysteresis` without firmware minimum.
- `adapters/`: `subscribe` on the fake gateway; in-flight filtering on the ZHA gateway with a mocked cluster.
- `climate.py`: entity properties, service calls, restore.
- `config_flow.py`: options sections, room settings step with `global`, numeric empty = global, range validation, migration v1 → v2.
- Translations: every new key in `de` and `en`, hassfest key rules.

Coverage stays at the Phase 1 level (≥ 95 % for `core/` and `profiles/`).

## 10. Hardware spikes before and during 2b

| Spike | Question | Consequence if "no" |
|---|---|---|
| S1 | does a `0x600D` write wake an `off` TRVZB? | if yes: T6 amendment stands (no writes while room off); if no: writes could resume while off — keep the amendment anyway, it is simpler |
| S5 | does the thermostat report `0x0012 = 1200` during its open-window phase? | rule 5.2-4b is never taken; delete it in 2c |
| S8 | does every knob step arrive as a report (ZHA min reporting 30 s)? | the 3 s debounce is replaced by "adopt the last report" |
| S9 | does a setpoint write while `system_mode = auto` switch the thermostat to heat? | affects only thermostats a user put into auto; the exclusion rule 5.2-4a stays |

## 11. Repository process (plan 2a, small)

- `CHANGELOG.md` in Keep-a-Changelog format, `Unreleased` section filled by every plan; the Phase 1 state is recorded as `0.1.0`.
- Releases are git tags `vX.Y.Z` matching `manifest.json` `version`; a GitHub Actions workflow `release.yml` runs on a tag, checks the version match, and creates the GitHub Release with the changelog section as notes (HACS reads releases). Tagging stays the owner's manual step.
- The README gets, after Phase 2, a rework on the TODO list: why this integration exists (simplicity, robustness, reuse of the thermostat's native functions instead of a regulator in HA), kept short with collapsible sections and separate files for details.

## 12. Out of scope for Phase 2

Native weekly schedules (and a check that no schedule leads a life of its own on the device — TODO list), presets beyond `frost`, night setback, presence, per-thermostat profiles or contacts, hiding the ZHA entities, damped outdoor cut-off, Zigbee2MQTT, other thermostat models.
