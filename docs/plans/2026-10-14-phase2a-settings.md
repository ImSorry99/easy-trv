# Phase 2a — Settings, naming, texts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every Easy TRV setting gets a global default with a per-room, per-setting override; the hysteresis is always written; adaptive mode and the frost protection temperature become profile capabilities; room devices are named "Heizung {room}" and sit in their area; window-detection texts and Repairs messages use plain names; existing entries migrate; the repo gains a changelog and a release workflow.

**Architecture:** A pure `core/settings.py` resolves built-in defaults, `entry.options` and `subentry.data` into one frozen `RoomSettings`; the room logic only ever sees resolved values. Two new intents (`SetAdaptiveMode`, `SetFrostTemperature`) flow through the unchanged profile engine; the built-in profile declares them. The config flows grow a "Settings" step with a `global` choice per select and empty-means-global numbers. Naming uses HA device translations; Repairs placeholders are resolved from the integration's own translations once per entry load.

**Tech Stack:** Home Assistant 2026.10 (`ConfigSubentryFlow`, `section`, device `translation_key`), voluptuous, pytest-homeassistant-custom-component, ruff, mypy. Run everything with `UV_CACHE_DIR=.uv-cache uv run …`.

**Spec:** `docs/specs/2026-10-14-easy-trv-phase2-design.md` (sections 1–4, 8, 9, 11). Phase 1 design `docs/specs/2026-10-12-easy-trv-design.md` stays valid where the Phase 2 spec is silent.

## Global Constraints

- `custom_components/easy_trv/core/` and `profiles/engine.py`, `profiles/schema.py` import nothing from Home Assistant; `const.py` re-exports core constants, never the other way round.
- mypy strict over `core/`, `profiles/`, `adapters/`; ruff `select = ALL` with the repo's `.ruff.toml`; every translation key must satisfy hassfest's `^(?!.+[_-]{2})(?![_-])[a-z0-9-_]+(?<![_-])$` and every key in `strings.json` exists in `translations/en.json` and `translations/de.json` (`tests/test_translations.py`).
- Setting defaults (spec 2.1): refresh 20 min, verify delay 10 s, max attempts 3, min delta 0.1 K, sensor timeout 120 min (0 = off), window detection `on`, hysteresis `0_4`, adaptive mode `on`, frost temperature 7.0 °C (4.0–16.0, step 0.5), profile `auto`.
- The room value `"global"` (selects) or an absent key (numbers) means "use the global value"; the hysteresis has no `leave` option any more; `window_detection` and `adaptive_mode` keep `on` / `off` / `leave`.
- Settings write order per thermostat: window detection, adaptive mode, hysteresis (only when the resolved adaptive mode is not `on`; a thermostat whose firmware cannot do adaptive mode — `Unsupported` outcome — gets the hysteresis written right then, and on every later settings apply while `adaptive_mode` is in its `unsupported` set), frost temperature, then source and value.
- Device name: translation key `room`, placeholder `room`; `en` "{room} heating", `de` "Heizung {room}". `suggested_area` only for rooms created from an area.
- Commits carry no `Co-Authored-By` line (owner rule). Implementers never push.
- Setpoint range, window contacts and outdoor keys are plans 2b/2c; this plan adds only the settings it uses (ruling recorded in the ledger).

## Review Focus

1. A room stored before this plan with `hysteresis: "leave"` and no `adaptive_mode` key must, after migration, write hysteresis 0.4 K **only if** the global adaptive mode is not `on`; with the default (`on`) the migration must not cause a hysteresis write. (Task 5 test `test_migrated_room_with_default_adaptive_writes_no_hysteresis`.)
2. A thermostat on firmware 1.3.0 in a room with adaptive mode `on` must get the `trv_firmware` issue for `adaptive_mode`, still receive window detection and frost temperature, and — because adaptive cannot be effective there — get the room's hysteresis written as soon as the `Unsupported` outcome for `adaptive_mode` arrives. (Task 3 tests `test_unsupported_adaptive_mode_does_not_block_other_settings`, `test_unsupported_adaptive_mode_triggers_the_hysteresis_write`.)
3. An options flow submission that leaves a number empty must store nothing for that key and fall back to the built-in default, not store `None` or `0`. (Task 6 test `test_options_empty_number_falls_back_to_default`.)
4. Renaming a room (reconfigure) must update the device name while a user-renamed device keeps its `name_by_user`. (Task 7 test `test_device_name_follows_room_name_unless_renamed_by_user`.)
5. A Repairs issue raised before the translations loaded (very early start) must still render: labels fall back to the capability key and the thermostat name to the entity id. (Task 8 test `test_labels_fall_back_to_keys_and_entity_ids`.)

---

### Task 1: Requirements, changelog, release workflow

**Files:**
- Modify: `REQUIREMENTS.md` (P3, T6, F3; new F6 "Room thermostat", F7 "Window contacts and outdoor cut-off" as one-line summaries pointing to the Phase 2 spec; roadmap list)
- Create: `CHANGELOG.md`
- Create: `.github/workflows/release.yml`
- Modify: `docs/README.md` (doc map: Phase 2 spec, plans, changelog)

**Interfaces:**
- Produces: `CHANGELOG.md` with an `## [Unreleased]` section every later task appends to under `### Added` / `### Changed`.

- [ ] **Step 1: Amend `REQUIREMENTS.md`**

P3 becomes: "Minimal entities: one diagnostic status sensor and one thin pass-through `climate` entity per room. No regulating climate entity, no entity per setting." T6 becomes: "Writes are independent of the thermostat's own mode; while a *room* is `off`, no external-temperature writes are sent." F3 becomes: "Per-room settings with a global default and a per-setting override: built-in open-window detection (on / off / leave), hysteresis (0.2–1.0 K, default 0.4, written only while adaptive mode is not on), adaptive mode (on / off / leave, firmware ≥ 1.4.4, default on), frost protection temperature (4–16 °C, default 7)." Add F6 and F7 as one sentence each referencing the spec. In the roadmap paragraph remove "room setpoint without a climate entity", "window contacts", "outdoor cut-off", "adaptive-mode switch" (they are Phase 2) and add "validation that no weekly schedule lives on the device".

- [ ] **Step 2: Create `CHANGELOG.md`** in Keep-a-Changelog format with `## [Unreleased]` (empty `### Added`, `### Changed`) and `## [0.1.0] - 2026-10-13` listing Phase 1 in five bullets (rooms from areas, profiles as YAML, external temperature with refresh and verified writes, fail-safe detection, Repairs and status sensor). Link references at the bottom: `[Unreleased]: https://github.com/ImSorry99/easy-trv/compare/v0.1.0...HEAD`, `[0.1.0]: https://github.com/ImSorry99/easy-trv/releases/tag/v0.1.0`.

- [ ] **Step 3: Create `.github/workflows/release.yml`**

Trigger `on: push: tags: ['v*']`. Jobs: `release` on `ubuntu-latest` with `permissions: contents: write`; steps: checkout; a shell step that reads `custom_components/easy_trv/manifest.json` with `jq -r .version` and fails unless it equals `${GITHUB_REF_NAME#v}`; a shell step that extracts the changelog section for that version with `awk '/^## \[VERSION\]/{flag=1;next}/^## \[/{flag=0}flag' CHANGELOG.md > notes.md` (VERSION substituted) and fails if `notes.md` is empty; `softprops/action-gh-release@v2` with `body_path: notes.md`.

- [ ] **Step 4: Update `docs/README.md`** doc map with the Phase 2 spec, this plan and the changelog.

- [ ] **Step 5: Verify**

Run: `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_translations.py -q --no-cov` (nothing changed, must stay green) and `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml'))"`.
Expected: tests pass, YAML loads.

- [ ] **Step 6: Commit**

```bash
git add REQUIREMENTS.md CHANGELOG.md .github/workflows/release.yml docs/README.md
git commit -m "docs: Phase 2 requirements, changelog, release workflow"
```

---

### Task 2: Settings resolution

**Files:**
- Create: `custom_components/easy_trv/core/settings.py`
- Modify: `custom_components/easy_trv/const.py` (new `CONF_*`, `SETTING_GLOBAL`, `DEFAULT_*` re-exported from `core.settings`)
- Test: `tests/core/test_settings.py`

**Interfaces:**
- Produces:
  ```python
  GLOBAL: Final = "global"
  @dataclass(frozen=True)
  class RoomSettings:
      refresh_minutes: int; verify_delay_seconds: int; max_attempts: int
      min_delta_kelvin: float; sensor_timeout_minutes: int
      window_detection: Literal["on", "off", "leave"]
      hysteresis: float                      # kelvin, 0.2..1.0
      adaptive_mode: Literal["on", "off", "leave"]
      frost_temperature: float               # celsius
      profile: str                           # "auto" or profile id
  DEFAULTS: Final[Mapping[str, Any]]        # key -> built-in default, keys = CONF_* names
  def resolve_settings(options: Mapping[str, Any], room: Mapping[str, Any]) -> RoomSettings
  def hysteresis_kelvin(stored: str | float) -> float   # "0_4" / "0.4" / 0.4 -> 0.4
  def overrides(room: Mapping[str, Any]) -> dict[str, Any]   # the room keys that are not global
  ```
- `const.py` re-exports `SETTING_GLOBAL = GLOBAL`, `CONF_ADAPTIVE_MODE = "adaptive_mode"`, `CONF_FROST_TEMPERATURE = "frost_temperature"`, and `DEFAULT_WINDOW_DETECTION`, `DEFAULT_HYSTERESIS = "0_4"`, `DEFAULT_ADAPTIVE_MODE`, `DEFAULT_FROST_TEMPERATURE` next to the existing `DEFAULT_*`.

- [ ] **Step 1: Write the failing tests**

```python
def test_defaults_when_nothing_is_set():
    s = resolve_settings({}, {})
    assert (s.refresh_minutes, s.verify_delay_seconds, s.max_attempts) == (20, 10, 3)
    assert (s.min_delta_kelvin, s.sensor_timeout_minutes) == (0.1, 120)
    assert (s.window_detection, s.hysteresis, s.adaptive_mode) == ("on", 0.4, "on")
    assert (s.frost_temperature, s.profile) == (7.0, "auto")

def test_global_overrides_default_and_room_overrides_global():
    options = {"hysteresis": "0_6", "refresh_minutes": 5, "adaptive_mode": "off"}
    room = {"hysteresis": "0_2", "refresh_minutes": None, "adaptive_mode": "global"}
    s = resolve_settings(options, room)
    assert (s.hysteresis, s.refresh_minutes, s.adaptive_mode) == (0.2, 5, "off")

def test_legacy_values_are_accepted():
    assert resolve_settings({}, {"hysteresis": "0.4"}).hysteresis == 0.4
    assert resolve_settings({}, {"hysteresis": "leave"}).hysteresis == 0.4  # leave -> global

def test_overrides_lists_only_room_specific_keys():
    assert overrides({"hysteresis": "0_2", "adaptive_mode": "global", "refresh_minutes": None, "trvs": []}) == {"hysteresis": "0_2"}

def test_sensor_timeout_zero_means_off_is_kept_as_zero():
    assert resolve_settings({"sensor_timeout_minutes": 0}, {}).sensor_timeout_minutes == 0
```

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/core/test_settings.py -q --no-cov` — Expected: FAIL, `ModuleNotFoundError: custom_components.easy_trv.core.settings`.

- [ ] **Step 3: Implement `core/settings.py`**

`resolve_settings` walks `DEFAULTS`: value = room value if the key is present in `room` and not `None` and not `GLOBAL` and not `"leave"` for `hysteresis`; else options value if present and not `None`; else default. Hysteresis values pass through `hysteresis_kelvin`; ints are coerced with `int(round(x))`, floats with `float`. Unknown keys in `room` are ignored.

- [ ] **Step 4: Run the tests** — Expected: PASS. Then `UV_CACHE_DIR=.uv-cache uv run mypy custom_components/easy_trv/core` — Expected: no issues.

- [ ] **Step 5: Add the `const.py` names and a `tests/test_const.py` assertion** that `DEFAULT_HYSTERESIS == "0_4"` and `SETTING_GLOBAL == "global"`; run `tests/test_const.py`.

- [ ] **Step 6: Commit**

```bash
git add custom_components/easy_trv/core/settings.py custom_components/easy_trv/const.py tests/core/test_settings.py tests/test_const.py
git commit -m "feat(core): resolve settings from defaults, options and room overrides"
```

---

### Task 3: Core model and room logic

**Files:**
- Modify: `custom_components/easy_trv/core/model.py` (intents, `INTENT_NAMES`, `SETTING_INTENTS`, `RoomConfig`, `GlobalParams` → `RoomParams`)
- Modify: `custom_components/easy_trv/core/room_logic.py` (`_apply_settings`, `_reissuable`, the two `isinstance(..., (SetWindowDetection, SetHysteresis))` checks)
- Modify: every importer of `GlobalParams` (`controller.py`, `__init__.py`, `diagnostics.py`, `const.py` re-export if any, tests) — rename only, via `grep -rl GlobalParams custom_components tests`
- Test: `tests/core/test_model.py`, `tests/core/test_room_logic.py`

**Interfaces:**
- Consumes: `RoomSettings` from Task 2.
- Produces:
  ```python
  @dataclass(frozen=True) class SetAdaptiveMode: enabled: bool      # INTENT_NAMES -> "set_adaptive_mode"
  @dataclass(frozen=True) class SetFrostTemperature: celsius: float # INTENT_NAMES -> "set_frost_temperature"
  SETTING_INTENTS = (SetWindowDetection, SetAdaptiveMode, SetHysteresis, SetFrostTemperature)
  class RoomConfig: ... window_detection: Literal[...]; hysteresis: float; adaptive_mode: Literal["on","off","leave"]; frost_temperature: float
  class RoomParams:  # formerly GlobalParams, same fields
      @classmethod def from_settings(cls, settings: RoomSettings) -> RoomParams
      @classmethod def defaults(cls) -> RoomParams   # kept for tests
  ```
  `RoomParams.from_options` is removed.

- [ ] **Step 1: Write the failing tests** (in `tests/core/test_room_logic.py`, using the existing helpers `started()`, `executes()`, `CFG`)

```python
def test_start_applies_settings_in_order_with_adaptive_on():
    cfg = replace(CFG, adaptive_mode="on", hysteresis=0.4, frost_temperature=7.0)
    st, cmds = RoomLogic(cfg, RoomParams.defaults(), capability_of=capability_of).handle(Start(READINGS, BOTH_AVAILABLE), T0, RoomState.initial(cfg))
    intents = [i for trv, i in executes(cmds) if trv == "climate.a"][:4]
    assert intents == [SetWindowDetection(True), SetAdaptiveMode(True), SetFrostTemperature(7.0), SetSource("external")]

def test_hysteresis_is_written_when_adaptive_is_off_or_leave():
    for mode in ("off", "leave"):
        cfg = replace(CFG, adaptive_mode=mode, hysteresis=0.4)
        ...  # intents for climate.a start with window, (SetAdaptiveMode(False) only for "off"), SetHysteresis(0.4), SetFrostTemperature(7.0)

def test_adaptive_leave_writes_nothing_for_adaptive():
    # no SetAdaptiveMode in the Start commands when adaptive_mode == "leave"

def test_unsupported_adaptive_mode_does_not_block_other_settings():
    # cfg adaptive_mode "on"; Unsupported outcome for capability "adaptive_mode" on climate.a
    # -> health UNSUPPORTED, firmware issue raised, SetFrostTemperature(7.0) for climate.a is still issued/verified

def test_unsupported_adaptive_mode_triggers_the_hysteresis_write():
    # cfg adaptive_mode "on", hysteresis 0.4; the Unsupported outcome for "adaptive_mode" on climate.a
    # produces Execute("climate.a", SetHysteresis(0.4)) in the same handle() call; climate.b gets no hysteresis write

def test_exhausted_frost_write_is_reapplied_on_resync():
    # three Failed on SetFrostTemperature -> settings_applied False -> availability off/on re-issues SetFrostTemperature
```

Replace the `...` with the concrete calls the existing tests use (`started()` builds a room from `CFG`; add `adaptive_mode="leave", frost_temperature=7.0` to `CFG` so the Phase 1 expectations keep their intent lists, then adjust the Phase 1 tests that assert the full Start command list: they now contain `SetFrostTemperature(7.0)` after the hysteresis).

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/core -q --no-cov` — Expected: FAIL on import of `SetAdaptiveMode` / `RoomParams`.

- [ ] **Step 3: Implement** the model changes and the room-logic order. `_apply_settings` and `_reissuable` build the intent list from the config in the constrained order; both `isinstance` checks use `SETTING_INTENTS`. `RoomParams.from_settings` maps `refresh_minutes → timedelta`, `sensor_timeout_minutes 0 → None`.

- [ ] **Step 4: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/core -q --no-cov && UV_CACHE_DIR=.uv-cache uv run mypy custom_components/easy_trv/core` — Expected: PASS, no issues. Then the whole suite: `UV_CACHE_DIR=.uv-cache uv run pytest -q --no-cov` — Expected: failures only in `tests/test_init.py`, `tests/test_room_setup.py`, `tests/test_controller.py`, `tests/test_diagnostics.py` caused by the `GlobalParams` rename and the new `RoomConfig` fields; fix those call sites in this task (rename, add `adaptive_mode="leave", frost_temperature=7.0` in test helpers) until the suite is green.

- [ ] **Step 5: Append to `CHANGELOG.md`** under Unreleased/Added: "Adaptive mode and frost protection temperature as profile capabilities".

- [ ] **Step 6: Commit**

```bash
git add -A custom_components tests CHANGELOG.md
git commit -m "feat(core): adaptive mode and frost temperature intents, settings order, RoomParams"
```

---

### Task 4: Profile capabilities

**Files:**
- Modify: `custom_components/easy_trv/profiles/schema.py` (`INTENTS` + `set_adaptive_mode`, `set_frost_temperature`)
- Modify: `custom_components/easy_trv/profiles/builtin/sonoff_trvzb_native.yaml`
- Modify: `docs/profiles/sonoff-trvzb-native.md` (capability table rows, firmware map row 1.4.4 → "adaptive mode: `ADAPTIVE_MODE`"), `docs/concept/profiles.md` (intent list)
- Test: `tests/profiles/test_engine.py` (planning tests), `tests/profiles/test_schema.py` (intent acceptance)

**Interfaces:**
- Consumes: intents from Task 3.
- Produces: built-in profile capabilities `adaptive_mode` (`0xFC11/0x6017`, `encode: {map: {"true": 2, "false": 0}}`, `verify: {rule: one_of, "true": [2], "false": [0, 1]}`, `min_firmware: "1.4.4"`) and `frost_protection` (`0xFC11/0x6002`, `temperature_centi`, `range: [4, 35]`, `equals`).

- [ ] **Step 1: Write the failing tests**

```python
def test_builtin_profile_plans_adaptive_mode_on_and_off():
    p = builtin_profile()
    on = p.plan(SetAdaptiveMode(True), (1, 4, 4)); off = p.plan(SetAdaptiveMode(False), (1, 4, 4))
    assert (on.attribute, on.raw_value, on.accepted) == (0x6017, 2, frozenset({2}))
    assert (off.raw_value, off.accepted) == (0, frozenset({0, 1}))

def test_adaptive_mode_needs_firmware_1_4_4():
    with pytest.raises(CapabilityUnsupported):
        builtin_profile().plan(SetAdaptiveMode(True), (1, 4, 1))

def test_frost_temperature_encodes_centi_and_rejects_out_of_range():
    op = builtin_profile().plan(SetFrostTemperature(7.0), (1, 3, 0))
    assert (op.attribute, op.raw_value) == (0x6002, 700)
    with pytest.raises(ValueOutOfRange):
        builtin_profile().plan(SetFrostTemperature(3.0), (1, 3, 0))

def test_schema_accepts_the_two_new_intents():  # validate_profile_dict on a minimal dict with both capabilities
```

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/profiles -q --no-cov` — Expected: FAIL (intent not in `INTENTS` / capability missing).

- [ ] **Step 3: Implement** schema `INTENTS`, the YAML, the two docs tables.

- [ ] **Step 4: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/profiles -q --no-cov && UV_CACHE_DIR=.uv-cache uv run mypy custom_components/easy_trv/profiles` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add custom_components/easy_trv/profiles tests/profiles docs/profiles docs/concept
git commit -m "feat(profiles): adaptive mode and frost protection capabilities"
```

---

### Task 5: Setup with resolved settings and migration

**Files:**
- Modify: `custom_components/easy_trv/__init__.py` (`room_config_from_subentry`, `_build_room`, `async_setup_entry`, new `async_migrate_entry`)
- Modify: `custom_components/easy_trv/config_flow.py` (`EasyTrvConfigFlow.VERSION = 2`)
- Modify: `custom_components/easy_trv/issues.py` (`raise_settings_migrated`, `ISSUE_SETTINGS_MIGRATED = "settings_migrated"`)
- Modify: `custom_components/easy_trv/strings.json`, `translations/en.json`, `translations/de.json` (issue `settings_migrated`: title "Thermostat settings changed by the update", description naming `{rooms}` and the new global hysteresis `{hysteresis}`; `is_fixable: true`, fix flow = confirm)
- Test: `tests/test_init.py`, `tests/test_room_setup.py`, `tests/test_issues.py`

**Interfaces:**
- Consumes: `resolve_settings`, `overrides` (Task 2); `RoomConfig`, `RoomParams.from_settings` (Task 3).
- Produces:
  ```python
  def room_config_from_subentry(subentry_id: str, title: str, data: Mapping[str, Any], device_refs: Mapping[str, DeviceRef], settings: RoomSettings) -> RoomConfig
  async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool
  ```
  `EasyTrvRuntimeData` gains `settings: dict[str, RoomSettings]` (subentry id → resolved settings) for diagnostics (Task 9) and the controller gets `RoomParams.from_settings(settings[subentry_id])`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_room_uses_global_hysteresis_when_room_says_global(hass, patched_gateway):
    entry = make_entry(hass, options={"hysteresis": "0_6"}, rooms=[room_data(hysteresis="global")])
    await start(hass, entry)
    assert [w[4] for w in patched_gateway.writes if w[3] == 0x6011] == [-60]

async def test_migrate_v1_entry_turns_leave_into_global_and_raises_issue(hass):
    entry = make_entry(hass, version=1, rooms=[room_data(name="Living", hysteresis="leave"), room_data(name="Bed", hysteresis="0_2")])
    await start(hass, entry)
    assert entry.version == 2
    living, bed = entry.subentries.values()
    assert living.data["hysteresis"] == "global" and bed.data["hysteresis"] == "0_2"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "settings_migrated")
    assert issue is not None and issue.translation_placeholders["rooms"] == "Living"

async def test_migrated_room_with_default_adaptive_writes_no_hysteresis(hass, patched_gateway):
    # version 1 room with hysteresis "leave"; after start the gateway saw a 0x6017=2 write and no 0x6011 write

async def test_migration_without_leave_rooms_raises_no_issue(hass): ...
```

`make_entry` in `tests/helpers_entry.py` gets a `version: int = 2` parameter.

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_init.py tests/test_room_setup.py -q --no-cov` — Expected: FAIL (`room_config_from_subentry` signature, no migration).

- [ ] **Step 3: Implement.** `async_migrate_entry`: for `entry.version == 1`, for each room subentry rewrite `hysteresis == "leave"` to `"global"` through `hass.config_entries.async_update_subentry`, collect the room titles, `hass.config_entries.async_update_entry(entry, version=2)`, raise `settings_migrated` when the list is not empty (placeholders `rooms` = ", ".join(titles), `hysteresis` = "0.4 K"), return `True`. `_build_room` resolves `settings = resolve_settings(entry.options, subentry.data)` and passes `RoomParams.from_settings(settings)` to the controller.

- [ ] **Step 4: Run** `UV_CACHE_DIR=.uv-cache uv run pytest -q --no-cov` — Expected: whole suite PASS.

- [ ] **Step 5: Append to `CHANGELOG.md`** under Changed: "Hysteresis is always written (default 0.4 K); rooms that had 'leave unchanged' are migrated and get a Repairs notice".

- [ ] **Step 6: Commit**

```bash
git add -A custom_components tests CHANGELOG.md
git commit -m "feat: resolve room settings at setup, migrate entries to version 2"
```

---

### Task 6: Options flow and room settings step

**Files:**
- Modify: `custom_components/easy_trv/config_flow.py`
- Modify: `custom_components/easy_trv/strings.json`, `translations/en.json`, `translations/de.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `DEFAULTS`, `GLOBAL`, `CONF_*` (Task 2).
- Produces: options keys `window_detection`, `hysteresis`, `adaptive_mode`, `frost_temperature` next to the five Phase 1 keys; room step `settings` with keys `profile`, `window_detection`, `hysteresis`, `adaptive_mode`, `frost_temperature`, `refresh_minutes`, `verify_delay_seconds`, `max_attempts`, `min_delta_kelvin`, `sensor_timeout_minutes`; selects store `"global"` or a value, numbers are stored only when filled.

Form layout: options flow uses `section()` groups `writes` (refresh, verify delay, attempts, min delta, sensor timeout) and `thermostat` (window detection, hysteresis, adaptive mode, frost temperature); the stored options stay flat (flatten on submit, nest for display). Room flow: the `entities` step no longer carries `profile`/`window_detection`/`hysteresis`; a new step `settings` follows it (add) and the reconfigure flow shows `entities` then `settings` too. Selects: options `["global", *values]` with `translation_key` of the setting; `description_placeholders` carry `global_<key>` with the current global value rendered through the same translations (`"0,4 K"`, "An", …) so the description can say "Global (currently {global_hysteresis})". Numbers: `vol.Optional(key): NumberSelector(... mode box, min/max from the spec table)`; absent → not stored.

- [ ] **Step 1: Write the failing tests**

```python
async def test_options_flow_stores_thermostat_settings_flat(hass): ...   # submit sections, assert entry.options == flat dict with hysteresis "0_6" and adaptive_mode "off"
async def test_options_empty_number_falls_back_to_default(hass): ...     # refresh_minutes left empty -> key absent in options
async def test_room_flow_has_settings_step_with_global_defaults(hass): ... # after entities -> step settings; default suggested values are "global"; placeholders contain "0.4 K"
async def test_room_settings_store_only_overrides(hass): ...            # choose hysteresis 0_2, leave the rest -> subentry.data has hysteresis "0_2", adaptive_mode "global", no refresh_minutes key
async def test_hysteresis_select_has_no_leave_option(hass): ...
async def test_reconfigure_shows_current_overrides(hass): ...
```

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_config_flow.py -q --no-cov` — Expected: FAIL (no `settings` step, options keys missing).

- [ ] **Step 3: Implement** the forms and the strings for both flows in `strings.json` + `en.json` + `de.json` (`de`: "Global (aktuell: {global_hysteresis})" etc.; the window-detection and adaptive texts are finalised in Task 8 but the keys exist here).

- [ ] **Step 4: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_config_flow.py tests/test_translations.py -q --no-cov` then the whole suite — Expected: PASS.

- [ ] **Step 5: Append to `CHANGELOG.md`** under Added: "Every setting has a global default and can be overridden per room".

- [ ] **Step 6: Commit**

```bash
git add custom_components/easy_trv/config_flow.py custom_components/easy_trv/strings.json custom_components/easy_trv/translations tests/test_config_flow.py CHANGELOG.md
git commit -m "feat(flow): global settings with per-room overrides"
```

---

### Task 7: Device naming and area

**Files:**
- Modify: `custom_components/easy_trv/sensor.py` (`DeviceInfo`)
- Modify: `custom_components/easy_trv/__init__.py` (pass the area name into the platform via `EasyTrvRuntimeData.areas: dict[str, str | None]`, subentry id → area name, resolved with `homeassistant.helpers.area_registry`)
- Modify: `custom_components/easy_trv/strings.json`, `en.json`, `de.json` (`"device": {"room": {"name": "{room} heating"}}` / "Heizung {room}")
- Test: `tests/test_sensor.py`

- [ ] **Step 1: Write the failing tests**

```python
async def test_room_device_is_named_heating_and_sits_in_its_area(hass, patched_gateway):
    area = ar.async_get(hass).async_get_or_create("Wohnzimmer")
    entry = make_entry(hass, rooms=[room_data(name="Wohnzimmer", area_id=area.id)])
    await start(hass, entry)
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, next(iter(entry.subentries)))})
    assert device.name == "Wohnzimmer heating" and device.area_id == area.id
    assert hass.states.get("sensor.wohnzimmer_heating_status") is not None

async def test_custom_room_without_area_has_no_area(hass, patched_gateway): ...
async def test_device_name_follows_room_name_unless_renamed_by_user(hass, patched_gateway): ...  # rename subentry title -> device.name changes; set name_by_user first -> stays
```

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_sensor.py -q --no-cov` — Expected: FAIL (name is the bare room name, no area).

- [ ] **Step 3: Implement** `DeviceInfo(identifiers=..., manufacturer="Easy TRV", translation_key="room", translation_placeholders={"room": subentry.title}, suggested_area=area_name)` and the strings `device` block.

- [ ] **Step 4: Run** `tests/test_sensor.py`, `tests/test_translations.py`, whole suite — Expected: PASS.

- [ ] **Step 5: Append to `CHANGELOG.md`** under Changed: "Room devices are named 'Heizung {room}' / '{room} heating' and are assigned to their area".

- [ ] **Step 6: Commit**

```bash
git add custom_components/easy_trv/sensor.py custom_components/easy_trv/__init__.py custom_components/easy_trv/strings.json custom_components/easy_trv/translations tests/test_sensor.py CHANGELOG.md
git commit -m "feat: name room devices and assign them to their area"
```

---

### Task 8: Texts and plain-name Repairs

**Files:**
- Modify: `custom_components/easy_trv/strings.json`, `en.json`, `de.json` (window detection and adaptive texts per spec section 4; `selector.capability.options` block with the capability labels `external_temperature`, `source_select`, `window_detection_flag`, `hysteresis`, `adaptive_mode`, `frost_protection`; issue texts `trv_unreachable` and `trv_firmware` use `{setting}` and `{thermostat}` instead of `{attribute}` and `{trv}`)
- Modify: `custom_components/easy_trv/issues.py` (`async_capability_labels(hass) -> dict[str, str]`, `thermostat_name(hass, entity_id) -> str`, `raise_unreachable`/`raise_firmware` take `setting: str, thermostat: str` placeholders)
- Modify: `custom_components/easy_trv/controller.py` (gets `labels: Mapping[str, str]` in its constructor; passes `labels.get(capability, capability)` and `thermostat_name(...)`)
- Modify: `custom_components/easy_trv/__init__.py` (load labels once per entry with `await async_capability_labels(hass)` and store in `EasyTrvRuntimeData.labels`)
- Test: `tests/test_issues.py`, `tests/test_controller.py`, `tests/test_translations.py`

- [ ] **Step 1: Write the failing tests**

```python
async def test_capability_labels_come_from_translations(hass):
    labels = await async_capability_labels(hass)
    assert labels["window_detection_flag"] == "Thermostat's open-window detection"
    assert labels["external_temperature"] == "Room temperature"

async def test_unreachable_issue_names_setting_and_thermostat(hass, patched_gateway): ...  # after 3 failures on window_detection_flag the issue placeholders are {"setting": "Thermostat's open-window detection", "thermostat": "Living TRV"} with a registered device name

async def test_labels_fall_back_to_keys_and_entity_ids(hass): ...  # controller built with labels={} and an entity without a device -> placeholders use the capability key and the entity id

def test_window_detection_option_texts_de():  # de.json selector.window_detection.options == {"on": "Erkennung einschalten", "off": "Erkennung ausschalten", "leave": "Im Thermostat unverändert lassen"}
```

- [ ] **Step 2: Run** the three test files — Expected: FAIL (`async_capability_labels` missing, old placeholders).

- [ ] **Step 3: Implement.** `async_capability_labels` calls `homeassistant.helpers.translation.async_get_translations(hass, hass.config.language, "selector", {DOMAIN})` and picks the keys `component.easy_trv.selector.capability.options.<capability>`; missing keys fall back to the capability name. Texts per spec section 4, in `en` and `de`.

- [ ] **Step 4: Run** the whole suite — Expected: PASS.

- [ ] **Step 5: Append to `CHANGELOG.md`** under Changed: "Repairs messages name the setting and the thermostat; clearer texts for the thermostat's open-window detection and the adaptive mode".

- [ ] **Step 6: Commit**

```bash
git add -A custom_components tests CHANGELOG.md
git commit -m "feat: plain-name Repairs messages and clearer setting texts"
```

---

### Task 9: Diagnostics, docs, device checklist

**Files:**
- Modify: `custom_components/easy_trv/diagnostics.py` (add the resolved settings per room from `EasyTrvRuntimeData.settings`)
- Modify: `README.md` (settings section: global vs room, hysteresis default, adaptive mode and what it disables, frost temperature; a note recommending to hide the ZHA climate entities of single thermostats once the room thermostat exists in Phase 2b — keep this to two sentences)
- Modify: `docs/testing-on-device.md` (hardware check "adaptive mode on: 0x6017 reads 2, hysteresis not written; adaptive off: 0x6011 reads −40"; new spike S5 "adaptive mode with the external sensor: does the room stay within ±0.5 K over an evening?")
- Test: `tests/test_diagnostics.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_diagnostics_include_resolved_room_settings(hass, hass_client, patched_gateway):
    ...  # diagnostics["rooms"][<id>]["settings"]["hysteresis"] == 0.4 and ["adaptive_mode"] == "on"
```
(The HTTP-client variant is skipped in the sandbox by the `local_binding` fixture; add a direct call test of `async_get_config_entry_diagnostics` as well so the assertion runs locally.)

- [ ] **Step 2: Run** `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_diagnostics.py -q --no-cov` — Expected: FAIL (`settings` key missing).

- [ ] **Step 3: Implement** diagnostics and write the docs.

- [ ] **Step 4: Run** the whole suite with coverage: `UV_CACHE_DIR=.uv-cache uv run pytest -q` — Expected: PASS, total coverage ≥ 95 %. Then `UV_CACHE_DIR=.uv-cache uv run ruff check . && UV_CACHE_DIR=.uv-cache uv run ruff format --check . && UV_CACHE_DIR=.uv-cache uv run mypy` — Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add custom_components/easy_trv/diagnostics.py README.md docs/testing-on-device.md tests/test_diagnostics.py
git commit -m "docs: settings in README, diagnostics and device checklist for Phase 2a"
```
