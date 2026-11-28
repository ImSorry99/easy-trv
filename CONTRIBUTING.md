# Contributing to Easy TRV

Thanks for helping. Easy TRV is a small project, so short and focused contributions work best.

## Share a thermostat profile

Profiles let Easy TRV drive more thermostat models. This is the easiest and most useful contribution.

1. In Home Assistant create or upload the profile: **Settings > Devices & services > Easy TRV > Add thermostat profile** (see [Profiles](docs/guide/profiles.md)).
2. Open it with **Show or edit profile** and use the share link. It opens the [profile form](https://github.com/ImSorry99/easy-trv/issues/new?template=profile.yml) on GitHub with the profile filled in.
3. Say which thermostat, firmware and integration you tested it with.
4. A workflow checks the profile and opens a pull request. Once it is merged, the profile shows up under **Download from the community** for everyone.

Shared profiles live in [`community/profiles/`](community/profiles/). The format is described in [`docs/concept/profiles.md`](docs/concept/profiles.md).

## Report a bug or test results

Open an [issue](https://github.com/ImSorry99/easy-trv/issues). Helpful: the Easy TRV version, Home Assistant version, thermostat model and firmware, the room's heating state, and a debug log or diagnostics download (look through it first, it contains IEEE addresses). Results from real hardware are especially welcome, see [testing on a device](docs/testing-on-device.md).

## Code and documentation

Pull requests are welcome. Before opening one, run the checks from [Development](docs/guide/development.md) (`pytest`, `ruff`, `mypy`) and add a line to the `Unreleased` section of the [changelog](CHANGELOG.md) if users will notice the change.
