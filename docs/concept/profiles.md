# Device profiles

Easy TRV never talks to a thermostat directly from its room logic. It talks to a **profile**. This is what makes principle P2 in `REQUIREMENTS.md` concrete.

## What a profile is

| Part | Meaning | Phase 1 (`sonoff_trvzb_native`) |
|---|---|---|
| `id`, `name` | stable identifier, UI name | `sonoff_trvzb_native`, "Sonoff TRVZB (native)" |
| `match(device)` | does this profile fit a ZHA device? (manufacturer, model) | `SONOFF` / `TRVZB` |
| `min_firmware` | lowest firmware the profile is allowed to drive | 1.2.1 |
| `capabilities` | what the profile can do; each may carry its own firmware minimum | `EXTERNAL_TEMPERATURE`, `SOURCE_SELECT`, `WINDOW_DETECTION_FLAG`, `HYSTERESIS` (≥ 1.3.0) |
| `plan(intent) -> [Operation]` | translate a room intent into device operations | see `docs/profiles/sonoff-trvzb-native.md` |

An **intent** is what the room wants: `SetRoomTemperature(21.4)`, `SetSource(external | internal)`, `ApplySettings(window_detection=on, hysteresis=0.4)`.

An **operation** is what the adapter executes: `(endpoint, cluster, attribute, raw_value, verify)` where `verify` is a rule for the read-back (`equals`, `in {…}`, plus an optional *signal* mapping such as "read 2 means fail-safe"). Operations are device-level and profile-owned; the room logic never sees attribute ids.

## Why profiles

- **Native first.** The profile encodes the native path. The room logic stays the same whether a device regulates itself or, in some future profile, needs HA to nudge it.
- **Fallback without rewrite.** If a native path proves unreliable, a second profile for the same device (for example driving the TRVZB through `local_temperature_calibration` or through the HA `climate` entity) can be added and chosen per thermostat. The room logic, the write pipeline and the error reporting do not change.
- **Firmware fit.** A profile and each capability carry a minimum firmware. A thermostat below it is not driven; the user gets a Repairs issue naming the version instead of silent `UNSUPPORTED_ATTRIBUTE` failures.
- **Other devices later.** A new thermostat model is a new profile, not a change to the core.

## Selection

Phase 1: the only profile is auto-assigned to every thermostat it matches; a thermostat matched by no profile is rejected in the room form (`no_profile`). Later: a per-thermostat profile selector, defaulting to the native profile.

## Boundaries

A profile may only use the adapter (attribute write / read on the device) and, if an operation needs it, HA services on the thermostat's own entities. It must not touch other rooms, other devices or HA configuration.
