# Easy TRV — Technical design, Phase 1

Derived from `REQUIREMENTS.md` (2026-10-12). Device specifics live in `docs/profiles/sonoff-trvzb-native.md`, the profile concept in `docs/concept/profiles.md`. This document is for whoever implements; Oliver reviews the requirements, not this file.

## 1. Architecture

```
custom_components/easy_trv/
  __init__.py            setup/unload: adapter, profile registry, one RoomController per room subentry, runtime_data, reload listener
  config_flow.py         EasyTrvConfigFlow, RoomSubentryFlow (area or custom), EasyTrvOptionsFlow
  const.py               domain, config keys, defaults, event names, issue ids
  controller.py          RoomController: HA events → core events; core commands → profile/adapter/HA; timers; one serial worker per room
  core/
    model.py             frozen dataclasses: RoomConfig, GlobalParams, RoomState, TrvState, Intent, events, commands
    room_logic.py        RoomLogic.handle(event, now, state) -> (state, [commands]) — pure, no HA imports
    values.py            temperature validity, rounding (Decimal, half-up to 0.1 K), firmware parsing
  profiles/
    schema.py            voluptuous schema for profile YAML (schema version 1), intent/encoder/verify vocabularies
    loader.py            loads builtin/*.yaml and <config>/easy_trv/profiles/*.yaml, validates, builds the registry; invalid user file → Repairs issue
    engine.py            Profile object: match(), firmware gates, plan(intent) -> [Operation] using the encoders
    builtin/sonoff_trvzb_native.yaml
  adapters/
    zha_gateway.py       TrvGateway protocol + ZhaLibraryGateway (only module importing zha/zigpy/ZHA helpers)
  sensor.py              RoomStatusSensor
  diagnostics.py
  strings.json, translations/{en,de}.json, icons.json, manifest.json, quality_scale.yaml
```

Layers and rules:

- **Core** (`core/`) is pure Python. It receives events (`Start`, `Stop`, `SensorStateChanged`, `TrvStateChanged`, `Tick`, `DebounceElapsed`, `OperationResult`) with `now` as a parameter and returns commands (`Execute(intent, trv)`, `ScheduleTimer`, `SetStatus`, `RaiseIssue`, `ClearIssue`, `FireEvent`, `Log`). No timers, no awaits.
- **Profiles** (`profiles/`) are declarative YAML (see `docs/concept/profiles.md`) loaded into `Profile` objects that turn an `Intent` for one thermostat into `Operation`s (`endpoint, cluster, attribute, raw_value, verify: VerifyRule, also_read`). A `VerifyRule` is `Equals(raw)` or `OneOf({…})` plus optional `signals: {raw: SignalName}` (e.g. `{2: "failsafe"}`). Capabilities carry their own `min_firmware`. Encoders and intents are a fixed code-side vocabulary; profiles only combine them. User profiles from `<config>/easy_trv/profiles/` are loaded with `hass.async_add_executor_job` (file I/O) and validated; failures raise Repairs `profile_invalid_<file>`.
- **Adapter** (`adapters/`) executes operations: `resolve(climate_entity_id) -> DeviceRef(ieee, manufacturer, model, firmware)`, `is_available(ieee)`, `write(ieee, op)`, `read(ieee, endpoint, cluster, [attributes]) -> dict`. `ZhaLibraryGateway` uses `homeassistant.components.zha.helpers.get_zha_gateway` / `async_get_zha_device_proxy`, `Device.write_zigbee_attribute`, `Device.async_get_cluster(...).read_attributes(..., allow_cache=False)`. Any import or lookup failure → `ConfigEntryNotReady` + Repairs issue `zha_api_incompatible`. A `FakeGateway` serves the tests.
- **Controller** is thin: translates HA state events, runs one `asyncio.Queue` worker per room (write → wait → read → compare → `OperationResult` back into core), schedules retries and the debounce timer with `async_call_later`, registers everything on `entry.async_on_unload`.
- No `DataUpdateCoordinator`: nothing is polled. Inputs are `async_track_state_change_event` (room sensors + thermostat climate entities) and a 60 s `async_track_time_interval` tick that drives refresh, timeout and retry bookkeeping.
- State lives in `entry.runtime_data` (`EasyTrvRuntimeData(gateway, profiles, rooms: dict[subentry_id, RoomController])`).

## 2. Configuration model

- **Entry**: one, `single_config_entry: true`, title "Easy TRV", created without a form.
- **Room subentry** (`subentry_type = "room"`), two steps:
  1. `kind`: *From area* (AreaSelector filtered to areas with ZHA climate entities) or *Custom room* (name). From area: the room name is the area name and step 2 is prefilled with the area's entities (entity area, or device area when the entity has none). The `area_id` is stored; reconfigure re-prefills from it.
  2. `entities`: `trvs` (EntitySelector, multiple, filter `{integration: zha, domain: climate}`; ≥ 1; each must resolve to a ZHA device matched by a profile, errors `not_zha` / `no_profile`; not already in another room, error `trv_in_other_room`), `temp_sensors` (EntitySelector, multiple, `reorder=True`, filter `{domain: sensor, device_class: temperature}`; thermostat-own temperature entities are excluded from the prefill), `humidity_sensors` (same, `device_class: humidity`; stored only), `profile` (select: `auto` or any loaded profile id matching the chosen thermostats; errors `no_profile`, `profile_mismatch`), `window_detection` (select `on` / `off` / `leave`, default `on`), `hysteresis` (select `leave` / `0.2` … `1.0`, default `leave`).
  - Create: `async_create_entry(title, data, unique_id=casefold(name))`. Reconfigure: `async_update_and_abort` (the entry's update listener reloads; the reload variant raises when listeners exist).
- **Options** (entry): `refresh_minutes` 20 (5–90), `verify_delay_seconds` 10 (2–60), `max_attempts` 3 (1–10), `min_delta_kelvin` 0.1 (0.1–1.0), `sensor_timeout_minutes` 120 (0 = off, 10–720).
- Internal constants: debounce 5 s (coalesce bursts of sensor reports), operation timeout 20 s, backoff `[5, 15, 45]` s, tick 60 s, first sync after `EVENT_HOMEASSISTANT_STARTED`.
- One update listener → `async_schedule_reload` on any options or subentry change.
- `strings.json` = `translations/en.json`; `translations/de.json` translated. Covers config, options, `config_subentries.room` (steps, errors, aborts, `initiate_flow.user` "Add room"), `issues.*`, `entity.sensor.room_status`.

## 3. Room logic

State: `source` (external | internal | none), `active_sensor`, `last_written` (°C), `last_value_write` (ts of last verified value write), `pending_change`, per-thermostat `TrvState(ieee, profile_id, firmware, available, health ∈ {ok, unavailable, unsupported, error}, attempts: dict[attribute, int], last_error, failsafe_seen_at)`, `settings_applied`.

A sensor is **usable** when: state not `unavailable`/`unknown`; parses as float; unit °C (°F converted; anything else unusable, warned once); 0.0 ≤ v ≤ 99.9; and, if `sensor_timeout_minutes > 0`, `now − last_reported ≤ timeout`. Active sensor = first usable in configured order.

| Trigger | Condition | Intents (per writable thermostat) |
|---|---|---|
| Start | — | `ApplySettings` once per thermostat (skip capabilities below firmware → issue `trv_firmware`); then, if `temp_sensors` configured: usable → `SetSource(external)`, `SetRoomTemperature`; none usable → `SetSource(internal)`. No sensors configured → source untouched. |
| Sensor change | usable sensor and (`last_written is None` or `|v − last_written| ≥ min_delta`) | arm debounce; on `DebounceElapsed` → `SetRoomTemperature(current)` |
| Refresh (tick) | `source == external` and `now − last_value_write ≥ refresh_minutes` | `SetRoomTemperature(current)` (same value is fine) |
| Source lost | no usable sensor, `source == external` | `SetSource(internal)` once; status degraded |
| Source regained | usable sensor, `source != external` | `SetSource(external)`, `SetRoomTemperature` |
| Fail-safe signal | read-back of source reports signal `failsafe` while `source == external` | `SetSource(external)`, `SetRoomTemperature`; event `failsafe_detected`; `failsafe_seen_at = now` |
| Thermostat unavailable | climate state `unavailable` | health `unavailable`; skip; status degraded; no issue |
| Thermostat back | available again | resync: `SetSource(current)`, `SetRoomTemperature` if external |

Writable = available and not `unsupported`. The HVAC mode of the thermostat is irrelevant to writing (requirement T6). Every verified value write sets `last_value_write` and clears `attempts[attribute]`.

## 4. Operation pipeline

Per thermostat and operation, executed by the room's serial worker:

1. Preconditions: thermostat available; capability firmware OK (else result `unsupported`, no retry, issue `trv_firmware`).
2. `write(ieee, op)` within 20 s.
3. `sleep(verify_delay_seconds)`.
4. `read(ieee, op.endpoint, op.cluster, [op.attribute] + op.also_read)` within 20 s, cache bypassed. For the TRVZB value write `also_read = [0x600E]`.
5. Apply `op.verify`: `ok`, `mismatch`, or a **signal** (e.g. `failsafe`) that is routed to the core as its own event rather than as a failure.
6. Result → `OperationResult(trv, op, outcome ∈ {ok, mismatch, error(msg), timeout, unsupported, signal(name)})`.

Retry (core): `mismatch` / `error` / `timeout` → `attempts += 1`; `< max_attempts` → `ScheduleTimer(backoff[min(n−1, 2)])` then the same intent again (a newer intent for the same thermostat and attribute supersedes a pending retry); otherwise health `error`, `RaiseIssue(trv_unreachable)`, `FireEvent(write_failed)`. Attempts, backoff and health are per thermostat; the worker continues with the next queued operation regardless of outcome.

## 5. Errors and visibility

| Channel | When | Details |
|---|---|---|
| Repairs `trv_unreachable_<ieee>` | `max_attempts` exhausted | `severity=ERROR`, not fixable, placeholders `room`, `trv`, `attribute`, `attempts`, `error`, actionable text (battery, range, re-pair). Deleted on the next verified operation for that thermostat; then event `recovered`. |
| Repairs `trv_firmware_<ieee>` | capability/profile minimum not met or `UNSUPPORTED_ATTRIBUTE` | `severity=WARNING`, placeholders `trv`, `capability`, `required`, `installed`. Deleted when the capability later verifies. |
| Repairs `zha_api_incompatible` | adapter cannot be constructed | `severity=ERROR`; setup raises `ConfigEntryNotReady`. |
| Repairs `profile_invalid_<file>` | a user profile fails schema validation | `severity=WARNING`, placeholders `file`, `error`; the file is ignored until fixed. Deleted when it validates on the next load. |
| Events | `easy_trv_write_failed`, `easy_trv_recovered`, `easy_trv_failsafe_detected` | payload `room`, `room_id`, `trv_entity_id`, `ieee`, plus `attribute`, `attempts`, `error` for failures |
| Log | once per transition | warning OK→error, info error→OK and fail-safe; debug decision log per evaluation |
| Status sensor | always | one per room device (`identifiers={(DOMAIN, subentry_id)}`): `sensor`, `device_class=ENUM`, options `ok`/`degraded`/`error`, `EntityCategory.DIAGNOSTIC`, `has_entity_name`, `unique_id=f"{subentry_id}_status"`. `ok`: all thermostats ok and (if sensors configured) external active. `degraded`: no usable sensor though configured, any thermostat unavailable/unsupported, or fail-safe within the last refresh period. `error`: any thermostat in error. Attributes, written only on change: `active_sensor`, `source`, `trv_health`, `last_error`. |
| Diagnostics | on download | options, every room config and full `RoomState` including timestamps and attempt counters |

No `persistent_notification`, no entity per error, no entities besides the status sensor.

## 6. Tests and tooling

| Layer | Tooling | Coverage |
|---|---|---|
| core + profiles | plain pytest | sensor selection (priority, timeout, unit, range), every row of §3, retry counting, fail-safe routing, profile schema (valid builtin, invalid user files), profile planning, encoders, firmware gating, value rounding/encoding |
| controller | `pytest-homeassistant-custom-component` pinned to HA 2026.10.x, `FakeGateway`, `freezer` + `async_fire_time_changed` | HA states in → gateway calls out, in order; serial worker; issues raised/cleared; events; status sensor |
| config flow | same | entry, room from area / custom, validation errors, reconfigure, options |
| device | `docs/testing-on-device.md` | manual checklist on Oliver's four thermostats, spikes S1–S4 |

Tooling: start from `ludeeus/integration_blueprint` (trimmed), `pyproject.toml` + `uv`, Ruff, mypy strict for `core/`, `profiles/`, `adapters/`, GitHub Actions (hassfest, HACS action, lint, tests), `hacs.json` with `homeassistant: "2026.8.0"`, `manifest.json` with `dependencies: ["zha"]`, `integration_type: hub`, `iot_class: local_push`, `single_config_entry: true`. Target ≥ 95 % coverage on `core/`, `profiles/`, `controller.py`.

## 7. Decisions

| # | Decision | Reason |
|---|---|---|
| D1 | Superpowers process; planning notes in the vault, technical docs in the repo | Oliver's choice and his tool-docs rule |
| D2 | Name `Easy TRV`, domain `easy_trv` | Oliver |
| D3 | No climate entity, no regulation | Principle P1/P3 |
| D4 | Profile layer between room logic and device; profiles are declarative YAML, shipped read-only, user copies in the config folder | Principles P2/P2a; native first, tested defaults, user-adaptable without touching code |
| D5 | Phase 1 window handling = device detection flag | TRVZB has no window input; contacts move to the roadmap |
| D6 | Direct ZHA adapter for write and read-back | Only path that sees the fail-safe; small surface, encapsulated |
| D7 | One status sensor per room | Visibility without clutter |
| D8 | One entry + room subentries, rooms from areas or custom | HA-native UI, matches how Oliver organises rooms |
| D9 | Writes independent of HVAC mode | Requirement T6; spike S1 watches for side effects |
| D10 | ZHA + TRVZB only, officially | Profiles are device specific by nature |
| D11 | No own quirk | Built-in quirk is complete; a second one conflicts |
| D12 | Minimum HA 2026.8.0 | Device-registry rules for one device per subentry |
