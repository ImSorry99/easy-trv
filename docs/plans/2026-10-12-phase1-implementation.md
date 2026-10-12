# Easy TRV Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A HACS-installable Home Assistant integration `easy_trv` that keeps Sonoff TRVZB thermostats (ZHA) fed with a room's external temperature through a declarative device profile, verifies every write, and reports failures as Repairs issues, with exactly one status sensor per room.

**Architecture:** Pure-Python room logic (`core/`) emits intents; a YAML-defined profile (`profiles/`) turns each intent into one Zigbee attribute operation with a verify rule; a thin controller executes operations serially per room through a ZHA adapter (`adapters/`) and feeds results back. Config is one entry with one subentry per room.

**Tech Stack:** Python 3.14, Home Assistant 2026.10.x (minimum 2026.8.0), `zha` library via `homeassistant.components.zha.helpers`, zigpy, voluptuous, `pytest-homeassistant-custom-component==0.13.372`, uv, Ruff, mypy.

**Spec:** `REQUIREMENTS.md` (what), `docs/specs/2026-10-12-easy-trv-design.md` (how), `docs/concept/profiles.md`, `docs/profiles/sonoff-trvzb-native.md`.

## Global Constraints

- Domain `easy_trv`; manifest: `"dependencies": ["zha"]`, `"integration_type": "hub"`, `"iot_class": "local_push"`, `"single_config_entry": true`, `"version": "0.1.0"`, `"codeowners": ["@ImSorry99"]`, `"loggers": ["custom_components.easy_trv"]`, `"requirements": []`.
- `hacs.json`: `"homeassistant": "2026.8.0"`. Dev/test against HA 2026.10.x: `pytest-homeassistant-custom-component==0.13.372`.
- Zigbee: cluster `0xFC11`, endpoint `1`, **never pass a manufacturer code**. Raw temperature = `round-half-up(°C, 1 decimal) × 100` as int; valid written range 0–9990.
- Defaults: `refresh_minutes` 20 (5–90), `verify_delay_seconds` 10 (2–60), `max_attempts` 3 (1–10), `min_delta_kelvin` 0.1 (0.1–1.0), `sensor_timeout_minutes` 120 (0 = off, else 10–720). Internal: debounce 5 s, operation timeout 20 s, backoff `(5, 15, 45)` s, tick 60 s.
- Issue ids: `trv_unreachable_<ieee without colons>`, `trv_firmware_<ieee without colons>`, `zha_api_incompatible`, `profile_invalid_<file stem>`. Event names: `easy_trv_write_failed`, `easy_trv_recovered`, `easy_trv_failsafe_detected`.
- Status sensor states: `ok`, `degraded`, `error`. Attributes: `active_sensor`, `source`, `trv_health`, `last_error`.
- Translations: `strings.json` identical to `translations/en.json`; `translations/de.json` with the same keys.
- Code, comments, commit messages in English; conventional commits; **no Co-Authored-By lines**.
- No `DataUpdateCoordinator`, no `climate`/`number`/`switch` entities, no `persistent_notification`.
- `core/` and `profiles/engine.py` must not import `homeassistant`.

## Review Focus

1. Temperature sensor reporting in °F or without a unit: °F is converted to °C; no unit or another unit makes the sensor unusable with one warning (Task 2 `test_parse_temperature_units`, Task 8 `test_unit_warning_logged_once`).
2. A thermostat already used by another room: the room form rejects it with `trv_in_other_room` (Task 10 `test_room_rejects_trv_from_other_room`).
3. Sensor flapping `unavailable` ↔ value inside the debounce window: at most one value write, no source toggling (Task 6 `test_flapping_sensor_single_write`).
4. ZHA not ready at startup: `ConfigEntryNotReady`, issue `zha_api_incompatible`, and a later successful setup deletes the issue (Task 9 `test_setup_zha_unavailable_retries_and_clears_issue`).
5. Source read-back `3` ("external restored") after a write of `1` counts as success; `2` is routed as the `failsafe` signal and never counted as a failed attempt (Task 4 `test_plan_set_source_accepts_3`, Task 6 `test_failsafe_signal_reenables_without_counting_attempt`).

---

## File structure

```
custom_components/easy_trv/
  __init__.py           setup/unload, runtime data, reload listener, profile selection per room
  manifest.json
  const.py              DOMAIN, CONF_*, DEFAULT_*, EVENT_*, ISSUE_* , SUBENTRY_ROOM, PLATFORMS
  config_flow.py        EasyTrvConfigFlow, RoomSubentryFlow, EasyTrvOptionsFlow
  controller.py         RoomController (HA glue, serial worker, timers)
  issues.py             raise/clear helpers for Repairs issues
  sensor.py             RoomStatusSensor
  diagnostics.py
  strings.json, translations/en.json, translations/de.json, icons.json, quality_scale.yaml
  core/__init__.py
  core/values.py        parse/round/encode temperatures, parse firmware versions
  core/model.py         enums, configs, state, intents, events, outcomes, commands
  core/room_logic.py    RoomLogic: pure state machine
  profiles/__init__.py
  profiles/schema.py    voluptuous schema + vocabularies (intents, encoders, verify rules)
  profiles/engine.py    Profile, CapabilitySpec, Operation, plan()
  profiles/loader.py    load builtin + user YAML, validate, registry
  profiles/builtin/sonoff_trvzb_native.yaml
  adapters/__init__.py
  adapters/zha_gateway.py  DeviceRef, resolve_device(), TrvGateway protocol, ZhaLibraryGateway
tests/
  conftest.py, fake_gateway.py
  core/test_values.py, core/test_room_logic.py
  profiles/test_schema.py, profiles/test_engine.py, profiles/test_loader.py, profiles/invalid/*.yaml
  adapters/test_zha_gateway.py
  test_controller.py, test_init.py, test_sensor.py, test_diagnostics.py, test_config_flow.py, test_translations.py
pyproject.toml, hacs.json, .ruff.toml, .github/workflows/{validate,lint,test}.yml, docs/testing-on-device.md
```

---

### Task 1: Project skeleton, tooling, CI

**Files:**
- Create: `pyproject.toml`, `.ruff.toml`, `hacs.json`, `custom_components/easy_trv/manifest.json`, `custom_components/easy_trv/__init__.py` (empty docstring only), `custom_components/easy_trv/const.py`, `custom_components/easy_trv/{core,profiles,adapters}/__init__.py`, `tests/conftest.py`, `tests/test_const.py`, `.github/workflows/validate.yml`, `.github/workflows/lint.yml`, `.github/workflows/test.yml`
- Modify: `.gitignore` (add `uv.lock` is **kept**, not ignored)

**Interfaces:**
- Produces: `const.DOMAIN = "easy_trv"`, `SUBENTRY_ROOM = "room"`, `PLATFORMS = [Platform.SENSOR]`, `CONF_TRVS = "trvs"`, `CONF_TEMP_SENSORS = "temp_sensors"`, `CONF_HUMIDITY_SENSORS = "humidity_sensors"`, `CONF_AREA = "area_id"`, `CONF_NAME = "name"`, `CONF_PROFILE = "profile"`, `CONF_WINDOW_DETECTION = "window_detection"`, `CONF_HYSTERESIS = "hysteresis"`, `CONF_REFRESH_MINUTES = "refresh_minutes"`, `CONF_VERIFY_DELAY = "verify_delay_seconds"`, `CONF_MAX_ATTEMPTS = "max_attempts"`, `CONF_MIN_DELTA = "min_delta_kelvin"`, `CONF_SENSOR_TIMEOUT = "sensor_timeout_minutes"`, `DEFAULT_REFRESH_MINUTES = 20`, `DEFAULT_VERIFY_DELAY = 10`, `DEFAULT_MAX_ATTEMPTS = 3`, `DEFAULT_MIN_DELTA = 0.1`, `DEFAULT_SENSOR_TIMEOUT = 120`, `DEBOUNCE_SECONDS = 5.0`, `OPERATION_TIMEOUT = 20.0`, `BACKOFF_SECONDS = (5.0, 15.0, 45.0)`, `TICK_SECONDS = 60`, `EVENT_WRITE_FAILED = "easy_trv_write_failed"`, `EVENT_RECOVERED = "easy_trv_recovered"`, `EVENT_FAILSAFE = "easy_trv_failsafe_detected"`, `ISSUE_ZHA_API = "zha_api_incompatible"`, `PROFILE_AUTO = "auto"`, `USER_PROFILES_DIR = "easy_trv/profiles"`.

- [ ] **Step 1: Write `pyproject.toml`**

`[project] name = "easy-trv", version = "0.1.0", requires-python = ">=3.14"`, no runtime deps. `[dependency-groups] dev = ["pytest-homeassistant-custom-component==0.13.372", "ruff==0.14.*", "mypy==1.19.*", "types-PyYAML"]`. `[tool.pytest.ini_options] asyncio_mode = "auto"`, `testpaths = ["tests"]`, `addopts = "-p no:cacheprovider --cov=custom_components.easy_trv --cov-report=term-missing"`. `[tool.mypy] strict = true`, `files = ["custom_components/easy_trv/core", "custom_components/easy_trv/profiles", "custom_components/easy_trv/adapters"]`, `ignore_missing_imports = true`. If a pinned ruff/mypy minor does not exist on PyPI, use the newest available and note it in the commit message.

- [ ] **Step 2: Write `.ruff.toml`** (copy of `ludeeus/integration_blueprint` `.ruff.toml`, `target-version = "py314"`, `select = ["ALL"]`, ignore list as in the blueprint plus `D203, D213, COM812, ISC001`; `[lint.per-file-ignores] "tests/**" = ["S101", "D", "ANN", "PLR2004"]`).

- [ ] **Step 3: Write `manifest.json`, `hacs.json`, `const.py`, package `__init__.py` files** with the values from Global Constraints and the Interfaces block. `hacs.json`: `{"name": "Easy TRV", "homeassistant": "2026.8.0", "render_readme": true}`.

- [ ] **Step 4: Write `tests/conftest.py`**

```python
import pytest

@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations for every test."""
```

- [ ] **Step 5: Write the failing test `tests/test_const.py`**

```python
from custom_components.easy_trv import const

def test_defaults_match_requirements() -> None:
    assert const.DOMAIN == "easy_trv"
    assert const.DEFAULT_REFRESH_MINUTES == 20
    assert const.DEFAULT_VERIFY_DELAY == 10
    assert const.DEFAULT_MAX_ATTEMPTS == 3
    assert const.DEFAULT_MIN_DELTA == 0.1
    assert const.DEFAULT_SENSOR_TIMEOUT == 120
    assert const.BACKOFF_SECONDS == (5.0, 15.0, 45.0)
```

- [ ] **Step 6: Install and run**

Run: `uv python install 3.14 && uv sync && uv run pytest tests/test_const.py -v`
Expected: PASS. (Inside the Claude sandbox `uv sync` needs `pypi.org` and `files.pythonhosted.org` allowed.)

- [ ] **Step 7: Write CI workflows**

`validate.yml`: checkout, `home-assistant/actions/hassfest@master`, `hacs/action@main` with `category: integration`, `ignore: brands`. `lint.yml`: `astral-sh/setup-uv`, `uv sync`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy`. `test.yml`: `uv sync`, `uv run pytest`. All on `push` and `pull_request`.

- [ ] **Step 8: Run lint locally and commit**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
Expected: no findings, 1 passed.

```bash
git add -A && git commit -m "chore: project skeleton, tooling and CI"
```

---

### Task 2: Value helpers (`core/values.py`)

**Files:**
- Create: `custom_components/easy_trv/core/values.py`
- Test: `tests/core/test_values.py`

**Interfaces:**
- Produces: `Version = tuple[int, int, int]`; `parse_version_string(s: str) -> Version` (`"1.2.1"` → `(1, 2, 1)`, raises `ValueError`); `parse_zha_sw_version(s: str | None) -> Version | None` (`"0x00001404"` → `(1, 4, 4)`, `"0x1300"` → `(1, 3, 0)`, anything else → `None`); `round_tenth(celsius: float) -> float` (Decimal `ROUND_HALF_UP` to 1 decimal); `to_centi(celsius: float) -> int` (`round_tenth` then `× 100`); `from_centi(raw: int) -> float`; `parse_temperature(state: str, unit: str | None) -> float | None` (`"unknown"`, `"unavailable"`, non-float → `None`; unit `"°F"` converted to °C; unit `"°C"` or `"C"` accepted; any other unit → `None`); `is_in_range(celsius: float, low: float, high: float) -> bool`.
- Note: `parse_temperature` must not import `homeassistant`; use `(f − 32) × 5 / 9` directly.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from custom_components.easy_trv.core import values as v

@pytest.mark.parametrize(("celsius", "raw"), [(21.35, 2140), (21.34, 2130), (0.0, 0), (99.9, 9990), (19.9, 1990), (21.25, 2130)])
def test_to_centi_rounds_half_up(celsius: float, raw: int) -> None:
    assert v.to_centi(celsius) == raw

@pytest.mark.parametrize(("state", "unit", "expected"), [
    ("21.3", "°C", 21.3), ("70.0", "°F", pytest.approx(21.11, abs=0.01)),
    ("unknown", "°C", None), ("unavailable", "°C", None), ("abc", "°C", None),
    ("21.3", None, None), ("21.3", "K", None), ("21.3", "C", 21.3),
])
def test_parse_temperature_units(state: str, unit: str | None, expected: float | None) -> None:
    assert v.parse_temperature(state, unit) == expected

@pytest.mark.parametrize(("sw", "expected"), [("0x00001404", (1, 4, 4)), ("0x1300", (1, 3, 0)), ("0x00001201", (1, 2, 1)), (None, None), ("garbage", None), ("1.4.4", (1, 4, 4))])
def test_parse_zha_sw_version(sw: str | None, expected: tuple[int, int, int] | None) -> None:
    assert v.parse_zha_sw_version(sw) == expected

def test_parse_version_string() -> None:
    assert v.parse_version_string("1.2.1") == (1, 2, 1)
    with pytest.raises(ValueError):
        v.parse_version_string("1.2")

def test_is_in_range_inclusive() -> None:
    assert v.is_in_range(0.0, 0.0, 99.9) and v.is_in_range(99.9, 0.0, 99.9)
    assert not v.is_in_range(-0.1, 0.0, 99.9) and not v.is_in_range(100.0, 0.0, 99.9)
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/core/test_values.py -v` → ImportError.
- [ ] **Step 3: Implement the functions in `core/values.py`.** `parse_zha_sw_version`: accept `"a.b.c"` too; for hex strings `int(s, 16)` then nibbles `(v >> 12) & 0xF, (v >> 8) & 0xF, v & 0xFF`.
- [ ] **Step 4: Run to verify they pass** — same command → all PASS.
- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(core): temperature and firmware value helpers"`

---

### Task 3: Profile schema, built-in TRVZB profile, loader

**Files:**
- Create: `custom_components/easy_trv/profiles/schema.py`, `custom_components/easy_trv/profiles/loader.py`, `custom_components/easy_trv/profiles/builtin/sonoff_trvzb_native.yaml`, `tests/profiles/test_schema.py`, `tests/profiles/test_loader.py`, `tests/profiles/invalid/bad_encoder.yaml`, `tests/profiles/invalid/duplicate_intent.yaml`, `tests/profiles/collision/sonoff_trvzb_native.yaml`

**Interfaces:**
- Produces (`schema.py`): `INTENTS = ("set_room_temperature", "set_source", "set_window_detection", "set_hysteresis")`; `ENCODERS = ("temperature_centi", "kelvin_centi_negative", "bool")` plus dict forms `{"map": {str: int}}` and `{"const": int}`; `PROFILE_SCHEMA: vol.Schema`; `validate_profile_dict(data: dict[str, Any]) -> dict[str, Any]` (raises `vol.Invalid`; additionally raises `vol.Invalid("duplicate intent …")` when two capabilities share an intent).
- Produces (`loader.py`): `class ProfileLoadError(Exception)` with `.file: str`; `load_profile_file(path: Path) -> dict[str, Any]` (sync: `yaml.safe_load` → `validate_profile_dict`, raises `ProfileLoadError`); `BUILTIN_DIR = Path(__file__).parent / "builtin"`; `@dataclass(frozen=True) class LoadResult: profiles: dict[str, Profile]; errors: tuple[ProfileLoadError, ...]`; `load_profiles_sync(user_dir: Path | None) -> LoadResult` (builtin first; a user file whose `id` collides with a builtin id is reported as an error and skipped); `async def async_load_profiles(hass: HomeAssistant) -> LoadResult` (`hass.async_add_executor_job(load_profiles_sync, Path(hass.config.path(USER_PROFILES_DIR)))`).
- `Profile` comes from Task 4; for this task `load_profiles_sync` builds `Profile.from_dict(validated)` — define `Profile.from_dict` in Task 4 and run Task 3's loader tests after Task 4 is green (order of commits: schema+yaml first, loader after engine).
- Built-in YAML: exactly the document in `docs/concept/profiles.md` (`schema: 1`, `id: sonoff_trvzb_native`, `tested_firmware: ["1.3.0", "1.4.4"]`, four capabilities, hex ints as `0xFC11` etc.).

- [ ] **Step 1: Write the failing schema tests**

```python
import yaml, pytest, voluptuous as vol
from pathlib import Path
from custom_components.easy_trv.profiles import schema

BUILTIN = Path("custom_components/easy_trv/profiles/builtin/sonoff_trvzb_native.yaml")

def test_builtin_profile_validates() -> None:
    data = schema.validate_profile_dict(yaml.safe_load(BUILTIN.read_text()))
    assert data["id"] == "sonoff_trvzb_native"
    assert data["capabilities"]["external_temperature"]["write"]["cluster"] == 0xFC11
    assert data["capabilities"]["source_select"]["signals"] == {2: "failsafe"}
    assert data["capabilities"]["hysteresis"]["min_firmware"] == "1.3.0"

def test_unknown_encoder_rejected() -> None:
    data = yaml.safe_load(BUILTIN.read_text())
    data["capabilities"]["external_temperature"]["write"]["encode"] = "float"
    with pytest.raises(vol.Invalid):
        schema.validate_profile_dict(data)

def test_duplicate_intent_rejected() -> None:
    data = yaml.safe_load(BUILTIN.read_text())
    data["capabilities"]["copy"] = dict(data["capabilities"]["external_temperature"])
    with pytest.raises(vol.Invalid, match="duplicate intent"):
        schema.validate_profile_dict(data)
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/profiles/test_schema.py -v`.
- [ ] **Step 3: Write the built-in YAML and `schema.py`.** Schema per `docs/concept/profiles.md`: `schema` must equal 1, `id` slug, `match` manufacturer/model strings, `min_firmware` matches `^\d+\.\d+\.\d+$`, `tested_firmware` list of the same, capabilities: `intent` in `INTENTS`, optional `min_firmware`, `write` (`endpoint` 0–240, `cluster` 0–0xFFFF, `attribute` 0–0xFFFF, `encode`, optional `range` `[low, high]`), `verify` (`{"rule": "equals"}` or `{"rule": "one_of", <key>: [int, ...]}`), `signals` `{int: slug}`, `also_read` `[int]`.
- [ ] **Step 4: Run schema tests** → PASS. Commit: `git commit -m "feat(profiles): profile schema and built-in Sonoff TRVZB profile"`.
- [ ] **Step 5: Write the failing loader tests** (run after Task 4)

```python
from pathlib import Path
from custom_components.easy_trv.profiles import loader

def test_builtin_loads() -> None:
    result = loader.load_profiles_sync(user_dir=None)
    assert set(result.profiles) == {"sonoff_trvzb_native"} and result.errors == ()

def test_invalid_user_files_reported_and_skipped() -> None:
    result = loader.load_profiles_sync(Path("tests/profiles/invalid"))
    assert set(result.profiles) == {"sonoff_trvzb_native"}
    assert sorted(e.file for e in result.errors) == ["bad_encoder.yaml", "duplicate_intent.yaml"]

def test_user_id_collision_with_builtin_is_error() -> None:
    result = loader.load_profiles_sync(Path("tests/profiles/collision"))
    assert [e.file for e in result.errors] == ["sonoff_trvzb_native.yaml"]
    assert result.profiles["sonoff_trvzb_native"].name == "Sonoff TRVZB (native)"
```

Create the three fixture YAML files: `bad_encoder.yaml` (builtin copy, `id: user_bad`, encoder `float`), `duplicate_intent.yaml` (`id: user_dup`, two capabilities with `set_source`), `collision/sonoff_trvzb_native.yaml` (builtin copy with `name: Collision`).

- [ ] **Step 6: Implement `loader.py`**, run `uv run pytest tests/profiles -v` → PASS, commit `feat(profiles): YAML loader for built-in and user profiles`.

---

### Task 4: Profile engine (`profiles/engine.py`)

**Files:**
- Create: `custom_components/easy_trv/profiles/engine.py`, `custom_components/easy_trv/core/model.py` (intents only in this task; the rest in Task 5)
- Test: `tests/profiles/test_engine.py`

**Interfaces:**
- Produces (`core/model.py`, intents): `@dataclass(frozen=True) class SetRoomTemperature: celsius: float`; `class SetSource: source: Literal["external", "internal"]`; `class SetWindowDetection: enabled: bool`; `class SetHysteresis: kelvin: float`; `Intent = SetRoomTemperature | SetSource | SetWindowDetection | SetHysteresis`; `INTENT_NAMES: dict[type, str] = {SetRoomTemperature: "set_room_temperature", …}`.
- Produces (`engine.py`): `class CapabilityUnsupported(Exception)` with `.capability: str`, `.required: Version | None`, `.installed: Version | None`; `class ValueOutOfRange(ValueError)`; `@dataclass(frozen=True) class Operation: capability: str; endpoint: int; cluster: int; attribute: int; raw_value: int; accept: frozenset[int]; signals: Mapping[int, str]; also_read: tuple[int, ...]; extra_signals: Mapping[int, Mapping[int, str]]`; `@dataclass(frozen=True) class CapabilitySpec` (fields mirror the validated YAML); `@dataclass(frozen=True) class Profile: id: str; name: str; manufacturer: str; model: str; min_firmware: Version; tested_firmware: tuple[Version, ...]; capabilities: Mapping[str, CapabilitySpec]` with `@classmethod from_dict(data: dict) -> Profile`, `matches(manufacturer: str, model: str) -> bool` (case-insensitive, stripped), `device_supported(firmware: Version | None) -> bool` (None → True), `capability_for(intent: Intent) -> str | None`, `supports(capability: str, firmware: Version | None) -> bool`, `plan(intent: Intent, firmware: Version | None) -> Operation` (raises `CapabilityUnsupported` when the profile has no capability for the intent or firmware is below the capability/profile minimum; `ValueOutOfRange` when `range` is violated).
- Encoding: `temperature_centi` → `values.to_centi`; `kelvin_centi_negative` → `-round(k × 100)`; `bool` → 1/0; `map` → `map[str(value)]`; `const` → the constant. `one_of` key for `SetSource` is the source string; for other intents the key is `str(value).lower()`.
- `extra_signals` is built from the capabilities owning each `also_read` attribute.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
from custom_components.easy_trv.core.model import SetRoomTemperature, SetSource, SetWindowDetection, SetHysteresis
from custom_components.easy_trv.profiles import engine, loader

@pytest.fixture
def trvzb() -> engine.Profile:
    return loader.load_profiles_sync(None).profiles["sonoff_trvzb_native"]

def test_matches_case_insensitive(trvzb: engine.Profile) -> None:
    assert trvzb.matches("sonoff", "TRVZB") and not trvzb.matches("SONOFF", "TRV-ZBT")

def test_plan_room_temperature(trvzb: engine.Profile) -> None:
    op = trvzb.plan(SetRoomTemperature(21.35), (1, 3, 0))
    assert (op.cluster, op.endpoint, op.attribute, op.raw_value) == (0xFC11, 1, 0x600D, 2140)
    assert op.accept == frozenset({2140}) and op.also_read == (0x600E,)
    assert op.extra_signals == {0x600E: {2: "failsafe"}}

def test_plan_set_source_accepts_3(trvzb: engine.Profile) -> None:
    op = trvzb.plan(SetSource("external"), None)
    assert op.raw_value == 1 and op.accept == frozenset({1, 3}) and op.signals == {2: "failsafe"}
    assert trvzb.plan(SetSource("internal"), None).accept == frozenset({0})

def test_plan_hysteresis_and_window(trvzb: engine.Profile) -> None:
    assert trvzb.plan(SetHysteresis(0.4), (1, 3, 0)).raw_value == -40
    assert trvzb.plan(SetWindowDetection(True), (1, 2, 1)).raw_value == 1

def test_firmware_gating(trvzb: engine.Profile) -> None:
    with pytest.raises(engine.CapabilityUnsupported) as exc:
        trvzb.plan(SetHysteresis(0.4), (1, 2, 1))
    assert exc.value.required == (1, 3, 0)
    assert not trvzb.device_supported((1, 1, 6)) and trvzb.device_supported(None)

def test_range_enforced(trvzb: engine.Profile) -> None:
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetRoomTemperature(-0.5), None)
    with pytest.raises(engine.ValueOutOfRange):
        trvzb.plan(SetHysteresis(1.2), (1, 3, 0))
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement intents in `core/model.py` and `engine.py`.**
- [ ] **Step 4: Run `uv run pytest tests/profiles -v`** → PASS (including Task 3 loader tests).
- [ ] **Step 5: Commit** — `git commit -m "feat(profiles): profile engine turning intents into verified operations"`

---

### Task 5: Room logic, part 1 — sensors, source, temperature writes

**Files:**
- Modify: `custom_components/easy_trv/core/model.py`
- Create: `custom_components/easy_trv/core/room_logic.py`
- Test: `tests/core/test_room_logic.py`

**Interfaces (model.py additions):**
- `class Source(StrEnum): EXTERNAL, INTERNAL, NONE`; `class TrvHealth(StrEnum): OK, UNAVAILABLE, UNSUPPORTED, ERROR`; `class RoomStatus(StrEnum): OK, DEGRADED, ERROR`.
- `@dataclass(frozen=True) class GlobalParams: refresh: timedelta; verify_delay: float; max_attempts: int; min_delta: float; sensor_timeout: timedelta | None; debounce: float = 5.0; backoff: tuple[float, ...] = (5.0, 15.0, 45.0)` with `@classmethod defaults() -> GlobalParams`.
- `@dataclass(frozen=True) class TrvConfig: entity_id: str; ieee: str; firmware: Version | None`; `class RoomConfig: room_id: str; name: str; trvs: tuple[TrvConfig, ...]; temp_sensors: tuple[str, ...]; window_detection: Literal["on", "off", "leave"]; hysteresis: float | None`.
- `@dataclass(frozen=True) class SensorReading: entity_id: str; value: float | None; last_reported: datetime | None`.
- `@dataclass(frozen=True) class TrvState: health: TrvHealth = OK; available: bool = True; attempts: Mapping[str, int] = {}; pending_retry: Mapping[str, Intent] = {}; last_error: str | None = None; failsafe_seen_at: datetime | None = None; unsupported: frozenset[str] = frozenset()`.
- `@dataclass(frozen=True) class RoomState: readings: Mapping[str, SensorReading]; trvs: Mapping[str, TrvState]; source: Source = NONE; active_sensor: str | None = None; last_written: float | None = None; last_value_write: datetime | None = None; debounce_pending: bool = False; settings_applied: bool = False` with `@classmethod initial(cfg: RoomConfig) -> RoomState`. All updates via `dataclasses.replace`.
- Events: `Start(sensors: tuple[SensorReading, ...], trv_available: Mapping[str, bool])`, `SensorChanged(reading: SensorReading)`, `TrvAvailabilityChanged(entity_id: str, available: bool)`, `Tick()`, `DebounceElapsed()`, `RetryDue(trv: str, capability: str)`, `OperationResult(trv: str, intent: Intent, capability: str, outcome: Outcome)`.
- Outcomes: `Ok()`, `Mismatch(expected: frozenset[int], actual: int | None)`, `Failed(error: str)`, `Unsupported(capability: str, required: str, installed: str)`, `Signal(name: str)`.
- Commands: `Execute(trv: str, intent: Intent)`, `ScheduleDebounce(seconds: float)`, `ScheduleRetry(trv: str, capability: str, seconds: float)`, `SetStatus(status: RoomStatus, attributes: Mapping[str, Any])`, `RaiseIssue(kind: Literal["unreachable", "firmware"], trv: str, placeholders: Mapping[str, str])`, `ClearIssue(kind: …, trv: str)`, `FireEvent(name: str, data: Mapping[str, Any])`, `Log(level: Literal["debug", "info", "warning"], message: str)`.
- `class RoomLogic: __init__(cfg: RoomConfig, params: GlobalParams, capability_of: Callable[[Intent], str])`; `handle(event: Event, now: datetime, state: RoomState) -> tuple[RoomState, list[Command]]`. Every call ends with exactly one `SetStatus` as the last command. `usable(reading: SensorReading, now: datetime) -> bool` (value not None, 0.0 ≤ v ≤ 99.9, and `sensor_timeout is None or last_reported and now − last_reported ≤ timeout`). `active_sensor(state, now) -> SensorReading | None`.
- Status rule: `ERROR` if any `TrvHealth.ERROR`; `DEGRADED` if (`temp_sensors` and `source != EXTERNAL`) or any trv `UNAVAILABLE`/`UNSUPPORTED` or any `failsafe_seen_at` within `params.refresh`; else `OK`. Attributes: `active_sensor`, `source` (enum value), `trv_health` (`{entity_id: health}`), `last_error` (first non-None).

Test helper at top of the test file:

```python
CFG = RoomConfig("room1", "Living", (TrvConfig("climate.a", "00:11", (1, 3, 0)), TrvConfig("climate.b", "00:22", (1, 3, 0))), ("sensor.t1", "sensor.t2"), "on", 0.4)
T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
def reading(eid: str, value: float | None, at: datetime = T0) -> SensorReading: ...
def logic() -> RoomLogic: return RoomLogic(CFG, GlobalParams.defaults(), capability_of=lambda i: INTENT_NAMES[type(i)].removeprefix("set_"))
def executes(cmds) -> list[tuple[str, Intent]]: return [(c.trv, c.intent) for c in cmds if isinstance(c, Execute)]
```

(`capability_of` yields `room_temperature`, `source`, `window_detection`, `hysteresis`; the controller passes `profile.capability_for` instead.)

- [ ] **Step 1: Write the failing tests**

```python
def test_start_applies_settings_then_external_and_value() -> None:
    st, cmds = logic().handle(Start((reading("sensor.t1", 21.3), reading("sensor.t2", 20.0)), {"climate.a": True, "climate.b": True}), T0, RoomState.initial(CFG))
    assert executes(cmds)[:4] == [("climate.a", SetWindowDetection(True)), ("climate.a", SetHysteresis(0.4)), ("climate.b", SetWindowDetection(True)), ("climate.b", SetHysteresis(0.4))]
    assert executes(cmds)[4:] == [("climate.a", SetSource("external")), ("climate.b", SetSource("external")), ("climate.a", SetRoomTemperature(21.3)), ("climate.b", SetRoomTemperature(21.3))]
    assert st.source == Source.EXTERNAL and st.active_sensor == "sensor.t1" and st.last_written == 21.3
    assert isinstance(cmds[-1], SetStatus)

def test_start_without_usable_sensor_sets_internal_and_degraded() -> None:
    st, cmds = logic().handle(Start((reading("sensor.t1", None), reading("sensor.t2", None)), {"climate.a": True, "climate.b": True}), T0, RoomState.initial(CFG))
    assert [i for _, i in executes(cmds) if isinstance(i, SetSource)] == [SetSource("internal")] * 2
    assert cmds[-1].status == RoomStatus.DEGRADED

def test_priority_falls_through_to_second_sensor() -> None:
    st, cmds = logic().handle(Start((reading("sensor.t1", None), reading("sensor.t2", 20.0)), {}), T0, RoomState.initial(CFG))
    assert st.active_sensor == "sensor.t2"

def test_small_change_below_delta_does_not_write() -> None:
    st, _ = started()                                   # helper: Start with t1=21.3
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.35)), T0, st)
    assert not any(isinstance(c, ScheduleDebounce) for c in cmds)

def test_change_debounces_then_writes_current_value() -> None:
    st, _ = started()
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.5)), T0, st)
    assert [c for c in cmds if isinstance(c, ScheduleDebounce)] == [ScheduleDebounce(5.0)] and executes(cmds) == []
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.7)), T0, st)   # re-arm
    assert [c for c in cmds if isinstance(c, ScheduleDebounce)] == [ScheduleDebounce(5.0)]
    st, cmds = logic().handle(DebounceElapsed(), T0 + timedelta(seconds=5), st)
    assert executes(cmds) == [("climate.a", SetRoomTemperature(21.7)), ("climate.b", SetRoomTemperature(21.7))]

def test_refresh_resends_after_interval() -> None:
    st, _ = started(); st = verified_value_write(st, at=T0)   # helper: feed Ok results so last_value_write = T0
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=19), st)
    assert executes(cmds) == []
    _, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert executes(cmds) == [("climate.a", SetRoomTemperature(21.3)), ("climate.b", SetRoomTemperature(21.3))]

def test_sensor_timeout_loses_source_then_regains() -> None:
    st, _ = started()
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=121), st)
    assert [i for _, i in executes(cmds)] == [SetSource("internal")] * 2 and st.source == Source.INTERNAL
    st, cmds = logic().handle(SensorChanged(reading("sensor.t1", 21.0, at=T0 + timedelta(minutes=121))), T0 + timedelta(minutes=121), st)
    assert executes(cmds)[:2] == [("climate.a", SetSource("external")), ("climate.b", SetSource("external"))]
    assert executes(cmds)[2:] == [("climate.a", SetRoomTemperature(21.0)), ("climate.b", SetRoomTemperature(21.0))]

def test_out_of_range_value_is_unusable() -> None:
    st, cmds = logic().handle(Start((reading("sensor.t1", 100.0), reading("sensor.t2", None)), {}), T0, RoomState.initial(CFG))
    assert st.source == Source.INTERNAL

def test_room_without_sensors_never_touches_source() -> None:
    cfg = replace(CFG, temp_sensors=())
    st, cmds = RoomLogic(cfg, GlobalParams.defaults(), capability_of=...).handle(Start((), {}), T0, RoomState.initial(cfg))
    assert not any(isinstance(i, (SetSource, SetRoomTemperature)) for _, i in executes(cmds)) and cmds[-1].status == RoomStatus.OK
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `model.py` additions and `room_logic.py` for these events** (`Start`, `SensorChanged`, `DebounceElapsed`, `Tick` refresh/timeout). Unknown events raise `NotImplementedError` for now. Settings intents are emitted only when `window_detection != "leave"` / `hysteresis is not None`, once (`settings_applied`). Source-regain re-emits settings for nothing; only `SetSource` + `SetRoomTemperature`.
- [ ] **Step 4: Run `uv run pytest tests/core -v`** → PASS. `uv run mypy` → clean.
- [ ] **Step 5: Commit** — `git commit -m "feat(core): room logic for sensor selection, source switching and refresh"`

---

### Task 6: Room logic, part 2 — results, retries, fail-safe, availability, issues

**Files:**
- Modify: `custom_components/easy_trv/core/room_logic.py`
- Test: `tests/core/test_room_logic.py` (append)

**Interfaces:**
- `OperationResult` handling: `Ok` → clear `attempts[cap]`, `pending_retry[cap]`; if health was `ERROR` → health `OK`, `ClearIssue("unreachable")`, `FireEvent(EVENT_RECOVERED, …)`, `Log("info")`. For `room_temperature`: `last_value_write = now`, `last_written = intent.celsius`. `Mismatch`/`Failed` → `attempts[cap] += 1`; if `< max_attempts` → `pending_retry[cap] = intent`, `ScheduleRetry(trv, cap, backoff[min(n−1, len−1)])`; else health `ERROR`, `last_error`, `RaiseIssue("unreachable", trv, {room, trv, attribute: cap, attempts, error})`, `FireEvent(EVENT_WRITE_FAILED, …)`, `Log("warning")` only on the OK→ERROR transition. `Unsupported` → `unsupported |= {cap}`, health `UNSUPPORTED` (unless ERROR), `RaiseIssue("firmware", …)`, no retry. `Signal("failsafe")` → `failsafe_seen_at = now`, `FireEvent(EVENT_FAILSAFE)`, `Log("info")`, `Execute(SetSource("external"))` + `Execute(SetRoomTemperature(current))` for that trv; attempts untouched.
- `RetryDue(trv, cap)` → if `pending_retry[cap]` exists: pop and `Execute(trv, it)`; else nothing.
- A new `Execute` for `(trv, cap)` while a retry is pending replaces `pending_retry[cap]` (latest wins).
- `TrvAvailabilityChanged(False)` → health `UNAVAILABLE` (keep attempts); `Execute`s for that trv are suppressed while unavailable. `TrvAvailabilityChanged(True)` → health `OK` (or `UNSUPPORTED`/`ERROR` if flagged) and resync: `SetSource(current source)` if source != NONE, then `SetRoomTemperature(current)` if EXTERNAL.
- Event payload: `{"room": cfg.name, "room_id": cfg.room_id, "trv_entity_id": trv, "ieee": …}` plus `attribute`, `attempts`, `error` for failures.

- [ ] **Step 1: Write the failing tests**

```python
def test_failed_write_retries_with_backoff_then_raises_issue() -> None:
    st, _ = started(); it = SetRoomTemperature(21.3)
    st, cmds = logic().handle(OperationResult("climate.a", it, "room_temperature", Failed("timeout")), T0, st)
    assert [c for c in cmds if isinstance(c, ScheduleRetry)] == [ScheduleRetry("climate.a", "room_temperature", 5.0)]
    st, cmds = logic().handle(RetryDue("climate.a", "room_temperature"), T0, st)
    assert executes(cmds) == [("climate.a", it)]
    st, cmds = logic().handle(OperationResult("climate.a", it, "room_temperature", Failed("timeout")), T0, st)
    assert [c.seconds for c in cmds if isinstance(c, ScheduleRetry)] == [15.0]
    st, cmds = logic().handle(OperationResult("climate.a", it, "room_temperature", Mismatch(frozenset({2130}), 2100)), T0, st)
    assert any(isinstance(c, RaiseIssue) and c.kind == "unreachable" and c.placeholders["attempts"] == "3" for c in cmds)
    assert any(isinstance(c, FireEvent) and c.name == EVENT_WRITE_FAILED for c in cmds)
    assert cmds[-1].status == RoomStatus.ERROR and st.trvs["climate.a"].health == TrvHealth.ERROR

def test_recovery_clears_issue_and_fires_event() -> None:
    st = errored("climate.a")                                            # helper: three failures
    st, cmds = logic().handle(OperationResult("climate.a", SetRoomTemperature(21.3), "room_temperature", Ok()), T0, st)
    assert any(isinstance(c, ClearIssue) for c in cmds) and any(isinstance(c, FireEvent) and c.name == EVENT_RECOVERED for c in cmds)
    assert cmds[-1].status == RoomStatus.OK

def test_failsafe_signal_reenables_without_counting_attempt() -> None:
    st, _ = started()
    st, cmds = logic().handle(OperationResult("climate.b", SetRoomTemperature(21.3), "room_temperature", Signal("failsafe")), T0, st)
    assert executes(cmds) == [("climate.b", SetSource("external")), ("climate.b", SetRoomTemperature(21.3))]
    assert any(isinstance(c, FireEvent) and c.name == EVENT_FAILSAFE for c in cmds)
    assert st.trvs["climate.b"].attempts == {} and cmds[-1].status == RoomStatus.DEGRADED

def test_unsupported_raises_firmware_issue_no_retry() -> None:
    st, _ = started()
    st, cmds = logic().handle(OperationResult("climate.a", SetHysteresis(0.4), "hysteresis", Unsupported("hysteresis", "1.3.0", "1.2.1")), T0, st)
    assert not any(isinstance(c, ScheduleRetry) for c in cmds)
    assert any(isinstance(c, RaiseIssue) and c.kind == "firmware" for c in cmds) and st.trvs["climate.a"].health == TrvHealth.UNSUPPORTED

def test_unavailable_trv_is_skipped_then_resynced() -> None:
    st, _ = started()
    st, cmds = logic().handle(TrvAvailabilityChanged("climate.a", False), T0, st)
    assert cmds[-1].status == RoomStatus.DEGRADED
    st, cmds = logic().handle(Tick(), T0 + timedelta(minutes=20), st)
    assert all(t != "climate.a" for t, _ in executes(cmds))
    st, cmds = logic().handle(TrvAvailabilityChanged("climate.a", True), T0, st)
    assert executes(cmds) == [("climate.a", SetSource("external")), ("climate.a", SetRoomTemperature(21.3))]

def test_newer_intent_supersedes_pending_retry() -> None:
    st, _ = started()
    st, _ = logic().handle(OperationResult("climate.a", SetRoomTemperature(21.3), "room_temperature", Failed("x")), T0, st)
    st, _ = logic().handle(SensorChanged(reading("sensor.t1", 22.0)), T0, st)
    st, _ = logic().handle(DebounceElapsed(), T0, st)
    st, cmds = logic().handle(RetryDue("climate.a", "room_temperature"), T0 + timedelta(seconds=5), st)
    assert executes(cmds) in ([], [("climate.a", SetRoomTemperature(22.0))])   # never 21.3

def test_flapping_sensor_single_write() -> None:
    st, _ = started()
    st, c1 = logic().handle(SensorChanged(reading("sensor.t1", None)), T0, st)
    st, c2 = logic().handle(SensorChanged(reading("sensor.t1", 21.6)), T0 + timedelta(seconds=1), st)
    st, c3 = logic().handle(SensorChanged(reading("sensor.t1", None)), T0 + timedelta(seconds=2), st)
    st, c4 = logic().handle(SensorChanged(reading("sensor.t1", 21.6)), T0 + timedelta(seconds=3), st)
    st, c5 = logic().handle(DebounceElapsed(), T0 + timedelta(seconds=8), st)
    sources = [i for c in (c1, c2, c3, c4, c5) for _, i in executes(c) if isinstance(i, SetSource)]
    assert sources == []                                        # t2 (20.0) covers the gaps: no source toggling
    assert executes(c5) == [("climate.a", SetRoomTemperature(21.6)), ("climate.b", SetRoomTemperature(21.6))]
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** the remaining event handlers.
- [ ] **Step 4: Run `uv run pytest tests/core -v && uv run mypy`** → PASS, clean.
- [ ] **Step 5: Commit** — `git commit -m "feat(core): operation results, retries, fail-safe handling and availability"`

---

### Task 7: ZHA adapter (`adapters/zha_gateway.py`)

**Files:**
- Create: `custom_components/easy_trv/adapters/zha_gateway.py`, `tests/fake_gateway.py`
- Test: `tests/adapters/test_zha_gateway.py`

**Interfaces:**
- `@dataclass(frozen=True) class DeviceRef: entity_id: str; device_id: str; ieee: str; manufacturer: str; model: str; firmware: Version | None`.
- `class GatewayError(Exception)`; `class ZhaUnavailable(GatewayError)`; `class NotZhaEntity(GatewayError)`.
- `def resolve_device(hass: HomeAssistant, climate_entity_id: str) -> DeviceRef` — entity registry entry must exist with `platform == "zha"` and a `device_id`; device registry entry gives `manufacturer`, `model`, `sw_version` (→ `values.parse_zha_sw_version`) and the `("zha", ieee)` identifier; raises `NotZhaEntity`.
- `class TrvGateway(Protocol)`: `resolve(self, climate_entity_id: str) -> DeviceRef`; `is_available(self, ieee: str) -> bool`; `async write(self, ieee: str, endpoint: int, cluster: int, attribute: int, value: int) -> None`; `async read(self, ieee: str, endpoint: int, cluster: int, attributes: Sequence[int]) -> Mapping[int, int]`.
- `class ZhaLibraryGateway`: `__init__(hass)` imports `get_zha_gateway` from `homeassistant.components.zha.helpers` and `EUI64` from `zigpy.types`, calls `get_zha_gateway(hass)` once; any exception → `ZhaUnavailable`. `_device(ieee)` → `gateway.get_device(EUI64.convert(ieee))`, `None` → `GatewayError`. `write` → `await device.write_zigbee_attribute(endpoint, cluster, attribute, value)` (no manufacturer argument); `read` → `cluster = device.async_get_cluster(endpoint, cluster_id)`; `success, failure = await cluster.read_attributes(list(attributes), allow_cache=False, only_cache=False)`; missing/failed attribute → `GatewayError(f"read failed for 0x{attr:04X}: {failure}")`; values cast with `int()`. All exceptions from zha/zigpy wrapped in `GatewayError(str(err))`.
- `tests/fake_gateway.py`: `class FakeGateway` implementing `TrvGateway` with `devices: dict[str, DeviceRef]`, `availability: dict[str, bool]`, `store: dict[tuple[str, int, int, int], int]`, `writes: list[tuple[str, int, int, int, int]]`, `reads: list[…]`, `fail_write: dict[tuple[str, int], str]` (ieee, attribute → error message, raised once then cleared), `read_override: dict[tuple[str, int], int]` (returned instead of the stored value). `write` stores the value unless failing; `read` returns `read_override` or stored values (missing → `GatewayError`).

- [ ] **Step 1: Write the failing tests**

```python
async def test_resolve_device_from_registries(hass, device_registry, entity_registry) -> None:
    entry = MockConfigEntry(domain="zha"); entry.add_to_hass(hass)
    dev = device_registry.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("zha", "00:12:4b:00:aa:bb:cc:dd")}, manufacturer="SONOFF", model="TRVZB", sw_version="0x00001300")
    entity_registry.async_get_or_create("climate", "zha", "uid1", suggested_object_id="living_trv", device_id=dev.id)
    ref = zha_gateway.resolve_device(hass, "climate.living_trv")
    assert ref == DeviceRef("climate.living_trv", dev.id, "00:12:4b:00:aa:bb:cc:dd", "SONOFF", "TRVZB", (1, 3, 0))

async def test_resolve_rejects_non_zha(hass, entity_registry) -> None:
    entity_registry.async_get_or_create("climate", "mqtt", "uid2", suggested_object_id="other")
    with pytest.raises(zha_gateway.NotZhaEntity):
        zha_gateway.resolve_device(hass, "climate.other")

async def test_library_gateway_unavailable_when_zha_not_loaded(hass) -> None:
    with patch("homeassistant.components.zha.helpers.get_zha_gateway", side_effect=KeyError("zha")):
        with pytest.raises(zha_gateway.ZhaUnavailable):
            zha_gateway.ZhaLibraryGateway(hass)

async def test_library_gateway_write_and_read_wrap_zha(hass) -> None:
    device = MagicMock(); device.write_zigbee_attribute = AsyncMock()
    cluster = MagicMock(); cluster.read_attributes = AsyncMock(return_value=({0x600D: 2140, 0x600E: 3}, {}))
    device.async_get_cluster.return_value = cluster
    gw_obj = MagicMock(); gw_obj.get_device.return_value = device
    with patch("homeassistant.components.zha.helpers.get_zha_gateway", return_value=gw_obj):
        gw = zha_gateway.ZhaLibraryGateway(hass)
        await gw.write("00:12:4b:00:aa:bb:cc:dd", 1, 0xFC11, 0x600D, 2140)
        assert await gw.read("00:12:4b:00:aa:bb:cc:dd", 1, 0xFC11, [0x600D, 0x600E]) == {0x600D: 2140, 0x600E: 3}
    device.write_zigbee_attribute.assert_awaited_once_with(1, 0xFC11, 0x600D, 2140)
    cluster.read_attributes.assert_awaited_once_with([0x600D, 0x600E], allow_cache=False, only_cache=False)

async def test_library_gateway_read_failure_raises(hass) -> None:
    ...  # failure dict {0x600D: "UNSUPPORTED_ATTRIBUTE"} → GatewayError

def test_fake_gateway_contract() -> None:
    ...  # FakeGateway: write then read returns value; fail_write raises once; read_override wins
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `zha_gateway.py` and `tests/fake_gateway.py`.** Verify the real attribute names `write_zigbee_attribute` / `async_get_cluster` against the installed `zha` package (`uv run python -c "import zha.zigbee.device as d; print(d.Device.write_zigbee_attribute, d.Device.async_get_cluster)"`); if a name differs, adapt the adapter and the test patch target, not the Protocol.
- [ ] **Step 4: Run `uv run pytest tests/adapters -v`** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(adapters): ZHA gateway adapter with registry-based device resolution"`

---

### Task 8: Room controller (`controller.py`, `issues.py`)

**Files:**
- Create: `custom_components/easy_trv/controller.py`, `custom_components/easy_trv/issues.py`
- Test: `tests/test_controller.py`

**Interfaces:**
- `issues.py`: `issue_id_unreachable(ieee) -> str`, `issue_id_firmware(ieee) -> str`, `raise_unreachable(hass, ieee, placeholders)`, `clear_unreachable(hass, ieee)`, `raise_firmware(hass, ieee, placeholders)`, `clear_firmware(hass, ieee)`, `raise_zha_api(hass, error: str)`, `clear_zha_api(hass)`, `raise_profile_invalid(hass, file: str, error: str)`, `clear_profile_issues(hass, keep: set[str])`. Translation keys `trv_unreachable`, `trv_firmware`, `zha_api_incompatible`, `profile_invalid`; severities ERROR, WARNING, ERROR, WARNING; all `is_fixable=False`, `is_persistent=False`.
- `class RoomController`: `__init__(hass: HomeAssistant, config: RoomConfig, params: GlobalParams, profile: Profile, gateway: TrvGateway, device_refs: Mapping[str, DeviceRef])`; `async def async_start() -> None` (subscribe `async_track_state_change_event` to `temp_sensors + trv entity_ids`, tick via `async_track_time_interval(TICK_SECONDS)`, start worker task, dispatch `Start` built from current states); `async def async_stop() -> None` (cancel timers, worker, listeners); `status: RoomStatus` and `attributes: dict[str, Any]` properties; `def add_listener(cb: Callable[[], None]) -> Callable[[], None]`; `def snapshot() -> dict[str, Any]` (for diagnostics: config, state, params as JSON-able dict).
- Internals: `_reading(entity_id) -> SensorReading` from `hass.states.get` via `values.parse_temperature(state.state, state.attributes.get("unit_of_measurement"))` and `state.last_reported`; a non-°C unit logs one warning per entity (`_warned_units: set[str]`). `_trv_available(entity_id) -> bool` = climate state exists and not `unavailable`. `_dispatch(event)` runs the logic under `now = dt_util.utcnow()` and executes commands: `Execute` → `queue.put_nowait`; `ScheduleDebounce` → cancel + `async_call_later`; `ScheduleRetry` → `async_call_later` → `_dispatch(RetryDue)`; `SetStatus` → store, notify listeners if changed; `RaiseIssue`/`ClearIssue` → `issues.*` with `ieee` from `device_refs`; `FireEvent` → `hass.bus.async_fire(name, data)`; `Log` → logger.
- `_run_operation(trv, intent)`: `profile.plan(intent, firmware)` (`CapabilityUnsupported` → `Unsupported` outcome; `ValueOutOfRange` → `Failed`); `gateway.is_available(ieee)` false → `Failed("device unavailable")`; `async with asyncio.timeout(OPERATION_TIMEOUT): await gateway.write(...)`; `await asyncio.sleep(params.verify_delay)`; `async with asyncio.timeout(OPERATION_TIMEOUT): values = await gateway.read(ieee, endpoint, cluster, [attribute, *also_read])`; outcome: `op.signals.get(values[attribute])` or any `op.extra_signals[a].get(values[a])` → `Signal`; `values[attribute] in op.accept` → `Ok`; else `Mismatch`; `GatewayError`/`TimeoutError` → `Failed(str)`. Then `_dispatch(OperationResult(...))`.
- The worker processes the queue strictly one at a time.

- [ ] **Step 1: Write the failing tests** (fixtures: `hass`, `freezer`; `FakeGateway` with two TRVs `climate.a`/`climate.b`, sensors `sensor.t1`/`sensor.t2` set via `hass.states.async_set` with `unit_of_measurement: "°C"`; `params = GlobalParams.defaults()` but `verify_delay=0.0` unless stated)

```python
async def test_start_writes_settings_source_and_value_in_order(hass, freezer, controller, gateway) -> None:
    await controller.async_start(); await hass.async_block_till_done()
    assert [(w[0], w[3], w[4]) for w in gateway.writes] == [
        ("00:11", 0x6000, 1), ("00:11", 0x6011, -40), ("00:22", 0x6000, 1), ("00:22", 0x6011, -40),
        ("00:11", 0x600E, 1), ("00:22", 0x600E, 1), ("00:11", 0x600D, 2130), ("00:22", 0x600D, 2130)]
    assert controller.status == RoomStatus.OK and controller.attributes["active_sensor"] == "sensor.t1"

async def test_sensor_change_debounced_and_written(hass, freezer, controller, gateway) -> None:
    await controller.async_start(); await hass.async_block_till_done(); gateway.writes.clear()
    hass.states.async_set("sensor.t1", "21.8", {"unit_of_measurement": "°C"}); await hass.async_block_till_done()
    assert gateway.writes == []
    freezer.tick(timedelta(seconds=5)); async_fire_time_changed(hass); await hass.async_block_till_done()
    assert [w[4] for w in gateway.writes] == [2180, 2180]

async def test_verify_delay_and_mismatch_retry(hass, freezer, controller_with_delay, gateway) -> None:
    gateway.read_override[("00:11", 0x600D)] = 2000
    await controller_with_delay.async_start()        # verify_delay = 10
    # advance 10 s → read-back mismatch for climate.a → retry scheduled at +5 s; climate.b verifies fine
    ...
    assert controller_with_delay.attributes["trv_health"]["climate.a"] == "error" after three mismatches
    assert any(i.issue_id == "trv_unreachable_0011" for i in ir.async_get(hass).issues.values())

async def test_failsafe_readback_reenables(hass, controller, gateway, events) -> None:
    await controller.async_start(); await hass.async_block_till_done(); gateway.writes.clear()
    gateway.read_override[("00:22", 0x600E)] = 2
    freezer.tick(timedelta(minutes=20)); async_fire_time_changed(hass); await hass.async_block_till_done()
    assert ("00:22", 0x600E, 1) in [(w[0], w[3], w[4]) for w in gateway.writes]
    assert [e.event_type for e in events] == ["easy_trv_failsafe_detected"]

async def test_unit_warning_logged_once(hass, controller, caplog) -> None:
    hass.states.async_set("sensor.t1", "21.3", {"unit_of_measurement": "K"})
    await controller.async_start(); await hass.async_block_till_done()
    hass.states.async_set("sensor.t1", "21.4", {"unit_of_measurement": "K"}); await hass.async_block_till_done()
    assert caplog.text.count("unsupported unit") == 1

async def test_stop_cancels_everything(hass, controller) -> None:
    await controller.async_start(); await controller.async_stop()
    hass.states.async_set("sensor.t1", "25.0", {"unit_of_measurement": "°C"}); await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=30)); async_fire_time_changed(hass); await hass.async_block_till_done()
    assert gateway.writes == []   # after clearing post-start writes
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement `issues.py` and `controller.py`.**
- [ ] **Step 4: Run `uv run pytest tests/test_controller.py -v`** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: room controller with serial verified writes, retries and repairs issues"`

---

### Task 9: Integration setup, status sensor, diagnostics

**Files:**
- Modify: `custom_components/easy_trv/__init__.py`
- Create: `custom_components/easy_trv/sensor.py`, `custom_components/easy_trv/diagnostics.py`, `custom_components/easy_trv/strings.json` (entity + issues part), `custom_components/easy_trv/translations/en.json`, `custom_components/easy_trv/icons.json`
- Test: `tests/test_init.py`, `tests/test_sensor.py`, `tests/test_diagnostics.py`

**Interfaces:**
- `__init__.py`: `@dataclass class EasyTrvRuntimeData: gateway: TrvGateway; profiles: LoadResult; rooms: dict[str, RoomController]`; `type EasyTrvConfigEntry = ConfigEntry[EasyTrvRuntimeData]`; `async_setup_entry(hass, entry) -> bool`: build gateway (`ZhaUnavailable` → `issues.raise_zha_api` + `raise ConfigEntryNotReady`), `issues.clear_zha_api`; `profiles = await async_load_profiles(hass)` (errors → `issues.raise_profile_invalid`, `clear_profile_issues(keep=…)`); for each `room` subentry: resolve each TRV via `gateway.resolve`, pick profile (`select_profile(profiles, subentry.data[CONF_PROFILE], device_refs) -> Profile`; `auto` = first builtin matching every TRV; raises `ConfigEntryError` when none), build `RoomConfig` (`room_id=subentry.subentry_id`, `name=subentry.title`), `GlobalParams.from_options(entry.options)`, create `RoomController`; `entry.runtime_data = …`; `await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)`; `async_at_started(hass, _start_rooms)` starts controllers; `entry.async_on_unload(entry.add_update_listener(_async_reload))`. `async_unload_entry`: stop controllers, unload platforms. `GlobalParams.from_options(options: Mapping) -> GlobalParams` lives in `core/model.py` (sensor_timeout 0 → None).
- Gateway construction is behind a module-level factory `GATEWAY_FACTORY: Callable[[HomeAssistant], TrvGateway] = ZhaLibraryGateway` so tests patch `custom_components.easy_trv.GATEWAY_FACTORY` (and `resolve_device` is used through the gateway).
- `sensor.py`: `async_setup_entry(hass, entry, async_add_entities)` adds one `RoomStatusSensor(controller, subentry)` per room with `config_subentry_id=subentry.subentry_id`. `RoomStatusSensor(SensorEntity)`: `_attr_has_entity_name = True`, `_attr_translation_key = "room_status"`, `_attr_device_class = SensorDeviceClass.ENUM`, `_attr_options = ["ok", "degraded", "error"]`, `_attr_entity_category = EntityCategory.DIAGNOSTIC`, `_attr_should_poll = False`, `_attr_unique_id = f"{subentry_id}_status"`, `_attr_device_info = DeviceInfo(identifiers={(DOMAIN, subentry_id)}, name=subentry.title, manufacturer="Easy TRV")`; `native_value = controller.status.value`; `extra_state_attributes = controller.attributes`; subscribes via `controller.add_listener(self.async_write_ha_state)` in `async_added_to_hass`.
- `diagnostics.py`: `async_get_config_entry_diagnostics(hass, entry) -> dict` = `{"options": dict(entry.options), "profiles": [ids], "profile_errors": [...], "rooms": {subentry_id: controller.snapshot()}}`.
- `strings.json`/`en.json`: `entity.sensor.room_status.name = "Status"`, `.state.{ok: "OK", degraded: "Degraded", error: "Error"}`; `issues.trv_unreachable.title = "Thermostat {trv} in {room} is not responding"`, `.description` (attempts/attribute/error placeholders, advice: battery, Zigbee range, re-pair; auto-resolves), `issues.trv_firmware` (`{trv}`, `{capability}`, `{required}`, `{installed}`), `issues.zha_api_incompatible` (`{error}`), `issues.profile_invalid` (`{file}`, `{error}`). `icons.json`: `entity.sensor.room_status.default = "mdi:radiator"`, `state.error = "mdi:radiator-off"`.
- Test fixture `make_entry(hass, rooms: list[dict]) -> MockConfigEntry` using `subentries_data=[ConfigSubentryData(data=room, subentry_type="room", title=room["name"], unique_id=room["name"].casefold())]`; patch `GATEWAY_FACTORY` to return a `FakeGateway` pre-filled with `DeviceRef`s for `climate.a`/`climate.b` (`SONOFF`/`TRVZB`, firmware `(1,3,0)`).

- [ ] **Step 1: Write the failing tests**

```python
async def test_setup_creates_room_device_and_status_sensor(hass, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id); await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED); await hass.async_block_till_done()
    state = hass.states.get("sensor.living_status")
    assert state is not None and state.state == "ok" and state.attributes["source"] == "external"
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.subentries_ids[0])})
    assert dev is not None and dev.name == "Living"

async def test_setup_zha_unavailable_retries_and_clears_issue(hass, entry) -> None:
    with patch("custom_components.easy_trv.GATEWAY_FACTORY", side_effect=ZhaUnavailable("no zha")):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert ir.async_get(hass).async_get_issue(DOMAIN, "zha_api_incompatible") is not None
    # now with a working factory
    ...
    assert ir.async_get(hass).async_get_issue(DOMAIN, "zha_api_incompatible") is None

async def test_profile_auto_selects_builtin_and_rejects_unmatched(hass, entry_with_foreign_trv) -> None:
    assert not await hass.config_entries.async_setup(entry_with_foreign_trv.entry_id)
    assert entry_with_foreign_trv.state is ConfigEntryState.SETUP_ERROR

async def test_options_change_reloads(hass, entry) -> None: ...
async def test_unload(hass, entry) -> None: ...
async def test_diagnostics_contains_rooms(hass, hass_client, entry) -> None:
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert set(diag["rooms"]) == set(entry.subentries) and diag["profiles"] == ["sonoff_trvzb_native"]
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** Use `homeassistant.helpers.start.async_at_started`. The `subentries_ids` helper in the test is `list(entry.subentries)`.
- [ ] **Step 4: Run `uv run pytest tests/test_init.py tests/test_sensor.py tests/test_diagnostics.py -v`** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: config entry setup, room status sensor and diagnostics"`

---

### Task 10: Config flow, room subentry flow, options flow, translations

**Files:**
- Create: `custom_components/easy_trv/config_flow.py`
- Modify: `strings.json`, `translations/en.json`; Create: `translations/de.json`
- Test: `tests/test_config_flow.py`

**Interfaces:**
- `EasyTrvConfigFlow(ConfigFlow, domain=DOMAIN)`: `VERSION = 1`; `async_step_user` → `async_set_unique_id(DOMAIN)`, `_abort_if_unique_id_configured()`, `async_create_entry(title="Easy TRV", data={})`; `async_get_options_flow` → `EasyTrvOptionsFlow()`; `async_get_supported_subentry_types` → `{SUBENTRY_ROOM: RoomSubentryFlow}`.
- `RoomSubentryFlow(ConfigSubentryFlow)`: `async_step_user` shows a menu (`async_show_menu`) with `from_area` / `custom`; `async_step_from_area` (AreaSelector filtered `entity=[{"integration": "zha", "domain": "climate"}]`) → stores `name = area.name`, `area_id`, prefill via `prefill_from_area(hass, area_id) -> dict[str, list[str]]` (entity-registry entries of the area plus entities whose device is in the area; `trvs` = `climate` with platform `zha`; `temp_sensors` = `sensor` with `original_device_class == "temperature"` and not belonging to a selected TRV's device; `humidity_sensors` analogous); `async_step_custom` (TextSelector `name`); both → `async_step_entities` with schema: `trvs` (EntitySelector multiple, filter `{integration: zha, domain: climate}`), `temp_sensors` (multiple, `reorder=True`, filter `{domain: sensor, device_class: temperature}`), `humidity_sensors` (multiple, filter `{domain: sensor, device_class: humidity}`, default `[]`), `profile` (SelectSelector with options `auto` + loaded profile ids, default `auto`), `window_detection` (SelectSelector `on`/`off`/`leave`, default `on`, translation_key), `hysteresis` (SelectSelector `leave`/`0.2`/`0.4`/`0.6`/`0.8`/`1.0`, default `leave`). Validation via `validate_room(hass, entry, subentry_id | None, user_input) -> dict[str, str]` (errors): `no_trv`, `not_zha` (resolve fails), `no_profile` (auto finds no profile matching all TRVs), `profile_mismatch` (explicit profile does not match a TRV), `trv_in_other_room` (entity already in another room subentry of the entry). Create: `async_create_entry(title=name, data={name, area_id, trvs, temp_sensors, humidity_sensors, profile, window_detection, hysteresis}, unique_id=name.casefold())`; `async_step_reconfigure` shows one form with `name` + the entities schema prefilled from the subentry and ends with `async_update_and_abort(self._get_entry(), subentry, data_updates=user_input, title=user_input["name"])`.
- `EasyTrvOptionsFlow(OptionsFlow)`: `async_step_init` with NumberSelectors for the five global options (ranges from Global Constraints; `sensor_timeout_minutes` 0–720 with `0` allowed) → `async_create_entry(data=user_input)`.
- `strings.json` additions: `config.step.user.title`, `config.abort.single_instance_allowed`, `options.step.init.{title,data,data_description}`, `config_subentries.room.{title, entry_type, initiate_flow.user: "Add room", initiate_flow.reconfigure: "Edit room", step.user.menu_options.{from_area,custom}, step.from_area.data.area_id, step.custom.data.name, step.entities.{title,data,data_description}, step.reconfigure.title, error.{no_trv,not_zha,no_profile,profile_mismatch,trv_in_other_room}, abort.reconfigure_successful}`, `selector.window_detection.options.{on,off,leave}`, `selector.hysteresis.options.*`. `de.json` translates everything (UI language German: "Raum hinzufügen", "Raum bearbeiten", "Aus Bereich", "Eigener Raum", "Thermostate", "Temperatursensoren (Reihenfolge = Priorität)", "Feuchtesensoren", "Profil", "Fenstererkennung", "Hysterese", "Auffrischung (Minuten)", "Rücklese-Verzögerung (Sekunden)", "Versuche", "Mindeständerung (K)", "Sensor-Timeout (Minuten, 0 = aus)").

- [ ] **Step 1: Write the failing tests**

```python
async def test_user_flow_creates_single_entry(hass) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Easy TRV"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "single_instance_allowed"

async def test_room_from_area_prefills_and_creates(hass, entry, area_with_trvs) -> None:
    result = await hass.config_entries.subentries.async_init((entry.entry_id, "room"), context={"source": "user"})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"next_step_id": "from_area"})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"area_id": area_with_trvs.id})
    assert result["step_id"] == "entities"
    suggested = {k.description["suggested_value"] for k in result["data_schema"].schema if k == "trvs"}  # or inspect schema defaults
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {"trvs": ["climate.a", "climate.b"], "temp_sensors": ["sensor.t1"], "humidity_sensors": [], "profile": "auto", "window_detection": "on", "hysteresis": "0.4"})
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["title"] == "Living"
    sub = next(iter(entry.subentries.values()))
    assert sub.data["trvs"] == ["climate.a", "climate.b"] and sub.data["hysteresis"] == "0.4"

async def test_room_rejects_trv_from_other_room(hass, entry_with_room) -> None:
    ...  # second room selecting climate.a → errors == {"trvs": "trv_in_other_room"}

async def test_room_rejects_non_zha_and_unmatched_profile(hass, entry) -> None:
    ...  # climate.mqtt_trv → not_zha; TRV with model "TRV-ZBT" → no_profile

async def test_reconfigure_updates_room(hass, entry_with_room) -> None:
    ...  # change temp_sensors order → subentry.data updated, flow aborts with reconfigure_successful

async def test_options_flow_sets_values(hass, entry) -> None:
    ...  # refresh_minutes 30, sensor_timeout_minutes 0 → entry.options

def test_translation_files_consistent() -> None:   # tests/test_translations.py
    strings = json.load(open("custom_components/easy_trv/strings.json")); en = json.load(open(".../translations/en.json")); de = json.load(open(".../translations/de.json"))
    assert strings == en and keys(strings) == keys(de)
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** If the subentry test helper calls (`hass.config_entries.subentries.async_init/async_configure`) do not match the installed core, copy the pattern from `tests/components/mqtt/test_config_flow.py` of the matching core version (`uv run python -c "import homeassistant.const as c; print(c.__version__)"`).
- [ ] **Step 4: Run `uv run pytest tests/test_config_flow.py tests/test_translations.py -v`** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: config flow with room subentries from areas, options flow, de/en translations"`

---

### Task 11: Quality scale, README, on-device checklist, full validation

**Files:**
- Create: `custom_components/easy_trv/quality_scale.yaml`, `docs/testing-on-device.md`
- Modify: `README.md`, `docs/README.md`

- [ ] **Step 1: Write `quality_scale.yaml`** listing Bronze and Silver rules as `done`/`exempt` with a one-line reason each, plus Gold `diagnostics`, `entity-category`, `entity-translations`, `repair-issues`, `reconfiguration-flow`, `devices` as `done`.
- [ ] **Step 2: Write `docs/testing-on-device.md`**: install via HACS custom repository, add entry, add "Wohnzimmer" from area, expected first writes (observe in ZHA device page → Manage Zigbee device → cluster 0xFC11 values 0x600D/0x600E), fail-safe check (block writes for > 2 h by setting refresh to 90 and watching 0x600E), spikes S1–S4 with exact steps and what to record, where to look (status sensor, Repairs, events in Developer tools → Events).
- [ ] **Step 3: Update `README.md`**: installation (HACS custom repo), configuration walk-through, options table, status/events/issues reference, profiles section (where shipped profiles live, how to copy one into `<config>/easy_trv/profiles/`), supported devices, limitations (no window contacts in Phase 1).
- [ ] **Step 4: Run the whole suite and linters**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
Expected: clean; coverage of `custom_components/easy_trv/core`, `profiles`, `controller.py` ≥ 95 %.

- [ ] **Step 5: Commit and tag**

```bash
git add -A && git commit -m "docs: README, quality scale and on-device test checklist" && git tag v0.1.0-rc1
```

- [ ] **Step 6: Push to GitHub `ImSorry99/easy-trv` and confirm the three workflows pass** (hassfest, HACS action, lint, tests). Fix findings, commit, re-run.

---

## Self-review notes

- Spec coverage: R1–R4 → Tasks 9/10; T1–T6 → Tasks 5/6/8; F1–F5 → Tasks 3/4/9/10; B1–B4 → Tasks 6/8; V1–V3 → Tasks 9/10; P-principles → structure (core/profiles/adapters boundaries, mypy-enforced `homeassistant` import ban via Task 1 mypy `files` plus a grep check in Task 11 Step 4: `! grep -rl "homeassistant" custom_components/easy_trv/core custom_components/easy_trv/profiles/engine.py`).
- Type consistency: `Version` from `core/values.py` is used by `model.py`, `engine.py`, `zha_gateway.py`; capability names used by the core are the profile's capability keys (`room_temperature`? no — the controller passes `profile.capability_for`, which returns the YAML key: `external_temperature`, `source_select`, `window_detection_flag`, `hysteresis`). Task 5/6 tests use their own `capability_of` lambda; the event/issue placeholder `attribute` is the capability key either way.
- Review Focus items 1–5 have named tests in Tasks 2, 10, 6, 9, 4/6.
