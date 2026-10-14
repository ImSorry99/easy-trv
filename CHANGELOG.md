# Changelog

All notable changes to Easy TRV are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Adaptive mode and frost protection temperature as profile capabilities

### Changed

- Hysteresis follows the global value (default 0.4 K) and is written whenever the adaptive mode is not on; rooms that had 'leave unchanged' are migrated and get a Repairs notice

## [0.1.0] - 2026-10-13

### Added

- Rooms created from Home Assistant areas or as custom rooms, grouping thermostats and temperature sensors.
- Thermostat profiles as declarative YAML files: a shipped Sonoff TRVZB native profile and user profiles from the config folder.
- External temperature for the thermostats with periodic refresh and verified writes (write, wait, read back, retry).
- Fail-safe detection: a thermostat that fell back to its internal sensor is switched back to the external source.
- Repairs issues for failed writes, outdated firmware and invalid profiles, plus a status sensor per room.

[Unreleased]: https://github.com/ImSorry99/easy-trv/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ImSorry99/easy-trv/releases/tag/v0.1.0
