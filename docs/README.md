# Documentation map

| Read this if you want… | File |
|---|---|
| how to install, configure and use Easy TRV (user guide) | `../README.md` |
| what Easy TRV must do (the owner's requirements, short) | `../REQUIREMENTS.md` |
| why devices are driven through profiles | `concept/profiles.md` |
| exactly how the Sonoff TRVZB is addressed (attributes, firmware, spikes) | `profiles/sonoff-trvzb-native.md` |
| how the code is structured (for implementers) | `specs/2026-10-12-easy-trv-design.md` |
| what Phase 2 adds (settings model, room thermostat, window contacts, outdoor cut-off) | `specs/2026-10-14-easy-trv-phase2-design.md` |
| what 0.5 adds (heating state sensor, silent window contacts, areas, hiding the thermostat entities) and how 0.5.1 changes the sensor (one diagnostic sensor, five restrictions, hint) | `specs/2026-10-15-easy-trv-0.5-room-state-design.md` |
| what 0.6 adds (statistics sensors for rooms and thermostats: writes, failed writes, dropouts, window openings, heating time, control deviation) | `specs/2026-10-15-easy-trv-0.6-statistics-design.md` |
| what 0.7 adds (presence from a global or per-room entity: lower or switch off while away, override until home) | `specs/2026-10-18-easy-trv-0.7-presence-design.md` |
| how to test on real thermostats (checklist and spikes S1–S5, S8–S11) | `testing-on-device.md` |
| which Home Assistant quality scale rules are met | `../custom_components/easy_trv/quality_scale.yaml` |
| the research behind the decisions (German) | `research/` |
| implementation plans (Phase 1, Phase 2, 0.5 room state, 0.6 statistics, 0.7 presence) | `plans/` |
| what changed in each release | `../CHANGELOG.md` |

Planning, wishes, roadmap and open decisions live in the maintainer's planning notes, not here.
