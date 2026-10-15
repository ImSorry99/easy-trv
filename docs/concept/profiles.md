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

## Profiles are data

A profile is a YAML document, not Python. Shipped profiles live inside the integration (`profiles/builtin/*.yaml`), user profiles in `<config>/easy_trv/profiles/*.yaml`. Both go through the same schema validation at startup; an invalid user file becomes a Repairs issue (`profile_invalid_<file>`) and is skipped. Shipped files are read-only; to change one, copy it, give it a new `id`, adapt it, select it.

```yaml
schema: 1
id: sonoff_trvzb_native
name: Sonoff TRVZB (native)
match: { manufacturer: SONOFF, model: TRVZB }
min_firmware: "1.2.1"
tested_firmware: ["1.3.0", "1.4.4"]
capabilities:
  external_temperature:
    intent: set_room_temperature
    write: { endpoint: 1, cluster: 0xFC11, attribute: 0x600D, encode: temperature_centi, range: [0, 99.9] }
    verify: { rule: equals }
    also_read: [0x600E]              # read in the same request; signals apply
  source_select:
    intent: set_source
    write: { endpoint: 1, cluster: 0xFC11, attribute: 0x600E, encode: { map: { external: 1, internal: 0 } } }
    verify: { rule: one_of, external: [1, 3], internal: [0] }
    signals: { 2: failsafe }
  window_detection_flag:
    intent: set_window_detection
    write: { endpoint: 1, cluster: 0xFC11, attribute: 0x6000, encode: bool }
    verify: { rule: equals }
  hysteresis:
    intent: set_hysteresis
    min_firmware: "1.3.0"
    write: { endpoint: 1, cluster: 0xFC11, attribute: 0x6011, encode: kelvin_centi_negative, range: [0.2, 1.0] }
    verify: { rule: equals }
  adaptive_mode:
    intent: set_adaptive_mode
    min_firmware: "1.4.4"
    write: { endpoint: 1, cluster: 0xFC11, attribute: 0x6017, encode: { map: { "true": 2, "false": 0 } } }
    verify: { rule: one_of, "true": [2], "false": [0, 1] }   # the device reports 1 as a read-only alias of off
  frost_protection:
    intent: set_frost_temperature
    write: { endpoint: 1, cluster: 0xFC11, attribute: 0x6002, encode: temperature_centi, range: [4, 35] }
    verify: { rule: equals }
  setpoint:
    intent: set_setpoint
    observe: true                    # the room follows this value on the device
    write: { endpoint: 1, cluster: 0x0201, attribute: 0x0012, encode: temperature_centi, range: [4, 35], step: 0.5 }
    verify: { rule: equals }
  hvac_mode:
    intent: set_hvac_mode
    observe: true
    write: { endpoint: 1, cluster: 0x0201, attribute: 0x001C, encode: { map: { heat: 4, "off": 0 } } }
    verify: { rule: one_of, heat: [4], "off": [0] }
    signals: { 1: schedule }         # read 1 (auto) = the device runs its own schedule
```

The vocabulary is small on purpose: a fixed set of intents (`set_room_temperature`, `set_source`, `set_window_detection`, `set_hysteresis`, `set_adaptive_mode`, `set_frost_temperature`, `set_setpoint`, `set_hvac_mode`), a fixed set of encoders (`temperature_centi`, `kelvin_centi_negative`, `bool`, `map`, `const`), two verify rules (`equals`, `one_of`) and named signals. New vocabulary is added in code when a new device needs it; profiles only combine it.

Two optional keys support room state. `write.step` (a positive number) makes the engine round the intent value half-up to that grid before the range check and the encoder (21.25 → 21.5 with `step: 0.5`); without it nothing is rounded. `observe: true` marks a capability whose device value the room follows; `Profile.observed()` lists those. `Profile.decode(capability, raw)` is the inverse of the encoder (`temperature_centi` → °C, `map` → the key, an unmapped raw value → `None`) and `Profile.limits(intent)` returns `(low, high, step)` from `range` and `step` (step 0.5 when absent; it only matters for the setpoint). `set_setpoint` and `set_hvac_mode` are room state, not thermostat settings, so a room never applies them as settings. Existing user profiles stay valid: `step` and `observe` are optional.

Details that matter when writing a profile:

- The keys of a `map` encoder (and of a `one_of` verify rule) are matched against the intent value rendered as text: a string as it is (`external`), any other value as `str(value).lower()`. For a boolean intent the keys are therefore `true` and `false`; quote them in YAML (`"true": 1`), otherwise YAML reads them as booleans and the schema rejects the file. The same goes for `"off"` (and `"on"`, `"yes"`, `"no"`), which YAML 1.1 reads as booleans. A `one_of` rule must list exactly the keys of the `map` encoder.
- A user profile's `id` must be unique: an id that a shipped profile (or an earlier user file) already uses is rejected with a `profile_invalid` Repairs issue, and the file is skipped. Pick a new id when copying a shipped profile.

## Selection

Per room: `auto` (the shipped profile that matches the room's thermostats) or an explicit profile id from all shipped and user profiles that match. A thermostat matched by no profile is rejected in the room form (`no_profile`); a thermostat not matched by an explicitly chosen profile too (`profile_mismatch`). Per-thermostat selection is a later step.

## Boundaries

A profile may only use the adapter (attribute write / read on the device) and, if an operation needs it, HA services on the thermostat's own entities. It must not touch other rooms, other devices or HA configuration.
