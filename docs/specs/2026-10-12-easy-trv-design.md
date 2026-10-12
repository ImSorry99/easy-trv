# Easy TRV — Design Specification (Phase 1)

| | |
|---|---|
| Status | Approved design, 2026-10-12 (brainstorming with Oliver) |
| Domain | `easy_trv` |
| Scope | Phase 1: feed Sonoff TRVZB (ZHA) with an external room temperature, apply a small device profile, verify every write, surface failures |
| Research | `docs/research/01-bestehende-loesungen.md`, `02-trvzb-zha-profil.md`, `03-ha-integrations-architektur.md` |
| Planning source of truth | Obsidian vault `10 Projekte/Easy TRV/` (wishes, roadmap, decisions). This file is the technical contract. |

## 1. Purpose and non-goals

Easy TRV is a Home Assistant custom integration for people who own Sonoff TRVZB radiator thermostats connected through ZHA and want them to regulate on a *room* temperature instead of the sensor inside the valve body. The TRVZB has a native input for exactly that (attribute `external_temperature_sensor_value`). Easy TRV groups thermostats and sensors into rooms, keeps that input fed, and tells you when it could not.

It deliberately does **not**:

- regulate (no PID, no setpoint shifting, no valve-percentage writes, no hysteresis in HA). The TRVZB firmware regulates.
- create a `climate` entity, presets, schedules, or any per-room `number` entities.
- handle window contacts in Phase 1. The TRVZB has **no** Zigbee input for an external window state; Phase 1 only enables the device's built-in open-window detection.
- handle presence, outdoor temperature cut-off, battery warnings (HA does that), or the adaptive mode switch (user sets it in ZHA).

Officially supported hardware: Sonoff TRVZB (Gen 1, `SONOFF`/`TRVZB`) with firmware ≥ 1.2.1 on ZHA. Other thermostats and Zigbee2MQTT are out of scope.

Success for Phase 1: Oliver's four TRVs (3 × living room, 1 × bedroom) regulate on the room hygrometers, a silent fallback of the TRV to its internal sensor is detected and corrected, and a thermostat that stops accepting writes shows up as a Repairs issue instead of a cold room.

## 2. Environment and device facts that drive the design

Verified in `docs/research/02-trvzb-zha-profil.md` (quirk source, Zigbee2MQTT converter, zigpy, zha, HA core read on 2026-10-12).

| Fact | Consequence |
|---|---|
| Manufacturer cluster `0xFC11` on endpoint 1; attributes are written **without** a manufacturer code (`manufacturer_code=None` in the quirk, same in Sonoff's own script and Z2M). | Adapter never passes a manufacturer code. |
| `0x600D external_temperature_sensor_value`: `int16s`, 0.01 °C, valid 0–9990 (0.0–99.9 °C); out-of-range values are silently ignored by the device. | Values are clamped/validated before writing; negative values are never written. |
| `0x600E external_temperature_sensor_enable`: written as 0 (internal) / 1 (external); the device itself reports 2 (external source offline = fail-safe) and 3 (external restored). | Verification of "external" accepts {1, 3}; reading 2 is the fail-safe signal and triggers a re-enable. |
| The TRV falls back to its internal sensor after roughly 2 h without a new write (reported independently by the Z2M converter author and HA community; not documented by Sonoff). | A heartbeat write is mandatory even when the room temperature does not change. Default 20 min. |
| The ZHA quirk exposes `0x600E` as a `switch`; `is_on = bool(value)`, so 2 and 3 both show as "on". | The fail-safe is invisible in HA without a raw read. Easy TRV reads the raw attribute after every write. |
| With the external source active the TRV reports the external value as `local_temperature`; `climate.current_temperature` therefore shows the fed value. | Useful for the user; useless as a verification source (it is what we wrote). |
| `0x6000 open_window` is the on/off flag of the **device's own** detection (drop > 1.5 K within 4.5 min → valve closed, target 12 °C, ends after rise, manual change or 30 min). | Phase 1 "window handling" = write this flag once per room profile. |
| `0x6011 temperature_control_accuracy`: `int16s`, −100…−20 (−1.0…−0.2 K), default −100; firmware ≥ 1.3.0. | Optional profile value "hysteresis". |
| Firmware gates: 0x600D/0x600E ≥ 1.2.1, 0x6011 ≥ 1.3.0, 0x6014–0x6016 ≥ 1.4.0, 0x6017 ≥ 1.4.4. ZHA stores the OTA `current_file_version` as device `sw_version` (e.g. `0x00001404` = 1.4.4). | Writes are gated by parsed `sw_version`; an `UNSUPPORTED_ATTRIBUTE` response raises a "firmware too old" issue. |
| The TRVZB is a sleepy end device: writes are queued at the parent and delivered on the next poll (seconds); reads may time out while it sleeps. | Verify delay default 10 s; write timeout 20 s; retries with backoff; one serial writer per room. |
| A write to 0x600D may wake a TRV that was switched off from HA (single forum report, unverified). | Option `pause_when_off` (default on) suspends writes to a TRV whose `hvac_mode` is `off`; spike S1 decides whether the default stays. |
| The quirk is built into ZHA since HA 2026.3 (zhaquirks 2.3.0 covers all 17 attributes). A second custom quirk for the same model conflicts. | Easy TRV ships **no** quirk and uses the existing `CustomSonoffCluster` by attribute name. |
| ZHA internals (`homeassistant.components.zha.helpers`, the `zha` library) are not a public API; they broke in 2024.8, 2026.6 and 2026.8. | All ZHA access lives in one adapter module with four calls; an import/attribute failure turns into `ConfigEntryNotReady` plus a Repairs issue, never a crash in a callback. |
| HA 2026.8 restricts a device to one config entry and at most one subentry. | One room device per subentry is exactly the allowed shape. |

Minimum Home Assistant version: **2026.8.0** (subentries exist since 2025.3, the built-in quirk since 2026.3, the device-registry rules since 2026.8). Developed and tested against 2026.10.x (Oliver's instance).

## 3. Configuration model

### 3.1 Shape

- **One config entry** (`single_config_entry: true`, title "Easy TRV"). Creating it shows no form; defaults live in options.
- **One config subentry per room**, `subentry_type = "room"`, created from the entry card button "Add room", edited via "Reconfigure", removed via "Delete". A room has a `unique_id` = casefolded name (unique within the entry).
- **Options flow** on the entry for global parameters.
- Any change (options, subentry add/update/remove) triggers one update listener that calls `hass.config_entries.async_schedule_reload(entry_id)`. The subentry reconfigure step therefore uses `async_update_and_abort`, never the reload variant (which raises when update listeners exist).

### 3.2 Room subentry (two steps)

Step `user`:

| Field | Selector | Notes |
|---|---|---|
| `name` | Text, required | Room title |
| `area` | Area selector filtered to areas containing ZHA climate entities, optional | Used only to prefill step 2 (`er.async_entries_for_area`, plus device-inherited areas). Stored for reference. |

Step `entities`:

| Field | Selector | Validation |
|---|---|---|
| `trvs` | Entity, multiple, filter `{integration: zha, domain: climate}` | ≥ 1; each must resolve to a ZHA device with an IEEE (error `not_zha`); duplicates across rooms are rejected (error `trv_in_other_room`) |
| `temp_sensors` | Entity, multiple, **reorder = true**, filter `{domain: sensor, device_class: temperature}` | Order is priority. May be empty: then the room only applies the profile and leaves the source internal. |
| `window_detection` | Select `on` / `off` / `leave` (default `on`) | Writes `0x6000` once at room start |
| `hysteresis` | Select `leave` / `0.2` / `0.4` / `0.6` / `0.8` / `1.0` K (default `leave`) | Writes `0x6011` once at room start; gated to firmware ≥ 1.3.0 |

Reconfigure shows both steps' fields in one form, prefilled from the subentry data.

### 3.3 Global options

| Key | Default | Range | Meaning |
|---|---|---|---|
| `heartbeat_minutes` | 20 | 5–90 | Rewrite the external value at least this often while a sensor is active |
| `verify_delay_seconds` | 10 | 2–60 | Wait between write and read-back |
| `max_attempts` | 3 | 1–10 | Total attempts per logical write before it counts as failed |
| `min_delta_kelvin` | 0.1 | 0.1–1.0 | Minimum change versus the last written value that triggers a write |
| `debounce_seconds` | 30 | 0–300 | Sensor changes are coalesced for this long before writing |
| `sensor_stale_minutes` | 120 | 10–720 | A sensor that has not reported for longer is treated as unusable |
| `pause_when_off` | true | bool | Suspend external-temperature writes to a TRV whose `hvac_mode` is `off` |

Fixed constants (not user-facing): write timeout 20 s, backoff 5 s → 15 s → 45 s (45 s repeated), startup grace: wait for `EVENT_HOMEASSISTANT_STARTED` before the first sync.

### 3.4 Translations

`strings.json` (= `translations/en.json`) and `translations/de.json` cover `config`, `options`, `config_subentries.room` (title, `entry_type`, `initiate_flow.user` = "Add room", `initiate_flow.reconfigure` = "Edit room", steps, errors, aborts), `issues.*`, and `entity.sensor.room_status` with state translations.

## 4. Runtime architecture

```
custom_components/easy_trv/
  __init__.py          setup/unload: gateway, one RoomController per room subentry, runtime_data, reload listener
  config_flow.py       EasyTrvConfigFlow, RoomSubentryFlow, EasyTrvOptionsFlow
  const.py             domain, keys, defaults, attribute ids, firmware gates
  controller.py        RoomController: HA events -> core events; core commands -> adapter/HA; timers; serial writer
  core/
    model.py           frozen dataclasses: RoomConfig, GlobalParams, RoomState, TrvState, events, commands
    room_logic.py      RoomLogic.handle(event, now, state) -> (state, [commands])  — pure, no HA imports
    values.py          rounding/encoding helpers (°C <-> raw int16), firmware parsing, validity checks
  adapters/
    zha_gateway.py     TrvGateway Protocol + ZhaLibraryGateway (the only module importing zha/zigpy/ZHA helpers)
  sensor.py            RoomStatusSensor (one per room)
  diagnostics.py       config entry diagnostics dump
  strings.json, translations/{en,de}.json, icons.json, manifest.json, quality_scale.yaml
```

Principles:

- **No `DataUpdateCoordinator`.** Nothing is polled. Inputs are HA state-change events (`async_track_state_change_event` over the room's sensors and TRV climate entities) and a per-room time tick (`async_track_time_interval`, 60 s) that drives heartbeat, staleness and retry timing.
- **Pure core.** `RoomLogic` receives events (`SensorStateChanged`, `TrvStateChanged`, `Tick`, `DebounceElapsed`, `WriteResult`, `Start`, `Stop`) with `now` as a parameter and returns commands (`WriteAttribute`, `ReadAttributes`, `ScheduleTimer`, `SetStatus`, `RaiseIssue`, `ClearIssue`, `FireEvent`, `Log`). No timers, no awaits, no HA imports → table-driven unit tests.
- **Thin controller.** Translates HA events, executes commands, owns the asyncio side: one `asyncio.Queue` + worker task per room so writes and their verify delays are strictly serial per room. Retry delays are scheduled with `async_call_later`. Everything is registered with `entry.async_on_unload`.
- **One adapter.** `TrvGateway` Protocol: `resolve_ieee(climate_entity_id) -> str`, `is_available(ieee) -> bool`, `write_attribute(target, raw_value)`, `read_attributes(target_cluster, [attribute_ids]) -> dict`. `ZhaLibraryGateway` implements it via `get_zha_gateway(hass).get_device(EUI64)` → `Device.write_zigbee_attribute(...)` / `Device.async_get_cluster(...).read_attributes([...], allow_cache=False)`. Entity → IEEE resolution goes through the entity registry (`platform == "zha"`, `device_id`) and `async_get_zha_device_proxy`. A `FakeGateway` with programmable outcomes is used in tests.
- **State lives in `entry.runtime_data`** (`EasyTrvRuntimeData(gateway, rooms: dict[subentry_id, RoomController])`).
- **Firmware version** is read once per TRV at room start from the device registry `sw_version` (`0xMMNNPP` nibbles → major.minor.patch); unparsable → no gating, rely on the device's `UNSUPPORTED_ATTRIBUTE` answer.

## 5. Room logic

### 5.1 State

```
RoomState
  source: "external" | "internal"           # what we last successfully told the TRVs
  active_sensor: entity_id | None
  last_written_value: float | None          # °C, 1 decimal
  last_value_write: datetime | None         # last *verified* value write
  pending_change: bool                      # a sensor change is waiting for the debounce window
  trvs: dict[entity_id, TrvState]
  profile_applied: bool

TrvState
  ieee: str
  firmware: (major, minor, patch) | None
  hvac_mode: str | None                     # from climate state
  available: bool                           # from climate state (mirrors ZHA device availability)
  health: "ok" | "paused" | "unavailable" | "error"
  attempts: dict[attribute_id, int]
  last_error: str | None
  failsafe_seen_at: datetime | None
```

### 5.2 Sensor selection

A sensor is **usable** when all hold: state not `unknown`/`unavailable`; state parses as float; unit is °C (°F is converted; other units → unusable with one warning); 0.0 ≤ value ≤ 99.9; `now − state.last_reported ≤ sensor_stale_minutes`.

The **active sensor** is the first usable sensor in the configured order. Re-evaluated on every sensor event and every tick.

The value written is the active sensor's value rounded half-up to 0.1 K (`Decimal.quantize`), encoded as `int(value * 100)`.

### 5.3 Transitions

| Trigger | Condition | Commands |
|---|---|---|
| **Start** (room start or reload, after HA started) | — | Apply profile (5.4). Then, only if `temp_sensors` is non-empty: a usable sensor exists → `enable := 1` then `value`; none usable → `enable := 0`. Rooms without configured sensors never touch the source. All verified. |
| **Sensor change** | usable sensor exists, `abs(value − last_written_value) ≥ min_delta` or `last_written_value is None` | mark `pending_change` and emit `ScheduleTimer(debounce_seconds)`; the controller fires a `DebounceElapsed` event after that delay (re-armed on every further change) → `value` write to every writable TRV using the value current at that moment |
| **Heartbeat** (tick) | `source == external` and `now − last_value_write ≥ heartbeat` | `value` write (current active sensor value, even if unchanged) |
| **Source lost** | no usable sensor and `source == external` | `enable := 0` once; `source = internal`; status `degraded`; no further value writes |
| **Source regained** | usable sensor and `source == internal` | `enable := 1` then `value`; `source = external` |
| **Fail-safe detected** | read-back of `0x600E` returns 2 while `source == external` | immediately `enable := 1` then `value`; fire `easy_trv_failsafe_detected`; log info once; `failsafe_seen_at = now` |
| **TRV paused** | `pause_when_off` and TRV `hvac_mode == off` | TRV health `paused`; no external writes to it; profile writes unaffected |
| **TRV resumed** | `hvac_mode` leaves `off` or TRV becomes available again | resync this TRV: `enable` (current source) and, if external, `value` |
| **TRV unavailable** | climate state `unavailable` | health `unavailable`; no writes; room status `degraded`; no Repairs issue (ZHA already shows it) |

"Writable TRV" = available, not paused, firmware supports the attribute.

Every verified write updates `last_value_write` (for value writes) and resets `attempts[attribute]` to 0.

### 5.4 Profile

Applied at room start and after reconfigure, once, verified like any write, independent of the sensor path:

- `window_detection`: `on` → `0x6000 := 1`, `off` → `0x6000 := 0`, `leave` → nothing.
- `hysteresis`: value `k` → `0x6011 := −round(k × 100)`; `leave` → nothing; firmware < 1.3.0 → skip with a "firmware" issue (warning) for that TRV.

The profile is **not** re-asserted periodically. The user may change the device's switches in ZHA at any time; Easy TRV only sets them at start/reconfigure.

## 6. Write pipeline

A logical write for one TRV and one attribute runs as:

1. Preconditions: TRV available (else outcome `skipped: unavailable`), firmware gate passes (else outcome `unsupported`).
2. `write_attribute(target, raw)` inside `asyncio.timeout(20)`.
3. `await sleep(verify_delay_seconds)`.
4. `read_attributes(cluster 0xFC11, [attribute] + [0x600E if attribute == 0x600D])` with `allow_cache=False`, same timeout.
5. Compare raw values:
   - `0x600D`: read == written.
   - `0x600E` expecting external: read ∈ {1, 3}; expecting internal: read == 0.
   - Any read of `0x600E == 2` while expecting external → fail-safe path (5.3), reported as outcome `failsafe`.
   - `0x6000`: read == written. `0x6011`: read == written.
6. Outcome → `WriteResult(ok | mismatch | error(message) | timeout | unsupported | failsafe)` fed back into `RoomLogic`.

Retry policy (in `RoomLogic`): on `mismatch`, `error`, `timeout` increment `attempts[attribute]`; if `< max_attempts` schedule the same logical write after backoff `[5, 15, 45][min(n−1, 2)]` s; otherwise mark TRV `error` and emit `RaiseIssue` + `FireEvent(write_failed)`. `unsupported` never retries; it raises the firmware issue. A newer logical write for the same TRV/attribute supersedes a pending retry (latest value wins).

Isolation: attempts, backoff and health are per TRV. A failing TRV never blocks writes to the other TRVs of the room; the serial worker simply moves on after each outcome.

## 7. Error reporting

| Channel | When | Details |
|---|---|---|
| Repairs issue `trv_unreachable_<ieee>` | TRV reaches `max_attempts` for any attribute | `is_fixable=False`, `severity=ERROR`, `is_persistent=False`, placeholders `room`, `trv`, `attribute`, `attempts`, `error`; text tells the user to check battery, Zigbee range, or re-pair. Deleted on the next verified write to that TRV. |
| Repairs issue `trv_firmware_<ieee>` | `UNSUPPORTED_ATTRIBUTE` or firmware gate fails | `severity=WARNING`, placeholders `trv`, `attribute`, `required`, `installed`. Deleted when the attribute later verifies. |
| Repairs issue `zha_api_incompatible` | adapter import/lookup fails at setup | `severity=ERROR`; setup raises `ConfigEntryNotReady`. Deleted on successful setup. |
| Bus events | `easy_trv_write_failed`, `easy_trv_recovered`, `easy_trv_failsafe_detected` | Payload: `room`, `room_id` (subentry id), `trv_entity_id`, `ieee`, and for failures `attribute`, `attempts`, `error`. For automations/notifications. |
| Room status sensor | always | see §8 |
| Log | once per transition | `warning` on TRV OK→error, `info` on error→OK and on fail-safe detection, `debug` decision log for every evaluation ("sensor X 21.3 °C, delta 0.2 ≥ 0.1 → write") |

No `persistent_notification`. No entity per error.

## 8. Entities and devices

- One **device** per room (`identifiers={(DOMAIN, subentry_id)}`, name = room title, manufacturer "Easy TRV"), registered with `config_subentry_id`.
- One **sensor** per room: `translation_key="room_status"`, `device_class=ENUM`, `options=["ok","degraded","error"]`, `entity_category=DIAGNOSTIC`, `has_entity_name=True`, `unique_id=f"{subentry_id}_status"`, `should_poll=False`.
  - `ok`: every TRV healthy (or paused by user choice) and, if sensors are configured, an external source is active.
  - `degraded`: no usable sensor although sensors are configured, or any TRV unavailable, or a fail-safe was seen within the last heartbeat period.
  - `error`: any TRV in `error`.
  - Attributes, written only when they change: `active_sensor`, `source`, `trv_health` (map entity_id → health), `last_error`. No timestamps in attributes (recorder bloat); they live in diagnostics.
- **Diagnostics** (`diagnostics.py`): entry options, every room's config and full `RoomState` including timestamps and attempt counters. Nothing to redact.
- No other entities. Explicitly no `climate`, `number`, `switch`, `button`.

## 9. Testing strategy

| Layer | Tooling | What |
|---|---|---|
| `core/` | plain pytest, no HA | table-driven cases for sensor selection (priority, stale, unit, range), every transition in §5.3, retry/backoff counting, fail-safe path, pause/resume, profile gating |
| controller | `pytest-homeassistant-custom-component` (pinned to HA 2026.10.x), `FakeGateway`, `freezer` + `async_fire_time_changed` | end-to-end through HA states: set sensor states, advance time, assert gateway calls and their order; verify serial execution; Repairs issues raised/cleared; events fired; status sensor states and attributes |
| config flow | same | create entry, add/reconfigure/remove room subentry, validation errors, options flow, prefill from area |
| device | manual checklist in `docs/testing-on-device.md` | run on Oliver's four TRVs: write/read-back values, fail-safe after 2 h without writes, spikes S1–S3 |

Target: ≥ 95 % coverage on `core/` and `controller.py`; CI runs ruff (check + format), mypy strict for `core/` and `adapters/`, pytest, hassfest and the HACS action.

## 10. Project skeleton and tooling

- Start from `ludeeus/integration_blueprint` (current, HA ≥ 2026.6.4, Python 3.14, Ruff, hassfest + HACS workflows); remove its sample API/coordinator/entities.
- `pyproject.toml` with `uv` dependency groups (`dev`: `pytest-homeassistant-custom-component==<matching 2026.10.x>`, `ruff`, `mypy`). `asyncio_mode = "auto"`.
- `manifest.json`: `domain easy_trv`, `name "Easy TRV"`, `version 0.1.0`, `config_flow true`, `single_config_entry true`, `dependencies ["zha"]`, `integration_type "hub"`, `iot_class "local_push"`, `codeowners ["@ImSorry99"]`, `loggers ["custom_components.easy_trv"]`, `requirements []`.
- `hacs.json`: `name "Easy TRV"`, `homeassistant "2026.8.0"`, `render_readme true`.
- `quality_scale.yaml` tracking Bronze/Silver rules plus Gold `diagnostics`, `entity-category`, `entity-translations`, `repair-issues`, `reconfiguration-flow`, `devices`.
- Repo docs: `README.md` (English, user-facing), `docs/specs/` (this file and successors), `docs/plans/` (implementation plans), `docs/research/`, `docs/adr/` (from the first contested decision on), `docs/testing-on-device.md`.
- Git: `main` branch, conventional commits, GitHub `ImSorry99/easy-trv`, releases tagged `vX.Y.Z` for HACS.

## 11. Spikes to run on real hardware before or during implementation

| Id | Question | Why it matters | Method |
|---|---|---|---|
| S1 | Does a write to `0x600D` switch a TRV that is `off` back on? | Decides whether `pause_when_off` stays default on | Switch one TRV off in HA, write a value via the ZHA device page ("Manage Zigbee device" → cluster 0xFC11), observe `hvac_mode` |
| S2 | Does the TRV *report* `0x600E = 2` on fail-safe, or only answer reads? | If it reports, the controller can react immediately via an attribute listener; otherwise the heartbeat read is the detection | Stop writes for > 2 h, watch ZHA debug log / quirk switch state, then read raw |
| S3 | Does the built-in open-window detection trigger at all while the external source is active and the hygrometer reports slowly? | Validates the Phase 1 window decision; if it never triggers, window contacts move up the roadmap | Open a window with external source active, watch `hvac_action`/display "OP" |
| S4 | Confirm endpoint 1 and the exact raw read-back values for `0x600D` after a write (does the device round?) | Verification compares exact ints | Write 2135 and 2140, read back |

## 12. Decisions (short log)

| # | Decision | Reason |
|---|---|---|
| D1 | Process: Superpowers (brainstorm → spec → plan → TDD); planning notes in the Obsidian vault | Oliver's choice; already in the flow; vault is the planning source per his tool-docs rule |
| D2 | Name `Easy TRV`, domain `easy_trv` | Oliver's suggestion |
| D3 | No climate entity, no regulation | Core of the idea; existing solutions are judged bloated |
| D4 | Windows in Phase 1 = device detection flag only | TRVZB has no window input; sensors move to the roadmap ("if it is not enough, we adapt") |
| D5 | Direct ZHA adapter for write and read-back | Only path that sees the fail-safe state; small surface, encapsulated |
| D6 | Exactly one diagnostic status sensor per room | Visibility without entity clutter |
| D7 | One entry + room subentries | HA-native room UI, global options once |
| D8 | Event-driven + tick, pure core logic | Nothing to poll; testability |
| D9 | `pause_when_off` default on, pending S1 | Avoid waking a TRV the user switched off |
| D10 | ZHA + TRVZB only, officially | Attribute-level integration is device specific by nature |
| D11 | Ship no quirk | Built-in quirk is complete; a second one conflicts |
| D12 | Minimum HA 2026.8.0 | Device-registry rules for one device per subentry |

## 13. Roadmap pointer

The ordered roadmap (window contacts, presence, room setpoint without a climate entity, native weekly schedules, profiles, outdoor cut-off, adaptive-mode switch, learning) lives in the vault note `Easy TRV — Wünsche & TODO`. Each roadmap item gets its own spec in `docs/specs/` before implementation.
