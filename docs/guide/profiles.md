# Profiles

[Easy TRV](../../README.md) › [Documentation](README.md)

## Profiles

Easy TRV talks to a thermostat through a profile, a small YAML file that says which device it fits, from which firmware, and which attributes to write and read back. The shipped profile is `sonoff_trvzb_native` in [`custom_components/easy_trv/profiles/builtin/`](../../custom_components/easy_trv/profiles/builtin/). Shipped profiles are never edited in place. The `sonoff_trvzb_native` profile targets firmware 1.3.0 and 1.4.4; the first run on real hardware is pending (see [`docs/testing-on-device.md`](../testing-on-device.md)).

### Add a profile in the UI

**Settings > Devices & services > Easy TRV > Add thermostat profile** offers three ways. The profile is stored in Easy TRV and listed on the integration page; rooms choose it in their **Profile** field. Deleting it there removes it.

- **Create your own profile (Beta):** name the profile, pick one of your thermostats as the sample, then choose which of its entities does what: the room temperature for the thermostat (a number, required), the temperature source (a select or switch), the open-window detection, frost protection, hysteresis and adaptive mode (all optional). Easy TRV then drives the thermostats through these Home Assistant entities, so it works with any integration (Zigbee2MQTT, deCONZ, ZHA, Tuya, ...). Other thermostats of the same model are found by the same entity key. All thermostats of a room must be the same model.
- **Upload or paste a profile:** a `.yaml` file or its text. Easy TRV checks it and shows what is wrong before saving.
- **Download from the community:** the profiles shared by other users in [`community/profiles/`](../../community/profiles/), installed with one click.

**Show or edit profile** on a stored profile shows its text to copy, lets you change it, and has a link to share it: it opens a pre-filled form on GitHub, a workflow checks the profile and opens a pull request, and once it is merged the profile is in the community list.

Entity profiles (`backend: entity`) are a Beta: a write is confirmed by the entity's new state instead of the Zigbee answer, and the TRVZB-specific signals (fail-safe, the thermostat's own schedule) are not available. The shipped Sonoff TRVZB profile stays native.

### Profile files

To adapt one:

1. Copy the shipped file to `<config>/easy_trv/profiles/<name>.yaml` (create the folder if it does not exist).
2. Change `id` to a new, unique id (it starts with a lowercase letter and may contain lowercase letters, digits and underscores). An id that is already taken by a shipped or earlier profile is rejected.
3. Make your changes and reload the Easy TRV integration.
4. Select the new profile in the room (Profile field).

An invalid file is ignored and raises a `profile_invalid` Repairs issue that names the file and the problem. The format is described in [`docs/concept/profiles.md`](../concept/profiles.md), the shipped profile in [`docs/profiles/sonoff-trvzb-native.md`](../profiles/sonoff-trvzb-native.md).

Firmware minimums are part of a profile. Below the profile's minimum firmware nothing is written to a thermostat and a Repairs issue names the required version. A single capability can have a higher minimum (hysteresis needs 1.3.0, the adaptive mode 1.4.4): that capability is skipped and reported, while the temperature writes continue. The skip lasts until the integration is reloaded, for example after a firmware update. A profile must declare the room temperature (`set_room_temperature`) and the source (`set_source`); one without them is rejected with a `profile_invalid` issue. A setting for which the profile declares no capability at all (for example a profile copied from 0.1.0 without the adaptive mode) is no firmware problem: it is skipped without a Repairs issue, logged once per room start at debug level and listed as `not_in_profile` in the diagnostics.
