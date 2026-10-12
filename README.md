# Easy TRV

Home Assistant custom integration that feeds Sonoff TRVZB radiator thermostats (ZHA) with an external room temperature and verifies every Zigbee write. It groups thermostats and sensors into rooms, keeps the TRV's native external-sensor input alive, detects the firmware's silent fallback to the internal sensor, and reports failures as Repairs issues. It does not regulate and creates no `climate` entity.

Status: design phase. Start with [`REQUIREMENTS.md`](REQUIREMENTS.md); the documentation map is in [`docs/README.md`](docs/README.md).

Supported: Sonoff TRVZB (Gen 1) on ZHA, firmware ≥ 1.2.1, Home Assistant ≥ 2026.8.
