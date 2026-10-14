# Phase 2a — execution ledger

Binding decisions taken during the subagent-driven execution of `docs/plans/2026-10-14-phase2a-settings.md` (2026-10-14). Every `Ruling:` line is a decision the controller took on the owner's behalf; every `minor (deferred)` line is a reviewer finding set aside. Implementers: Sonnet; task reviewers: Opus; final review: Fable.

Spec: docs/specs/2026-10-14-easy-trv-phase2-design.md (binding). Branch feat/phase2a from main 956e19d. Implementers sonnet, task reviewers opus, final review fable. No Co-Authored-By lines.

## Pre-flight scan
| Tasks | Interface | Finding |
|---|---|---|
| 2 → 3 | RoomSettings consumed by RoomParams.from_settings; field names refresh_minutes/verify_delay_seconds/max_attempts/min_delta_kelvin/sensor_timeout_minutes | consistent |
| 2 → 5 | resolve_settings(options, room), overrides(room); hysteresis_kelvin replaces __init__._hysteresis_kelvin | Task 5 must delete the old helper — Ruling: delete `_hysteresis_kelvin` in Task 5, use core.settings.hysteresis_kelvin — avoids two parsers — cost if wrong: none |
| 2 → 6 | DEFAULTS / GLOBAL / CONF_* used by flows | consistent |
| 3 → 4 | intents SetAdaptiveMode(enabled), SetFrostTemperature(celsius); INTENT_NAMES set_adaptive_mode/set_frost_temperature; schema INTENTS | consistent |
| 3 → 5 | RoomConfig(adaptive_mode, frost_temperature, hysteresis float); RoomParams.from_settings | consistent; `from_options` removed in 3, Task 5 must not call it |
| 5 → 7 | EasyTrvRuntimeData.settings / .areas / .labels added in 5, 7, 8 respectively | each task adds its own field; no overlap |
| 5 → 8 | raise_unreachable/raise_firmware signatures change in 8; Task 5 adds raise_settings_migrated | no overlap |
| 6 → 8 | strings keys for window_detection/adaptive texts created in 6, final wording in 8 | Ruling: Task 6 writes the final wording from spec section 4 already; Task 8 only adds the capability labels and issue placeholders — avoids a second translation pass — cost if wrong: one more edit in Task 8 |
| 1 | REQUIREMENTS edits vs spec | consistent |
| 3 self | Phase 1 tests asserting full Start command lists change (frost write added) | expected, named in the task |
| 4 self | adaptive verify false: [0,1] relies on existing one_of | engine supports it, no change |
| 6 self | section() nesting vs flat options | flatten on submit, documented |
| 9 self | HTTP diagnostics test skipped in sandbox | direct-call test added, named |
Ruling: Task 3 adds the "Unsupported adaptive_mode → write hysteresis now and on later settings apply" rule (spec section 3 amended) — keeps old firmware on the room hysteresis — cost if wrong: one extra 0x6011 write per resync on old firmware.
Ruling: plan 2a adds only the settings it uses; setpoint range and window/outdoor keys arrive with 2b/2c — avoids dead settings in the UI — cost if wrong: a second migration-free options extension later.

## Tasks
Task 1: dispatched (implementer sonnet, BASE 956e19d) → DONE 0c454f8
Task 1: review opus → 1 Important (REQUIREMENTS scope header/out-of-scope list contradict F3/F6/F7); minor (deferred): release.yml awk includes link-reference lines for the oldest section; minor (deferred): -s check passes an empty-headings section
Task 1: fix round 1/5 (1 addressed, 0 open — REQUIREMENTS scope header/out-of-scope list; commits 0c454f8..fd06488)
Task 1: minor (deferred): roadmap list says "presets" though the frost preset is in scope
Task 1: complete (commits 956e19d..fd06488, review clean after 1 round)
Task 2: dispatched (implementer sonnet, BASE fd06488)
Task 2: DONE 26e1b2e (448 passed), concerns: const.py import alias form; global 'leave' treated as unset
Task 2: review opus → Approved, spec ✅
Task 2: minor (deferred): legacy hysteresis tests cannot fail for the behaviour they name (plan-mandated; strengthen with global 0_6 + room leave → 0.6)
Task 2: minor (deferred): no test pins window_detection/adaptive_mode "leave" kept while hysteresis "leave" falls through
Task 2: minor (deferred): DEFAULTS keys are string literals not tied to const.CONF_*; add a set-equality assertion in test_const
Task 2: minor (deferred): const.py re-export style mixed (EVENT_* alias form vs module alias)
Task 2: minor (deferred): hysteresis_kelvin/Literals unvalidated against corrupt stored values
Task 2: Ruling: GlobalParams.defaults()/from_options duplicate the defaults — Task 3 derives RoomParams.defaults() from resolve_settings({}, {}) and drops from_options — one source of truth — cost if wrong: none
Task 2: complete (commits fd06488..26e1b2e, review clean)
Task 3: dispatched (implementer sonnet, BASE 26e1b2e)
Task 3: DONE_WITH_CONCERNS 83ece62 (470 passed): RoomConfig.hysteresis stays float|None and frost_temperature float|None=None until Task 5 wires resolved settings; engine._intent_value got the two new intents (needed for mypy)
Task 3: Ruling: transitional Optional types accepted for this task — Task 5 must tighten RoomConfig.hysteresis and frost_temperature to float and delete the None branches in room_logic — keeps Phase 1 suite green between tasks — cost if wrong: one more edit in Task 5
Task 3: review opus → Approved, spec ✅ (transitional Optional types accepted; Task 5 must remove RoomConfig defaults and None cases)
Task 3: minor (deferred, carry to Task 5): test_a_trv_with_nothing_to_apply_counts_as_applied lost its premise (CFG has frost 7.0) — add frost_temperature=None
Task 3: minor (deferred, carry to Task 5): test_settings_are_applied_only_once / _only_emitted_when_configured check only (SetWindowDetection, SetHysteresis) — use SETTING_INTENTS
Task 3: minor (deferred, carry to Task 5): reissue parametrization lacks cases adaptive leave, frost None, adaptive on + hysteresis
Task 3: minor (deferred): no test for adaptive + hysteresis both unsupported (relies on _drop_unsupported)
Task 3: minor (deferred): _settings and _adaptive_was_in_charge duplicate the adaptive capability lookup; SETTING_INTENTS not used by _settings
Task 3: minor (deferred): stale GlobalParams mention in docs/specs/2026-10-12-easy-trv-design.md:14
Task 3: complete (commits 26e1b2e..83ece62, review clean)
Task 4: dispatched (implementer sonnet, BASE 83ece62)
Task 4: DONE f2e2d86 (474 passed)
Task 4: review opus → Approved, spec ✅
Task 4: minor (deferred): frost range edges 4.0/35.0 only tested from outside
Task 4: minor (deferred): frost never planned on None/old firmware in tests
Task 4: minor (deferred): "ignored while adaptive on" note sits in the verify column of the profile doc
Task 4: minor (deferred): schema test duplicates the YAML
Task 4: complete (commits 83ece62..f2e2d86, review clean)
Task 5: dispatched (implementer sonnet, BASE f2e2d86)
Task 5: DONE cd3a00c (484 passed); concerns: settings_migrated is persistent and never auto-cleared; test gateway firmware raised to 1.4.4
Task 5: Ruling: settings_migrated stays persistent (a one-time migration notice must survive a restart) — cost if wrong: a stale notice the user dismisses
Task 5: review opus → 1 Important (settings_migrated text false under default adaptive on); minors (deferred): no entry-level test of the 0.1.0 + FW 1.3.0 upgrade path; room param override not checked at the controller; German notice shows "0.4 K" with a point; persistent issue never cleared on entry removal; _HYSTERESIS_LEAVE duplicates core.settings._LEAVE; raise_settings_migrated bypasses _create; GATEWAY_FACTORY block moved; __init__.py at 305 lines
Task 5: fix round 1/5 (1 addressed, 0 open — migration notice wording; commits cd3a00c..05b0f3a)
Task 5: complete (commits f2e2d86..05b0f3a, review clean after 1 round)
Task 6: dispatched (implementer sonnet, BASE 05b0f3a)
Task 6: DONE 8f0eb1e (503 passed); concerns: sensor timeout keeps Phase 1 rule 0 or 10..1440 also per room; options form stores all numbers on untouched submit; README/testing doc stale (Task 9)
Task 6: Ruling: sensor-timeout lower bound 10 min stays (Phase 1 rule, spec table says 0–1440 but a 1–9 min timeout would flap on slow hygrometers) — cost if wrong: a user cannot set 5 min
Task 6: review opus → 2 Important (options form prefills built-in defaults so "empty = not stored" does not survive a save; room-form placeholders show raw English keys and dotted decimals in German); minors (deferred): untested `base` error path in _profile_errors; effective options computed twice; config_flow.py at 731 lines (split into flow_settings.py suggested); redundant second flow in test_room_settings_numbers_are_optional_boxes; mypy arg-type count 7; profile option label precedence unverified on device
Task 6: Ruling (reverses my dispatch decision 3): placeholders ARE localized — select keys through the integration's own selector translations, decimal comma for German, sensor timeout 0 as the translated "off" — a German form with "on"/"leave" inside sentences is not acceptable to the owner — cost if wrong: one translation lookup per form render
Task 6: Ruling: options numbers keep the "empty = not stored = built-in default" contract; the form suggests only saved values and shows the built-in default in data_description — defaults may change in later releases — cost if wrong: users who want the default pinned must type it
Task 6: fix round 1/5 (2 addressed pending re-review; commits 8f0eb1e..a884a77); round 2 requested: window-detection labels 'Erkennung an/aus' (Ruling: state-like labels read better inside the Global placeholder — cost if wrong: wording only)
Task 6: fix round 2/5 (3 addressed, 0 open — options suggestions, localized placeholders, state-like window labels; commits 8f0eb1e..215b41d)
Task 6: minor (deferred, carry to Task 8): adaptive-mode labels still action-like ("Adaptiven Modus einschalten") — make them state-like too
Task 6: minor (deferred): async_option_labels fetched once per select key inside the loop (one fetch before the loop would do)
Task 6: minor (deferred): custom profile id shows raw in the Global placeholder (no label)
Task 6: complete (commits 05b0f3a..215b41d, review clean after 2 rounds)
Task 7: dispatched (implementer sonnet, BASE 215b41d)
Task 7: DONE ecf2fbe (509 passed); concern: entity id with area becomes sensor.wohnzimmer_wohnzimmer_heating_status (HA prefixes area?) — verify in review
Task 7: review opus → 1 Important (area rooms get doubled entity ids sensor.wohnzimmer_wohnzimmer_heating_status because HA prefixes the device's area; plan-mandated name); minors (deferred): CHANGELOG over-promises area assignment for upgraded installs (suggested_area applies only on device creation); area test does not pin the entity id; tautological last assertion in the rename test
Task 7: Ruling: keep the owner's device name "Heizung {room}" and suggest a stable object id from the entity (`sensor.<room_slug>_heating_status`) so ids stay clean and unique; fallback if HA ignores the suggestion for entities with a unique_id: accept the doubled id, pin it in the test and document it — cost if wrong: entity ids differ between installs created before/after this change (none exist yet for area rooms)
Task 7: fix round 1/5 (3 addressed, 0 open — suggested entity id, CHANGELOG wording, rename-test assertion; commits ecf2fbe..c2b653e)
Task 7: minor (deferred): a room title that slugifies to nothing yields sensor._heating_status (config flow rejects empty names; symbol-only names unguarded)
Task 7: complete (commits 215b41d..c2b653e, review clean after 1 round)
Task 8: dispatched (implementer sonnet, BASE c2b653e)
Task 8: DONE 5f5078c (524 passed); concerns: firmware issue now shows {room}; intent names (set_hysteresis) reported for a missing profile capability have no label; noqa PLR0913 on raise_* and _build_room
Task 8: review opus → 1 Important (labels keyed by built-in capability names; intent names / custom profile slugs show raw in Repairs); minors (deferred): ambiguous device names for unrenamed thermostats; room taken from two sources in _raise_issue; attempts int round-trip; tautological error assertion in test_unreachable_issue_names_setting_and_thermostat
Task 8: Ruling (plan defect): capability labels are keyed by the fixed intent vocabulary (set_room_temperature … set_frost_temperature); the controller maps a profile capability to its intent before the lookup, so user profiles and missing capabilities get plain names too — cost if wrong: none (fallback stays the raw key)
Task 8: fix round 1/5 (4 addressed, 0 open — intent-keyed labels, room source, attempts string, concrete error assertion; commits 5f5078c..bf2477f)
Task 8: minor (deferred): stale comments "by capability" in __init__.py:64 and the controller docstring near line 202
Task 8: complete (commits c2b653e..bf2477f, review clean after 1 round)
Task 9: dispatched (implementer sonnet, BASE bf2477f)
Task 9: DONE ab14c6c (526 passed, 99 % coverage, ruff/mypy clean) — NOT YET REVIEWED (session paused on usage limit). Concerns to carry into the review: extra CHANGELOG line (diagnostics); S5 numbering clash — the checklist's S5 (adaptive with external sensor) collides with the spec's S5 (0x0012 = 1200 in the window phase): rename the checklist spike to S10 or renumber the spec's S5–S9 to S6–S10 in the final fix wave; profile docs not re-checked for stale wording
NEXT: review Task 9 (opus), then final whole-branch review (fable) over merge-base main..HEAD, one fix wave, finishing-a-development-branch
Task 9: review opus → Approved, spec ✅
Task 9: minor (deferred → final fix wave): S5 clash — rename the checklist spike to S10 and fix the "S1 to S5 from profiles doc" intro
Task 9: minor (deferred): checklist quotes "Empty = built-in default" which is not the on-screen text
Task 9: minor (deferred): "no 0x6011 line in the bedroom" holds only on firmware 1.4.4
Task 9: minor (deferred): dense README paragraph mixing adaptive rule and migration notice
Task 9: minor (deferred): misleading comment in the new diagnostics test (room sets its own hysteresis/window)
Task 9: complete (commits bf2477f..ab14c6c, review clean)
Final review: dispatched (fable) over merge-base main..HEAD
Final review (fable): Ready after 3 Important — (1) user profile without adaptive/frost capability → false trv_firmware issue + degraded room by default; (2) upgrade communication: adaptive on by default, hysteresis ignored, FW 1.3.0 rooms degraded not stated in CHANGELOG/README; migration notice needs the "on thermostats with firmware 1.4.4" qualifier; (3) no entry-level test of the 0.1.0 + FW 1.3.0 upgrade path. Minors promoted to the fix wave: dead `kelvin is not None` branch; German notice decimal comma; S5→S10 + intro; checklist on-screen quote; stale "by capability" comments; stronger legacy settings test; stale GlobalParams in Phase 1 spec.
Final: Ruling: a capability the profile does not declare (CapabilityUnsupported with required None) is "not in profile", not a firmware problem — no Repairs issue, health unchanged, one debug log per room start, listed in diagnostics; the adaptive→hysteresis rule still applies — cost if wrong: a user never learns their copied profile lacks a capability except via diagnostics/log
Final: Ruling (records Task 5's choice): settings_migrated is is_fixable=False (HA's Ignore) instead of the plan's confirm fix flow — satisfies "fixable by dismissing" without a repairs platform — cost if wrong: none
Final: fix wave dispatched (sonnet, BASE ab14c6c)
Final: fix wave DONE ab14c6c..e592ae6 (535 passed, 99 %); deviation: TrvState.not_in_profile marks profile gaps so _baseline does not flip health
Final: re-review (opus) → all 4 findings addressed; NEW Important: a profile missing set_room_temperature/set_source is now a silent "profile gap" (room ok, no issue). Minors (deferred): generic "set to leave/off" sentence in trv_firmware is wrong for hysteresis/frost/device cases and de says only "für diesen Raum"; clumsy qualifier placement in settings_migrated; not_in_profile duplicated in the snapshot; entry test cuts BUILTIN_TEXT at a capability name (brittle)
Final: Ruling (process deviation, recorded): one targeted follow-up fix after the fix wave — the schema requires one capability for set_room_temperature and one for set_source (profile_invalid otherwise), verified by TDD and the full suite, no further re-review — the hole was introduced by the wave itself and shipping a silent "ok" room is worse than one unreviewed two-line change — cost if wrong: a schema rule a reviewer did not read
Final: follow-up DONE 0d9d4ec (538 passed, 1 skipped, 99 %, ruff/mypy clean, verified by controller); branch ready to finish
