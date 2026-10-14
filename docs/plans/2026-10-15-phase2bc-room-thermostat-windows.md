# Phase 2b + 2c — Room thermostat, window contacts, outdoor cut-off Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One thin `climate` entity per room that owns setpoint, mode (heat/off) and the frost preset and feeds them to every thermostat of the room; knob turns on one thermostat are adopted and spread; window contacts and an outdoor cut-off switch the room off and restore it; clear Repairs for windows open too long or contacts that look stuck.

**Architecture:** The pure core gains two focused modules: `core/setpoint.py` (ownership: HA writes, knob adoption, echo filtering, device-driven notes, restore) and `core/hold.py` (window and outdoor holds, warn and stuck-contact checks). `core/room_logic.py` only dispatches the new events into them and merges the commands. Two new profile capabilities on the standard thermostat cluster (setpoint `0x0012`, system mode `0x001C`) run through the existing write → verify → retry → Repairs pipeline. The ZHA adapter gains `subscribe()` so the controller receives the thermostats' own attribute reports; the profile engine decodes raw values. A new `climate` platform is the room's face in Home Assistant.

**Tech Stack:** Home Assistant 2026.10 (`ClimateEntity`, `RestoreEntity`), zigpy/zha 2.3.x cluster events, voluptuous, pytest-homeassistant-custom-component. Run everything with `UV_CACHE_DIR=.uv-cache uv run …`.

**Spec:** `docs/specs/2026-10-14-easy-trv-phase2-design.md` sections 1, 2.1, 3, 5, 6, 7, 8, 9, 10. Phase 2a (on `main`) is the base: `core/settings.py`, `RoomParams`, `labels.py`, the settings forms.

## Global Constraints

- `custom_components/easy_trv/core/` and `profiles/engine.py`, `profiles/schema.py` import nothing from Home Assistant. mypy strict over `core/`, `profiles/`, `adapters/`; ruff `select = ALL`; every strings key exists in `en.json` and `de.json`; translation keys satisfy `^(?!.+[_-]{2})(?![_-])[a-z0-9-_]+(?<![_-])$`.
- Commits carry no `Co-Authored-By` line (owner rule). Implementers never push.
- Setpoint: step 0.5 °C; device limits come from the profile capability `range` (TRVZB 4.0–35.0); room settings `setpoint_min` 5.0 / `setpoint_max` 30.0 (4.0–35.0, min < max); the effective range is the intersection, the narrowest wins; the frost temperature is clamped into it.
- System mode raw values (`0x001C`): off = 0, auto (weekly schedule) = 1, heat = 4. Easy TRV only ever writes 0 and 4.
- Echo window: a reported value equal to one Easy TRV wrote to that thermostat in the last 120 s is an echo, never an adoption. Knob debounce: 3 s per thermostat.
- Window-phase note: a reported setpoint of exactly 12.0 °C while the room setpoint is not 12.0 and the room's window detection is `on` → note `window_phase`, no setpoint writes to that thermostat until it reports a value ≠ 12.0 or 30 min pass.
- While the room is `off` or in a hold, no external-temperature writes are sent (spec T6 amended). Returning to heat resyncs source, value, mode and setpoint.
- Window defaults: `window_warn_minutes` 120 (0 = off), `window_check_minutes` 30 (0 = off), `window_check_drop_kelvin` 0.3 (0.1–3.0). Outdoor: global `outdoor_sensor` (optional), `outdoor_cutoff_celsius` −10.0–30.0, empty globally = off; leave the hold 0.5 K below the threshold.
- Events: `easy_trv_setpoint_adopted` (room, trv, value), `easy_trv_window_hold` (room, state `open`/`closed`), `easy_trv_outdoor_hold` (room, state `on`/`off`).
- The config entry stays at version 2: every new key absent means global / built-in default.

## Review Focus

1. A thermostat whose knob is turned three times within 3 s must cause exactly one adoption and one write per sibling, with the last value. (Task 6 `test_knob_steps_within_debounce_adopt_the_last_value_once`.)
2. A late echo of our own write arriving after the verify read (e.g. 40 s later) must not be adopted. (Task 6 `test_late_echo_within_120_s_is_not_adopted`.)
3. A window opened while the room is `off` must not turn the room on when it closes. (Task 7 `test_window_closing_restores_off_when_room_was_off`.)
4. Home Assistant restarting during a window hold must come back in the hold (thermostats stay off) when the contact is still open, and restore the remembered mode when it is closed. (Task 9 `test_restore_during_window_hold`.)
5. A room whose profile lacks `setpoint` or `hvac_mode` must still run Phase 2a behaviour and simply get no climate entity. (Task 9 `test_no_climate_entity_without_setpoint_capability`.)

---

### Task 1: Settings for the room thermostat, windows and outdoor

**Files:**
- Modify: `custom_components/easy_trv/core/settings.py`, `custom_components/easy_trv/const.py`
- Test: `tests/core/test_settings.py`, `tests/test_const.py`

**Interfaces:**
- Produces: `RoomSettings` gains `setpoint_min: float`, `setpoint_max: float`, `window_warn_minutes: int`, `window_check_minutes: int`, `window_check_drop_kelvin: float`, `outdoor_cutoff_celsius: float | None`; `DEFAULTS` gains `setpoint_min: 5.0`, `setpoint_max: 30.0`, `window_warn_minutes: 120`, `window_check_minutes: 30`, `window_check_drop_kelvin: 0.3`, `outdoor_cutoff_celsius: None`. New room-only select key `outdoor_cutoff` with values `global` / `off` (room `off` forces `outdoor_cutoff_celsius = None`; a filled room number overrides the global threshold). `resolve_settings` raises `ValueError("setpoint_min must be below setpoint_max")` when the resolved min ≥ max. `const.py` re-exports the new `CONF_*` and `DEFAULT_*` and `CONF_OUTDOOR_SENSOR = "outdoor_sensor"`, `CONF_WINDOW_CONTACTS = "window_contacts"`.

- [ ] **Step 1: Failing tests**

```python
def test_new_defaults():
    s = resolve_settings({}, {})
    assert (s.setpoint_min, s.setpoint_max) == (5.0, 30.0)
    assert (s.window_warn_minutes, s.window_check_minutes, s.window_check_drop_kelvin) == (120, 30, 0.3)
    assert s.outdoor_cutoff_celsius is None

def test_outdoor_cutoff_global_room_number_and_room_off():
    assert resolve_settings({"outdoor_cutoff_celsius": 16.0}, {}).outdoor_cutoff_celsius == 16.0
    assert resolve_settings({"outdoor_cutoff_celsius": 16.0}, {"outdoor_cutoff_celsius": 18.0}).outdoor_cutoff_celsius == 18.0
    assert resolve_settings({"outdoor_cutoff_celsius": 16.0}, {"outdoor_cutoff": "off"}).outdoor_cutoff_celsius is None

def test_setpoint_min_must_be_below_max():
    with pytest.raises(ValueError, match="setpoint_min"):
        resolve_settings({"setpoint_max": 18.0}, {"setpoint_min": 18.0})
```

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/core/test_settings.py -q --no-cov` — Expected: FAIL (unknown fields).
- [ ] **Step 3: Implement** in `core/settings.py` and `const.py`. `outdoor_cutoff` is not in `DEFAULTS` (it is a room-only switch); `overrides()` keeps it when it is `off`.
- [ ] **Step 4: Run** the settings and const tests, then the whole suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): settings for setpoint range, window checks and outdoor cut-off`

---

### Task 2: Profile capabilities setpoint and hvac mode, decoding

**Files:**
- Modify: `custom_components/easy_trv/core/model.py` (intents), `custom_components/easy_trv/profiles/schema.py`, `custom_components/easy_trv/profiles/engine.py`, `custom_components/easy_trv/profiles/builtin/sonoff_trvzb_native.yaml`, `strings.json` + `translations/en.json` + `translations/de.json` (`selector.capability.options`), `docs/concept/profiles.md`, `docs/profiles/sonoff-trvzb-native.md`
- Test: `tests/profiles/test_engine.py`, `tests/profiles/test_schema.py`, `tests/core/test_model.py`

**Interfaces:**
- Produces: intents `SetSetpoint(celsius: float)` (`set_setpoint`) and `SetHvacMode(mode: Literal["heat", "off"])` (`set_hvac_mode`), added to `INTENT_NAMES`, the schema `INTENTS` and the `Intent` union (they are NOT in `SETTING_INTENTS`). Schema: optional `write.step` (positive number; the engine rounds the intent value to it before encoding) and optional capability flag `observe: bool` (default false). Engine: `Profile.decode(capability: str, raw: int) -> float | str | None` (inverse of `temperature_centi` → celsius; inverse of `map` → the key; unknown raw in a map → `None`); `Profile.limits(intent_name: str) -> tuple[float, float, float] | None` (the capability's `range` low, high and `step`, step default 0.5 when absent; `None` when the profile has no capability for the intent or it has no `range`); `Profile.observed() -> tuple[str, ...]` (capability names with `observe: true`); `CapabilitySpec` gains `step: float | None`, `observe: bool`. Built-in YAML:
  ```yaml
  setpoint:
    intent: set_setpoint
    observe: true
    write: { endpoint: 1, cluster: 0x0201, attribute: 0x0012, encode: temperature_centi, range: [4, 35], step: 0.5 }
    verify: { rule: equals }
  hvac_mode:
    intent: set_hvac_mode
    observe: true
    write: { endpoint: 1, cluster: 0x0201, attribute: 0x001C, encode: { map: { heat: 4, off: 0 } } }
    verify: { rule: one_of, heat: [4], off: [0] }
    signals: { 1: schedule }
  ```
  Capability labels (intent-keyed): en "Target temperature" / "Mode", de "Solltemperatur" / "Modus".

- [ ] **Step 1: Failing tests**

```python
def test_setpoint_plans_centi_rounded_to_half_degree(trvzb):
    op = trvzb.plan(SetSetpoint(21.3), (1, 4, 4))
    assert (op.cluster, op.attribute, op.raw_value) == (0x0201, 0x0012, 2150)

def test_hvac_mode_plans_heat_and_off(trvzb):
    assert trvzb.plan(SetHvacMode("heat"), (1, 3, 0)).raw_value == 4
    assert trvzb.plan(SetHvacMode("off"), (1, 3, 0)).raw_value == 0

def test_decode_inverts_the_encoders(trvzb):
    assert trvzb.decode("setpoint", 2150) == 21.5
    assert trvzb.decode("hvac_mode", 0) == "off"
    assert trvzb.decode("hvac_mode", 1) is None

def test_limits_and_observed(trvzb):
    assert trvzb.limits("set_setpoint") == (4.0, 35.0, 0.5)
    assert trvzb.limits("set_hysteresis") == (0.2, 1.0, 0.5)  # range present, default step
    assert set(trvzb.observed()) == {"setpoint", "hvac_mode"}

def test_schema_rejects_non_positive_step(): ...
```
(`round half up`: 21.25 → 21.5, 21.24 → 21.0; use the repo's `round_tenth` style with `Decimal`.)

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/profiles tests/core/test_model.py -q --no-cov` — Expected: FAIL.
- [ ] **Step 3: Implement.** `engine._intent_value` handles the two intents. Docs: capability rows, firmware map (both: all firmware), concept intent list.
- [ ] **Step 4: Run** profiles + core tests, mypy over `profiles/` and `core/`, then the whole suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat(profiles): setpoint and hvac mode capabilities, decoding and limits`

---

### Task 3: Adapter observation

**Files:**
- Modify: `custom_components/easy_trv/adapters/zha_gateway.py`, `tests/fake_gateway.py`
- Test: `tests/adapters/test_zha_gateway.py`

**Interfaces:**
- Produces: on `TrvGateway` and `ZhaLibraryGateway`:
  ```python
  type ObservationKind = Literal["report", "written"]
  type ObservationCallback = Callable[[str, int, ObservationKind, int], None]  # (ieee, attribute, kind, raw)
  def subscribe(self, ieee: str, endpoint: int, cluster: int, attributes: Sequence[int], callback: ObservationCallback) -> Callable[[], None]
  ```
  The ZHA implementation listens on the zigpy cluster the same way ZHA's own climate entity does — read `.venv/lib/python3.14/site-packages/zha/application/platforms/climate/__init__.py` and `zigpy/zcl/__init__.py` to find the event (`attribute_updated` listener or `on_event("attribute_report" | "attribute_written")`); state in the report which mechanism you used. Writes and verify reads issued by Easy TRV itself are filtered out: `write()` and `read()` add `(ieee, cluster, attribute)` to an in-flight set for their duration, and events for an in-flight key are dropped. Device reports always pass (the core filters echoes by value). The gateway is looked up per call as today; a ZHA reload drops subscriptions — `subscribe` returns an unsubscribe callable, and `ZhaUnavailable` is raised when the device is not there.
  `FakeGateway`: `subscribe(...)` records callbacks; `inject(ieee, attribute, raw, kind="report")` calls them; its own `write()` does NOT call callbacks (tests inject echoes explicitly).

- [ ] **Step 1: Failing tests** (mock the zigpy cluster as the existing adapter tests do)

```python
async def test_subscribe_delivers_device_reports(...): ...   # cluster event for 0x0012 raw 2150 -> callback("00:12:...", 0x0012, "report", 2150)
async def test_own_write_is_not_delivered(...): ...          # event raised while write() is in flight -> no callback
async def test_unsubscribe_stops_delivery(...): ...
async def test_subscribe_unknown_device_raises_zha_unavailable(...): ...
```

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/adapters -q --no-cov` — Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** adapters tests, mypy over `adapters/`, whole suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat(adapter): subscribe to thermostat attribute reports`

---

### Task 4: Core model for the room thermostat and holds

**Files:**
- Modify: `custom_components/easy_trv/core/model.py`
- Test: `tests/core/test_model.py`

**Interfaces:**
- Produces:
  ```python
  class RoomMode(StrEnum): HEAT = "heat"; OFF = "off"
  class Preset(StrEnum): NONE = "none"; FROST = "frost"
  class Note(StrEnum): WINDOW_PHASE = "window_phase"; SCHEDULE = "schedule"
  # RoomConfig gains:
  setpoint_range: tuple[float, float, float] | None   # effective (min, max, step); None = no climate (profile lacks the capabilities)
  window_contacts: tuple[str, ...]
  window_warn: timedelta | None; window_check: timedelta | None; window_check_drop: float
  outdoor_cutoff: float | None
  # RoomState gains:
  setpoint: float | None; mode: RoomMode; preset: Preset; frost_return: float | None
  setpoint_source: str | None            # "ha", "device:<entity_id>", "restore", "frost", "initial"
  window_open: bool; window_opened_at: datetime | None; window_open_value: float | None
  outdoor_hold: bool; window_warned: bool; window_suspect: bool
  # TrvState gains:
  reported_setpoint: float | None; reported_mode: str | None; note: Note | None; note_since: datetime | None
  written_recently: Mapping[str, tuple[tuple[float | str, datetime], ...]]   # capability -> (value, at)
  knob_pending: float | str | None       # last observed value awaiting the 3 s debounce, per capability -> use Mapping[str, float | str]
  # events:
  @dataclass(frozen=True) class SetRoomSetpoint: celsius: float
  @dataclass(frozen=True) class SetRoomMode: mode: RoomMode
  @dataclass(frozen=True) class SetRoomPreset: preset: Preset
  @dataclass(frozen=True) class RestoreRoom: setpoint: float | None; mode: RoomMode | None; preset: Preset | None
  @dataclass(frozen=True) class TrvObserved: trv: str; capability: str; value: float | str | None; raw: int; kind: str
  @dataclass(frozen=True) class KnobSettled: trv: str; capability: str
  @dataclass(frozen=True) class WindowChanged: open: bool
  @dataclass(frozen=True) class OutdoorChanged: celsius: float | None
  # commands:
  @dataclass(frozen=True) class ScheduleKnobSettle: trv: str; capability: str; seconds: float
  # Start gains: reported: Mapping[str, tuple[float | None, str | None]]   (trv -> (setpoint, mode) from HA states)
  # Mismatch gains: observed: float | str | None = None   (decoded actual value, set by the controller for observed capabilities)
  ```
  `RoomState.initial(cfg)` sets `mode=HEAT`, `preset=NONE`, everything else empty. The `Event` and `Command` unions include the new classes.

- [ ] **Step 1: Failing tests** — construction and defaults of the new dataclasses; `RoomState.initial` values; the unions contain the new types.
- [ ] **Step 2: Run** `tests/core/test_model.py` — Expected: FAIL.
- [ ] **Step 3: Implement**; existing `RoomConfig(...)` call sites in tests get the new fields through a single helper in `tests/core/test_room_logic.py` (`CFG` gets `setpoint_range=(5.0, 30.0, 0.5)`, no contacts, warn 120 min, check 30 min, drop 0.3, no outdoor cut-off) and the production builder in `__init__.room_config_from_subentry` gets placeholders that Task 9 fills (pass `setpoint_range=None`, empty contacts, settings-derived timings) so the suite stays green.
- [ ] **Step 4: Run** core tests, mypy over `core/`, whole suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): model for the room thermostat, observations and holds`

---

### Task 5: Setpoint ownership — HA writes, restore, off, frost preset

**Files:**
- Create: `custom_components/easy_trv/core/setpoint.py`
- Modify: `custom_components/easy_trv/core/room_logic.py` (dispatch only)
- Test: `tests/core/test_setpoint.py`

**Interfaces:**
- Consumes: Task 4 model.
- Produces (pure functions, each returns `(RoomState, list[Command])`; `room_logic.handle` routes `SetRoomSetpoint`, `SetRoomMode`, `SetRoomPreset`, `RestoreRoom` here and the `Start` reconciliation calls `reconcile_start`):
  ```python
  def clamp(value: float, rng: tuple[float, float, float]) -> float   # into [min, max], rounded to step (half up)
  def on_set_setpoint(cfg, state, now, celsius) -> _Step     # clamp, setpoint_source "ha", preset NONE, Execute(SetSetpoint) to every writable TRV not in note SCHEDULE; record written_recently
  def on_set_mode(cfg, state, now, mode) -> _Step            # OFF: Execute(SetHvacMode("off")) to all writable; stops value writes. HEAT: Execute(SetHvacMode("heat")) + resync source, value, setpoint
  def on_set_preset(cfg, params, state, now, preset) -> _Step  # FROST: frost_return = setpoint, setpoint = clamp(frost_temperature), source "frost"; NONE: setpoint = frost_return
  def on_restore(cfg, state, now, restore) -> _Step          # restored values win; writes only to TRVs whose reported values differ
  def reconcile_start(cfg, state, now, reported) -> _Step    # no restore yet: setpoint = first writable TRV's reported setpoint (config order), source "initial"; mode HEAT unless every reported mode is "off"
  ```
  While `state.mode is OFF` or a hold is active, `room_logic` suppresses `SetRoomTemperature` writes (refresh, debounce, sensor change) and `SetSource` re-issues; it resumes on HEAT.
  A setpoint change while a hold is active only updates `state.setpoint` (no Execute); the hold's restore applies it (Task 7).

- [ ] **Step 1: Failing tests** (`tests/core/test_setpoint.py`, using a started room from the test_room_logic helpers — import them, do not copy)

```python
def test_ha_setpoint_is_clamped_and_written_to_every_thermostat(): ...   # 40.0 with range (5,30,0.5) -> 30.0 to climate.a and climate.b, source "ha"
def test_setpoint_rounds_to_the_step(): ...                               # 21.3 -> 21.5
def test_mode_off_writes_off_and_stops_value_writes(): ...                # Tick after refresh interval -> no SetRoomTemperature
def test_mode_heat_resyncs_source_value_mode_and_setpoint(): ...
def test_frost_preset_remembers_and_restores_the_setpoint(): ...          # 21.0 -> frost 5.0 -> none -> 21.0
def test_frost_temperature_is_clamped_into_the_range(): ...               # frost 4.0, range (5,30) -> 5.0
def test_restore_wins_and_writes_only_differing_thermostats(): ...
def test_initial_setpoint_comes_from_the_first_thermostat(): ...
def test_schedule_thermostat_gets_no_setpoint_writes(): ...               # note SCHEDULE set -> excluded
```

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/core/test_setpoint.py -q --no-cov` — Expected: FAIL.
- [ ] **Step 3: Implement.** `room_logic.py` grows by dispatch lines only.
- [ ] **Step 4: Run** core tests, mypy over `core/`, whole suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat(core): room setpoint and mode owned by the room`

---

### Task 6: Observations — knob adoption, echoes, notes, mismatch as observation

**Files:**
- Modify: `custom_components/easy_trv/core/setpoint.py`, `custom_components/easy_trv/core/room_logic.py` (dispatch of `TrvObserved`, `KnobSettled`, `Mismatch.observed`)
- Test: `tests/core/test_setpoint.py`

**Interfaces:**
- Produces:
  ```python
  def on_observed(cfg, state, now, event: TrvObserved) -> _Step
  def on_knob_settled(cfg, state, now, event: KnobSettled) -> _Step
  def is_echo(trv_state: TrvState, capability: str, value, now) -> bool    # value in written_recently within 120 s
  ```
  Rules (spec 5.2): an echo updates `reported_*` only. `hvac_mode` raw 1 (auto, value `None` + signal schedule) → note SCHEDULE, `in_sync` false, excluded from writes until heat/off is reported. Setpoint exactly 12.0 under the window-phase condition (Global Constraints) → note WINDOW_PHASE for that thermostat, no writes to it until it reports ≠ 12.0 or 30 min pass (checked on Tick; then the room setpoint is written). Any other non-echo value → `knob_pending[capability] = value` and `ScheduleKnobSettle(trv, capability, 3.0)` (re-armed on every step). On `KnobSettled`: adopt — setpoint clamped (if clamped, also written back to that thermostat), `setpoint_source = f"device:{trv}"`, written to the siblings; a mode change heat↔off adopted the same way; a setpoint knob turn while the room is OFF sets the room to HEAT; `FireEvent("easy_trv_setpoint_adopted", {...})`. A `Mismatch` on `setpoint`/`hvac_mode` whose `observed` value is not an echo → treated as `TrvObserved(kind="report")` instead of a retry; a mismatch equal to an echo stays a failure (B1 retry).

- [ ] **Step 1: Failing tests**

```python
def test_knob_turn_is_adopted_and_spread_to_siblings(): ...
def test_knob_steps_within_debounce_adopt_the_last_value_once(): ...      # Review Focus 1
def test_late_echo_within_120_s_is_not_adopted(): ...                    # Review Focus 2
def test_echo_after_120_s_is_a_knob_turn(): ...
def test_knob_value_outside_the_range_is_clamped_and_written_back(): ...
def test_knob_turn_while_off_turns_the_room_on(): ...
def test_auto_mode_marks_schedule_and_excludes_the_thermostat(): ...
def test_twelve_degrees_with_window_detection_is_a_window_phase(): ...
def test_window_phase_ends_after_30_minutes_and_the_room_value_is_written(): ...
def test_mismatch_with_a_foreign_value_is_an_adoption_not_a_retry(): ...
def test_mismatch_with_our_own_value_is_retried(): ...
```

- [ ] **Step 2: Run** — Expected: FAIL. **Step 3: Implement.** **Step 4: Run** core tests, mypy, whole suite — PASS.
- [ ] **Step 5: Commit** `feat(core): adopt knob turns, filter echoes, respect device-driven values`

---

### Task 7: Holds — window contacts and outdoor cut-off

**Files:**
- Create: `custom_components/easy_trv/core/hold.py`
- Modify: `custom_components/easy_trv/core/room_logic.py` (dispatch `WindowChanged`, `OutdoorChanged`; Tick calls `hold.on_tick`)
- Test: `tests/core/test_hold.py`

**Interfaces:**
- Produces:
  ```python
  def held(state: RoomState) -> bool                          # window_open or outdoor_hold
  def on_window(cfg, state, now, open_: bool) -> _Step
  def on_outdoor(cfg, state, now, celsius: float | None) -> _Step
  def on_tick(cfg, state, now) -> _Step                       # warn and stuck-contact checks
  ```
  Entering a hold (first of window/outdoor): every writable thermostat gets `SetHvacMode("off")`; `state.mode`/`setpoint` stay as the room's wish (the climate entity keeps showing them). Leaving the last hold: if `state.mode is HEAT` → `SetHvacMode("heat")`, `SetSetpoint(state.setpoint)`, then source and value resync; if OFF → nothing is written (Review Focus 3). `FireEvent("easy_trv_window_hold" | "easy_trv_outdoor_hold", {"room": …, "state": …})`. Window: `window_opened_at = now`, `window_open_value` = active sensor value at opening. Warn: when `window_warn` is set and `now - window_opened_at ≥ window_warn` → `RaiseIssue("window_open_long", room_id, {"room": name, "minutes": str(int(minutes))})` once; cleared (`ClearIssue`) on close. Stuck check: when `window_check` is set, at the first Tick with `now - window_opened_at ≥ window_check`, if the active sensor value is not at least `window_check_drop` below `window_open_value` → `RaiseIssue("window_contact_suspect", room_id, {"room": name, "minutes": …, "drop": …})` once; cleared on close. Outdoor: hold when `celsius ≥ cfg.outdoor_cutoff`; leave when `celsius < cfg.outdoor_cutoff − 0.5`; `None` (sensor unavailable) leaves the outdoor hold with `Log("warning", …)`; no cut-off configured → ignored.

- [ ] **Step 1: Failing tests**

```python
def test_window_open_turns_every_thermostat_off(): ...
def test_window_close_restores_heat_and_setpoint(): ...
def test_window_closing_restores_off_when_room_was_off(): ...            # Review Focus 3
def test_setpoint_changed_during_hold_is_applied_at_close(): ...
def test_no_value_writes_during_a_hold(): ...
def test_window_open_long_issue_after_warn_minutes_and_cleared_on_close(): ...
def test_suspect_contact_when_the_room_does_not_cool(): ...
def test_no_suspect_issue_when_the_room_cools(): ...
def test_outdoor_hold_with_half_kelvin_deadband(): ...                   # 16.0 threshold: 16.0 -> hold; 15.6 -> still; 15.4 -> leave
def test_window_and_outdoor_holds_are_independent(): ...                 # restore only when both are clear
def test_unavailable_outdoor_sensor_ends_the_outdoor_hold(): ...
```

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/core/test_hold.py -q --no-cov` — Expected: FAIL. **Step 3: Implement.** **Step 4: Run** core tests, mypy, whole suite — PASS.
- [ ] **Step 5: Commit** `feat(core): window and outdoor holds with warn and stuck-contact checks`

---

### Task 8: Controller wiring

**Files:**
- Modify: `custom_components/easy_trv/controller.py`, `custom_components/easy_trv/issues.py`, `strings.json` + `en.json` + `de.json` (issues `window_open_long` severity error, `window_contact_suspect` severity warning; en/de texts naming the room, the minutes, and for the suspect issue the drop in K)
- Test: `tests/test_controller.py`, `tests/test_issues.py`

**Interfaces:**
- Consumes: `TrvGateway.subscribe`, `Profile.decode/observed/limits`, the new events and commands.
- Produces on `RoomController`: `async_set_setpoint(celsius)`, `async_set_mode(mode: RoomMode)`, `async_set_preset(preset: Preset)`, `async_restore(setpoint, mode, preset)`; read-only properties `setpoint`, `mode`, `preset`, `effective_range`, `in_sync`, `window_open`, `outdoor_hold`, `setpoint_source`, `notes` (trv → note), `current_temperature` (last written value, else active sensor value, else `None`); listener registration `add_update_listener(callback) -> unsubscribe` so entities refresh on every state change. On `async_start`: `Start.reported` built from each TRV climate entity's HA state (`attributes["temperature"]`, state `off`/`heat`/other → `None`); `subscribe()` for every observed capability of every thermostat; reports are decoded with `profile.decode` and fed as `TrvObserved`; `Mismatch` on observed capabilities gets `observed=profile.decode(capability, actual)`; `ScheduleKnobSettle` via `async_call_later` (cancel/re-arm per `(trv, capability)`); state tracking of `window_contacts` (any `on` → `WindowChanged(True)`, all `off` → `False`; `unavailable`/`unknown` count as closed with a debug log) and of the global `outdoor_sensor` (`OutdoorChanged(float | None)`); the new issue kinds map to `issues.raise_window_open_long/clear…` and `raise_window_contact_suspect/clear…` (issue ids `window_open_long_<subentry_id>`, `window_contact_suspect_<subentry_id>`). Unload cancels subscriptions and timers.

- [ ] **Step 1: Failing tests** (controller tests with the fake gateway)

```python
async def test_report_from_a_thermostat_is_decoded_and_adopted(...): ...   # inject 0x0012 raw 2200 on climate.a -> after 3 s climate.b gets 2200
async def test_set_setpoint_writes_through_the_pipeline(...): ...
async def test_window_contact_opens_and_closes_the_hold(...): ...         # binary_sensor.w on -> 0x001C=0 on both; off -> 4 and setpoint
async def test_outdoor_sensor_drives_the_outdoor_hold(...): ...
async def test_window_open_long_issue(...): ...                           # advance time past warn minutes
async def test_subscriptions_are_removed_on_unload(...): ...
```

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_controller.py tests/test_issues.py -q --no-cov` — Expected: FAIL. **Step 3: Implement.** **Step 4: Run** whole suite — PASS.
- [ ] **Step 5: Commit** `feat: wire observations, room commands and holds into the controller`

---

### Task 9: Climate platform and setup

**Files:**
- Create: `custom_components/easy_trv/climate.py`
- Modify: `custom_components/easy_trv/const.py` (`PLATFORMS` gains `Platform.CLIMATE`), `custom_components/easy_trv/__init__.py` (`room_config_from_subentry` fills `setpoint_range` from settings ∩ `profile.limits("set_setpoint")` — `None` when the profile lacks `setpoint` or `hvac_mode`; window contacts; timings; outdoor cut-off), `custom_components/easy_trv/sensor.py` (status attributes `window_open`, `outdoor_cutoff`, `setpoint_source`, `in_sync`, `schedule_thermostats`), `strings.json` + translations (`entity.climate.room` with `state_attributes.preset_mode.state` `none`/`frost` → en "None"/"Frost protection", de "Keins"/"Frostschutz"), `custom_components/easy_trv/diagnostics.py` (hold states, notes, last ten observations per thermostat)
- Test: `tests/test_climate.py`, `tests/test_init.py`, `tests/test_sensor.py`, `tests/test_diagnostics.py`

**Interfaces:**
- Produces: `RoomClimate(ClimateEntity, RestoreEntity)` per room with a `setpoint_range`: `_attr_has_entity_name = True`, `_attr_name = None` (the entity carries the device name "Heizung {room}"), unique id `f"{subentry_id}_climate"`, suggested `entity_id = f"climate.{slugify(title)}_heating"`, `hvac_modes = [HEAT, OFF]`, `preset_modes = ["none", "frost"]`, features `TARGET_TEMPERATURE | TURN_ON | TURN_OFF | PRESET_MODE`, `temperature_unit = CELSIUS`, `min_temp`/`max_temp`/`target_temperature_step` from `effective_range`, `current_temperature`, `target_temperature`, `hvac_mode`, `preset_mode`, no `hvac_action`; extra attributes `setpoint_source`, `in_sync`, `thermostats`, `window_open`, `outdoor_cutoff`, `note` (first note of any thermostat or `None`). `async_added_to_hass` restores the last state through `controller.async_restore(...)` before the controller's start reconciles. Services call the controller methods.

- [ ] **Step 1: Failing tests**

```python
async def test_room_climate_entity_properties(...): ...                  # climate.living_heating: heat, 5..30 step 0.5, presets, no hvac_action
async def test_set_temperature_service_writes_the_thermostats(...): ...
async def test_turn_off_and_on(...): ...
async def test_preset_frost_and_back(...): ...
async def test_restore_during_window_hold(...): ...                     # Review Focus 4 (mock_restore_cache)
async def test_no_climate_entity_without_setpoint_capability(...): ...  # Review Focus 5 (user profile without setpoint)
async def test_effective_range_is_the_narrowest(...): ...               # room 5..30, profile 4..35 -> 5..30; room 3..? impossible via form, settings 10..40? -> clamp to profile max 35
async def test_status_sensor_has_the_room_attributes(...): ...
async def test_diagnostics_include_holds_and_observations(...): ...
```

- [ ] **Step 2: Run** the four test files — Expected: FAIL. **Step 3: Implement.** **Step 4: Run** the whole suite with coverage (≥ 95 %), ruff, mypy — PASS.
- [ ] **Step 5: Commit** `feat: room climate entity`

---

### Task 10: Config flows for contacts, range, windows and outdoor

**Files:**
- Modify: `custom_components/easy_trv/config_flow.py` (consider moving the settings-form helpers into `flow_settings.py` if `config_flow.py` would exceed ~800 lines), `strings.json` + translations
- Test: `tests/test_config_flow.py`, `tests/test_translations.py`

**Interfaces:**
- Produces: entities step gains `window_contacts` (`EntitySelector` multiple, domain `binary_sensor`, device classes `window`, `door`, `opening`, `garage_door`, no area filter; prefill from the area's contacts of those classes when created from an area); room settings step gains `setpoint_min`, `setpoint_max` (4.0–35.0 step 0.5), `window_warn_minutes` (0–1440), `window_check_minutes` (0–240), `window_check_drop_kelvin` (0.1–3.0 step 0.1), `outdoor_cutoff` select (`global`/`off`) and `outdoor_cutoff_celsius` (−10.0–30.0 step 0.5); all numbers empty = global; validation error `setpoint_range` when the resolved min ≥ max. Options flow gains sections `room_thermostat` (setpoint min/max) and `windows_outdoor` (warn, check, drop, `outdoor_sensor` `EntitySelector` domain `sensor` device class `temperature`, `outdoor_cutoff_celsius`); empty = not stored. Placeholders `global_<key>`/`default_<key>` as in Phase 2a. de texts: window contacts may come from another area ("z. B. die Küche, wenn sie mit dem Wohnzimmer verbunden ist").

- [ ] **Step 1: Failing tests** — contacts stored and prefilled from the area; any-area contact accepted; range validation; outdoor off per room; options sections stored flat; translations complete.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implement.** **Step 4: Run** whole suite — PASS.
- [ ] **Step 5: Commit** `feat(flow): window contacts, setpoint range, window checks and outdoor cut-off`

---

### Task 11: Docs, changelog, device checklist

**Files:**
- Modify: `README.md` (room thermostat: what it does and does not do, knob behaviour, frost preset, off; window contacts incl. other-area contacts, no time limit, warn and stuck-contact issues, HA-down trade-off; outdoor cut-off with the 0.5 K deadband; recommendation to hide the ZHA climate entities of single thermostats — keep it compact), `CHANGELOG.md` (Unreleased: Added lines), `docs/testing-on-device.md` (steps for the climate entity, knob adoption across three thermostats, window contact open/close, outdoor cut-off via a template sensor; spikes S1, S5, S8, S9 from spec section 10 with what to record), `REQUIREMENTS.md` only if a statement became wrong
- Test: none (docs); run the whole suite once as a guard.

- [ ] **Step 1: Write the docs.** **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest -q` and ruff — PASS.
- [ ] **Step 3: Commit** `docs: room thermostat, window contacts and outdoor cut-off`
