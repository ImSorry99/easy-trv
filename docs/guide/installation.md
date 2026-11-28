# Installation and removal

[Easy TRV](../../README.md) › [Documentation](README.md)

## Requirements and supported devices

| Device | Integration | Firmware | Notes |
|---|---|---|---|
| Sonoff TRVZB (Gen 1) | ZHA | 1.2.1 or newer | Hysteresis needs 1.3.0 or newer, the adaptive mode 1.4.4 or newer. Targeted firmware: 1.3.0 and 1.4.4. The first hardware run is pending (see [`docs/testing-on-device.md`](../testing-on-device.md)). |

Home Assistant 2026.8 or newer is required. The shipped profile needs the thermostats paired with ZHA; thermostats of other integrations work with an entity profile (Beta, see [Profiles](profiles.md)). Use ZHA's built-in quirk for the TRVZB; do not add a custom quirk for the same model, the two conflict.

## Installation

1. In HACS, open the menu (three dots) and choose **Custom repositories**.
2. Add `https://github.com/ImSorry99/easy-trv` with the type **Integration**.
3. Download **Easy TRV** and restart Home Assistant.
4. Go to **Settings > Devices & services > Add integration** and add **Easy TRV**. It can be added once; it has no setup form.

Pre-releases (versions like `0.10.0b1`) are not offered as an update by default. To try one, open Easy TRV in HACS, choose **Redownload** and pick the version, or switch on the pre-release switch HACS adds for the repository.

## Removal

Delete the Easy TRV entry (**Settings > Devices & services > Easy TRV > three dots > Delete**), then remove the repository in HACS if you like. Easy TRV does not reset the thermostats. They keep their last settings; because nothing refreshes the external value any more, a TRVZB falls back to its internal sensor on its own after about two hours.
