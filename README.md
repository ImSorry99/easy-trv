# Easy TRV

Home Assistant custom integration that feeds Sonoff TRVZB radiator thermostats (ZHA) with an external room temperature and verifies every Zigbee write. It groups thermostats and sensors into rooms, keeps the TRV's native external-sensor input alive, detects the firmware's silent fallback to the internal sensor, and reports failures as Repairs issues. It does not regulate and creates no `climate` entity.

Status: design phase. See [`docs/specs/2026-10-12-easy-trv-design.md`](docs/specs/2026-10-12-easy-trv-design.md) for the Phase 1 specification and [`docs/research/`](docs/research/) for the research that shaped it (German).

Supported: Sonoff TRVZB (Gen 1) on ZHA, firmware ≥ 1.2.1, Home Assistant ≥ 2026.8.
