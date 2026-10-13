# Easy TRV

Easy TRV is a Home Assistant integration that lets Sonoff TRVZB radiator thermostats regulate on the temperature of the room instead of the temperature at the radiator. It groups thermostats and temperature sensors into rooms, writes the room temperature to the thermostats' own external-sensor input, and checks by read-back that every write arrived.

It works native first: the thermostat keeps regulating by itself, Home Assistant only feeds it a value and watches over it. There is no `climate` entity, no regulator and no entity per setting. Each room gets one status sensor.

Status: Phase 1, version 0.1.0. See [`REQUIREMENTS.md`](REQUIREMENTS.md) for what Easy TRV must do and [`docs/README.md`](docs/README.md) for the documentation map.

## Supported devices

| Device | Integration | Firmware | Notes |
|---|---|---|---|
| Sonoff TRVZB (Gen 1) | ZHA | 1.2.1 or newer | Hysteresis needs 1.3.0 or newer. Tested with 1.3.0 and 1.4.4. |

Home Assistant 2026.8 or newer is required. The thermostats must already be paired with ZHA. Use ZHA's built-in quirk for the TRVZB; do not add a custom quirk for the same model, the two conflict.

Other thermostats, Zigbee2MQTT and other Zigbee integrations are not supported.

## Installation

1. In HACS, open the menu (three dots) and choose **Custom repositories**.
2. Add `https://github.com/ImSorry99/easy-trv` with the type **Integration**.
3. Download **Easy TRV** and restart Home Assistant.
4. Go to **Settings > Devices & services > Add integration** and add **Easy TRV**. It can be added once; it has no setup form.

## Configuration

Rooms live inside the single Easy TRV entry. On the integration page choose **Add room**, then pick how the room is created:

- **From area**: the room takes the name of the area. The next page is prefilled with the area's ZHA thermostats and its temperature and humidity sensors (the thermostats' own temperature readings are left out). Change whatever does not fit.
- **Custom room**: enter a name, then choose everything yourself.

The room form asks for:

| Field | Meaning |
|---|---|
| Thermostats (ZHA) | One or more ZHA thermostats of a supported model. A thermostat can belong to one room only. |
| Temperature sensors | The room's sensors in priority order (drag to reorder). The first usable one is used. Without any sensor Easy TRV does not touch the thermostats' temperature source. |
| Humidity sensors | Stored for later use. Not evaluated yet. |
| Profile | `auto` takes the first profile that matches all thermostats of the room. Or choose a shipped or [user profile](#profiles). |
| Window detection | `on` (default), `off` or `leave unchanged`. Switches the thermostat's own open-window detection. |
| Hysteresis | `leave unchanged` (default) or 0.2, 0.4, 0.6, 0.8, 1.0 K. Needs firmware 1.3.0 or newer. |

Window detection and hysteresis are applied once when the room starts. To change a room later, or to remove it, open the room's menu on the integration page (edit or delete). Saving a change reloads the integration.

### Global options

Choose **Configure** on the integration page. The options apply to all rooms.

| Option | Default | Range | Meaning |
|---|---|---|---|
| `refresh_minutes` (Refresh interval) | 20 | 5 to 90 minutes | The room temperature is written again at least this often, even if it has not changed. The TRVZB drops the external value after about two hours without a write. |
| `verify_delay_seconds` (Read-back delay) | 10 | 2 to 60 seconds | Wait this long after a write before reading the value back. The thermostat is a sleepy device and may need a moment. |
| `max_attempts` (Attempts) | 3 | 1 to 10 | How often a failing write is tried before the thermostat is reported as not responding. |
| `min_delta_kelvin` (Minimum change) | 0.1 | 0.1 to 1.0 K | A new room temperature is written only if it differs from the last written value by at least this much. |
| `sensor_timeout_minutes` (Sensor timeout) | 120 | 0 (off), or 10 to 720 minutes | A sensor that has not reported for this long is ignored although Home Assistant still shows it as available. `0` switches the timeout off. |

## How it behaves

- **Active sensor.** A sensor is usable if it is available, shows a number in °C (°F is converted), lies between 0 and 99.9 °C, and has reported within the sensor timeout. The first usable sensor in the room's order is the active one.
- **Writes.** When the active sensor's value changes by at least the minimum change, the value is written to every thermostat of the room after a 5 second pause that merges bursts of reports. Independently it is written again every refresh interval. Writes do not depend on the thermostat's HVAC mode.
- **Verification.** Every write is read back after the read-back delay and compared. A write that does not arrive is retried with a growing pause (5, 15, then 45 seconds) up to the configured number of attempts. After that the thermostat is marked as failing (see the status sensor, Repairs and events below). The next verified write clears this by itself. A failing thermostat never blocks the others in the room, and writes within a room happen one at a time so the Zigbee network is not flooded.
- **Fail-safe.** When a TRVZB gets no external value for about two hours it silently falls back to its internal sensor. ZHA still shows the external source as on. Easy TRV reads the source flag back together with every value write, notices the fallback, switches the external source on again, writes the value and fires `easy_trv_failsafe_detected`.
- **No usable sensor.** If none of the room's sensors is usable, the thermostats are switched to their internal sensor and the room is `degraded`. When a sensor is usable again, the external source is switched on again.
- **Unavailable thermostats.** A thermostat that Home Assistant reports as unavailable is skipped without an issue and brought up to date when it returns.

Rooms start once Home Assistant has fully started.

## What you see

### Status sensor

Each room is a device with one diagnostic sensor, for example `sensor.living_room_status`.

| State | Meaning |
|---|---|
| `ok` | All thermostats work and, if the room has sensors, the external source is active. |
| `degraded` | No usable sensor although some are configured, a thermostat is unavailable or lacks a firmware feature, or a fail-safe was handled within the last refresh interval. |
| `error` | At least one thermostat did not accept a write after all attempts. |

Attributes:

| Attribute | Content |
|---|---|
| `active_sensor` | Entity id of the sensor in use, or empty. |
| `source` | `external`, `internal` or `none` (no sensors configured, nothing switched yet). |
| `trv_health` | Per thermostat: `ok`, `unavailable`, `unsupported` or `error`. |
| `last_error` | The last write error of a failing thermostat, or empty. |

### Repairs

| Issue | Severity | Raised when | Cleared |
|---|---|---|---|
| `trv_unreachable` | Error | A thermostat did not accept a write after all attempts. Check battery and Zigbee signal. | On the next verified write, or when the integration is reloaded. |
| `trv_firmware` | Warning | The thermostat's firmware is too old for a capability (for example hysteresis below 1.3.0). | After a firmware update and a reload of the integration. |
| `zha_api_incompatible` | Error | Easy TRV cannot use the installed ZHA's internal API. Setup is retried. | When setup works. |
| `profile_invalid` | Warning | A user profile file is invalid and is ignored. The issue names the file and the problem. | When the file loads cleanly after you fix it and reload the integration. |

### Events

Events are fired on the Home Assistant bus (Developer tools > Events) and can trigger automations.

| Event | When |
|---|---|
| `easy_trv_write_failed` | A thermostat did not accept a write after all attempts. |
| `easy_trv_recovered` | A thermostat that was failing accepted a write again. |
| `easy_trv_failsafe_detected` | A thermostat had fallen back to its internal sensor and was switched back. |

Payload of all three: `room`, `room_id`, `trv_entity_id`, `ieee`. `easy_trv_write_failed` adds `attribute` (the capability that failed, for example `external_temperature`), `attempts` and `error`.

### Diagnostics

**Settings > Devices & services > Easy TRV > Download diagnostics** contains the options, the loaded profiles and skipped profile files, and for every room its configuration and full state (including timestamps and attempt counters). It includes the thermostats' IEEE addresses and entity ids, so look through it before posting it publicly.

## Profiles

Easy TRV talks to a thermostat through a profile, a small YAML file that says which device it fits, from which firmware, and which attributes to write and read back. The shipped profile is `sonoff_trvzb_native` in [`custom_components/easy_trv/profiles/builtin/`](custom_components/easy_trv/profiles/builtin/). Shipped profiles are tested on real hardware and are never edited in place.

To adapt one:

1. Copy the shipped file to `<config>/easy_trv/profiles/<name>.yaml` (create the folder if it does not exist).
2. Change `id` to a new, unique id (it starts with a lowercase letter and may contain lowercase letters, digits and underscores). An id that is already taken by a shipped or earlier profile is rejected.
3. Make your changes and reload the Easy TRV integration.
4. Select the new profile in the room (Profile field).

An invalid file is ignored and raises a `profile_invalid` Repairs issue that names the file and the problem. The format is described in [`docs/concept/profiles.md`](docs/concept/profiles.md), the shipped profile in [`docs/profiles/sonoff-trvzb-native.md`](docs/profiles/sonoff-trvzb-native.md).

Firmware minimums are part of a profile. Below the profile's minimum firmware nothing is written to a thermostat and a Repairs issue names the required version. A single capability can have a higher minimum (hysteresis needs 1.3.0): that capability is skipped and reported, while the temperature writes continue. The skip lasts until the integration is reloaded, for example after a firmware update.

## Limitations

- **No window contacts in Phase 1.** The TRVZB has no input for them. The window setting only switches the thermostat's built-in detection, which reacts to a fast temperature drop.
- **Writes continue while a thermostat is off.** Easy TRV keeps the external value in sync regardless of the HVAC mode. Whether a write can wake an `off` thermostat is one of the open checks in [`docs/testing-on-device.md`](docs/testing-on-device.md).
- **ZHA and the Sonoff TRVZB only.** Other models need their own profile.
- **ZHA's internal API.** Writing and verifying attributes needs ZHA's internal API. Easy TRV keeps all of it in one adapter module, so a future Home Assistant change is fixed in one place. If the API breaks, the `zha_api_incompatible` issue says so.
- **Not used yet:** humidity sensors, presence, schedules, presets, outdoor cut-off, battery warnings.

## Removal

Delete the Easy TRV entry (**Settings > Devices & services > Easy TRV > three dots > Delete**), then remove the repository in HACS if you like. Easy TRV does not reset the thermostats. They keep their last settings; because nothing refreshes the external value any more, a TRVZB falls back to its internal sensor on its own after about two hours.

## Development

Python 3.14.2 or newer and [uv](https://docs.astral.sh/uv/) are required.

```bash
uv sync                      # install the dependencies, including Home Assistant
uv run pytest                # tests with a coverage report
uv run ruff check .          # lint
uv run ruff format --check . # formatting
uv run mypy                  # strict typing of core/, profiles/ and adapters/
```

CI runs the same checks plus hassfest and the HACS validation. Testing on real thermostats is covered by [`docs/testing-on-device.md`](docs/testing-on-device.md); the technical design is in [`docs/specs/2026-10-12-easy-trv-design.md`](docs/specs/2026-10-12-easy-trv-design.md).

## License

No license has been chosen yet.
