# Easy TRV

**Room-temperature control for Sonoff TRVZB radiator thermostats in Home Assistant.**

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Home Assistant 2026.8+](https://img.shields.io/badge/Home%20Assistant-2026.8%2B-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A radiator thermostat measures the temperature right next to the hot radiator, so the room stays colder than you set it. Easy TRV groups your thermostats and temperature sensors into rooms and feeds the room temperature from a real sensor into the TRVZB's own external-sensor input. The thermostat keeps regulating by itself; Home Assistant only supplies the value and checks that it arrived.

## Why Easy TRV

- **Native first.** No regulator in Home Assistant. The thermostat does the control, Easy TRV only feeds and watches it.
- **Verified writes.** Every write is confirmed and retried until it arrives. Easy TRV notices when a TRVZB silently drops back to its internal sensor and switches it back.
- **One thermostat per room.** One room `climate` entity with Off, Heat and Frost protection sets all thermostats of the room. A knob turn on one is taken over by the others.
- **Windows, presence, schedule, outdoor cut-off.** Window contacts switch the heating off, rooms are lowered while nobody is home, a Home Assistant schedule helper sets the times, warm weather switches rooms off.
- **Tells you what is wrong.** A heating state sensor per room with a plain-text hint, and Repairs issues for unreachable or silent thermostats and stuck window contacts.
- **All in the UI.** Global settings with per-room overrides, a dashboard card included, no YAML needed.

## What is supported

| | |
|---|---|
| **Thermostat** | Sonoff TRVZB (Gen 1), firmware 1.2.1 or newer (hysteresis needs 1.3.0, adaptive mode 1.4.4) |
| **Integration** | ZHA, with ZHA's built-in quirk (do not add a custom quirk for the TRVZB) |
| **Home Assistant** | 2026.8 or newer |
| **Other thermostats and integrations** | Beta: through your own entity profile (Zigbee2MQTT, deCONZ, Tuya, ...), an uploaded one or one from the [community profiles](community/profiles/) |

**Not supported or not done (yet)**

- Other thermostat models out of the box: they need a profile (see above).
- The thermostat's own weekly schedule: Easy TRV never uses it; schedules come from a Home Assistant helper.
- Window and outdoor holds switch the thermostats off and on again. If Home Assistant is down during a hold, they stay off (with their own frost protection).
- Humidity sensors, presets, boost and battery warnings are not used yet.
- Presence: no vacation mode with a return time.
- The first full run on real hardware is still pending, see [testing on a device](docs/testing-on-device.md).

Current version: **0.10.0b1** (pre-release). See the [changelog](CHANGELOG.md).

## Installation

1. In HACS, open the menu (three dots), choose **Custom repositories** and add `https://github.com/ImSorry99/easy-trv` as type **Integration**.
2. Download **Easy TRV** and restart Home Assistant.
3. **Settings > Devices & services > Add integration > Easy TRV**, then **Add room**.

Details, pre-releases and removal: [installation guide](docs/guide/installation.md).

## Documentation

| Topic | |
|---|---|
| [Installation and removal](docs/guide/installation.md) | HACS, pre-releases, removing Easy TRV |
| [Configuration](docs/guide/configuration.md) | Rooms, areas, all settings and their defaults |
| [Room thermostat](docs/guide/room-thermostat.md) | Modes, range, knob turns, restarts |
| [Entities and the dashboard card](docs/guide/entities-and-card.md) | House entities, room sensors, the Easy TRV card |
| [Window contacts and outdoor cut-off](docs/guide/windows-and-outdoor.md) | Opening delay, warnings, stuck contacts |
| [Presence](docs/guide/presence.md) | Lower or switch off while away, forcing presence |
| [Schedule](docs/guide/schedule.md) | Home Assistant schedule helper, temperature per block |
| [Heating state, Repairs, events, diagnostics](docs/guide/monitoring.md) | What the room is doing and why |
| [Statistics](docs/guide/statistics.md) | Optional sensors for writes, failures, heating time |
| [Profiles](docs/guide/profiles.md) | Supporting other thermostats |
| [How it works and limitations](docs/guide/how-it-works.md) | Sensors, writes, verification, fail-safe |
| [Development](docs/guide/development.md) | Running the tests and checks |

Design documents, research and plans: [documentation map](docs/README.md).

## Roadmap

Ideas, not promises:

- Final 0.10.0 once the beta has been tested on real thermostats.
- A radio connection sensor per thermostat (good, weak, offline).
- More thermostat models through community profiles.
- Using the humidity sensors, boost and battery warnings.
- Heating time from the valve opening instead of the reported heating state.

## Contributing

Profiles for more thermostats are the easiest way to help. In Easy TRV open your profile (**Show or edit profile**) and use the share link: it opens a pre-filled form on GitHub, a workflow checks the profile and opens a pull request. Pull requests for code and documentation are welcome too, and so are [issues](https://github.com/ImSorry99/easy-trv/issues) with bug reports or test results from real hardware. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE).
