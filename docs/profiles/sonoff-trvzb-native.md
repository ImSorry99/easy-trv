# Profile: Sonoff TRVZB (native)

`id = sonoff_trvzb_native` · match `SONOFF` / `TRVZB` on ZHA · minimum firmware **1.2.1** · shipped as `profiles/builtin/sonoff_trvzb_native.yaml` (the YAML is shown in `docs/concept/profiles.md`) · research: `docs/research/02-trvzb-zha-profil.md`

## Capabilities and operations

Cluster `0xFC11`, endpoint 1, **no manufacturer code** (the built-in ZHA quirk defines the attributes with `manufacturer_code=None`; Sonoff's own script and Zigbee2MQTT do the same).

| Capability | Intent | Attribute | Raw value | Verify (read-back) | Firmware |
|---|---|---|---|---|---|
| `EXTERNAL_TEMPERATURE` | `SetRoomTemperature(t)` | `0x600D external_temperature_sensor_value` | `int16s`, `round(t, 1) × 100`, valid 0–9990 | equals written value | ≥ 1.2.1 |
| `SOURCE_SELECT` | `SetSource(external)` | `0x600E external_temperature_sensor_enable` | `1` | read ∈ {1, 3}; read `2` = **fail-safe signal** | ≥ 1.2.1 |
| `SOURCE_SELECT` | `SetSource(internal)` | `0x600E` | `0` | equals 0 | ≥ 1.2.1 |
| `WINDOW_DETECTION_FLAG` | `ApplySettings(window_detection=on/off)` | `0x6000 open_window` | `1` / `0` | equals | all |
| `HYSTERESIS` | `ApplySettings(hysteresis=k)` | `0x6011 temperature_control_accuracy` | `int16s`, `−round(k × 100)`, allowed −100…−20 | equals | ≥ 1.3.0 |

Read-back of `0x600D` always includes `0x600E` in the same read so the fail-safe is seen on every refresh.

## Device facts the profile relies on

| Fact (verified in research 02) | Effect in the profile |
|---|---|
| Values outside 0–99.9 °C are silently ignored by the device. | Out-of-range temperatures are never written; the sensor counts as unusable. |
| After ~2 h without a write the device falls back to its internal sensor and reports `0x600E = 2`; the next write brings it back (`3`). Not documented by Sonoff, reported independently by several sources. | Refresh interval default 20 min; `2` on read-back triggers `SetSource(external)` + value. |
| The ZHA quirk exposes `0x600E` as a switch where 2 and 3 both show as "on". | The fail-safe is invisible in HA; only the raw read-back sees it. |
| With the external source active the device reports the external value as `local_temperature`; `climate.current_temperature` shows it. | Nice for the user; not usable for verification. |
| `0x6000` only enables the device's *own* open-window detection (drop > 1.5 K in 4.5 min → valve closed, target 12 °C, ends on rise, manual change or after 30 min). There is no input for an external window state. | Phase 1 "window" = this flag. Window contacts need another operation (off + restore, or timer mode `0x6014–0x6016`, firmware ≥ 1.4.0). |
| The TRVZB is a sleepy end device: writes are delivered on its next poll, reads may time out while it sleeps. | Verify delay 10 s, operation timeout 20 s, retries with backoff. |
| ZHA stores the OTA `current_file_version` as device `sw_version` (`0x00001404` = 1.4.4). | Firmware parsed as nibbles major.minor.patch; unparsable → no gating, rely on `UNSUPPORTED_ATTRIBUTE`. |
| The built-in quirk (HA ≥ 2026.3) already defines all attributes; a second custom quirk for the same model conflicts. | This profile ships no quirk. |

## Firmware map

| Version | Adds | Used by |
|---|---|---|
| 1.2.1 | external sensor, valve opening degree | profile minimum |
| 1.3.0 | hysteresis (`0x6011`) | `HYSTERESIS` |
| 1.4.0 / 1.4.1 | boost, timer (`0x6014–0x6016`), ±0.2 offset | roadmap (window via timer, boost preset) |
| 1.4.4 | adaptive mode (`0x6017`) | not driven by Easy TRV; user toggles it in ZHA |

OTA to 1.4.4 works through the zigpy OTA index on HA ≥ 2026.3; Sonoff's own OTA server only offers up to 1.4.1.

## Not used by this profile (yet)

`0x6014–0x6016` timer/boost, `0x6017` adaptive mode, `0x600B/0x600C` valve degrees, standard weekly schedule on cluster `0x0201` (`set_weekly_schedule`, `system_mode = auto`), `local_temperature_calibration`.

## Spikes on real hardware

| Id | Question | Why |
|---|---|---|
| S1 | Does a write to `0x600D` switch a thermostat that is `off` back on? | T6 keeps writing regardless of mode; if the device wakes, the requirement needs a second look |
| S2 | Does the device *report* `0x600E = 2` on fail-safe, or only answer reads? | If it reports, the fail-safe can be caught immediately instead of at the next refresh |
| S3 | Does the built-in open-window detection trigger while the external source is active and the hygrometer reports slowly? | Validates the Phase 1 window decision |
| S4 | Confirm endpoint 1 and exact read-back of `0x600D` after writing 2135 and 2140 | Verification compares exact integers |
