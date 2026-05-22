# Petkit BLE Water Fountain — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/custom-components/hacs)
[![GitHub Release][releases-shield]][releases]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
![Maintenance][maintenance-shield]

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=v1k70rk4&repository=ha-petkit-ble&category=integration)

> **100% local BLE control** for Petkit water fountains — no cloud, no app, no compromises.

---

## Highlights

| | Feature | Details |
|---|---------|---------|
| **Local-only** | All communication stays on your LAN via Bluetooth LE — zero cloud dependency |
| **Full dashboard** | 14 sensors, 5 switches, 3 sliders, 4 time pickers, mode selector, reset button |
| **Multi-device** | W4, W5, CTW2 & CTW3 series with automatic capability detection |
| **Smart scheduling** | LED on/off times, Do Not Disturb windows, smart mode work/sleep cycles |
| **Alerts** | Breakdown, filter expiry, low water, low battery — all as binary sensors |
| **Multi-language** | English, Hungarian, Dutch, Ukrainian — add yours in minutes |
| **Auto time-sync** | Device clock synced every ~60 min (no RTC chip on board) |
| **Instant reconnect** | Progressive retry (100 ms → 5 s) with automatic recovery |

---

## Supported Devices

| Series | Models | Extras |
|--------|--------|--------|
| **W4** | Eversweet 3 Pro, Eversweet 3 Pro UVC | UVC sterilization indicator |
| **W5** | Eversweet Mini | — |
| **CTW2** | Eversweet Solo 2 | — |
| **CTW3** | Eversweet Max, Eversweet Max 2 | Pet detection, AC power, low battery, pump suspend |

---

## Installation

### HACS (Recommended)

[![Add to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=v1k70rk4&repository=ha-petkit-ble&category=integration)

Click the button above, or manually:

1. Open [HACS](https://hacs.xyz/) → **Integrations**
2. **⋮** → **Custom repositories** → paste `https://github.com/v1k70rk4/ha-petkit-ble` → category **Integration**
3. Find **Petkit BLE Water Fountain** → **Install** → restart HA

### Manual

1. Copy `custom_components/petkit_ble` into your HA `custom_components/` folder
2. Restart Home Assistant
3. **Settings** → **Integrations** → **Add Integration** → search **Petkit BLE**

---

## What You Get

<details>
<summary><strong>Sensors (14)</strong></summary>

| Sensor | Unit | Notes |
|--------|------|-------|
| Battery level | % | |
| Filter remaining | % | |
| Filter days left | days | |
| Pump runtime (total) | hours | |
| Pump runtime (today) | hours | |
| Purified water (total) | L | |
| Purified water (today) | L | |
| Energy consumption | kWh | |
| Signal strength (RSSI) | dBm | disabled by default |
| Voltage | V | disabled by default |
| Connection status | — | connected / connecting / disconnected / … |
| Connection attempts | — | disabled by default |
| Last seen | timestamp | disabled by default |
| Firmware version | — | disabled by default |

</details>

<details>
<summary><strong>Binary Sensors (8)</strong></summary>

| Sensor | Notes |
|--------|-------|
| Power status | |
| Pump running | |
| Breakdown warning | |
| Filter warning | |
| Water missing | |
| Pet drinking | CTW3 only, disabled by default |
| AC power | CTW3 only, disabled by default |
| Low battery | CTW3 only, disabled by default |
| Pump suspended | CTW3 only, disabled by default |

</details>

<details>
<summary><strong>Switches (5)</strong></summary>

- Power on/off
- Smart mode
- LED on/off
- Do Not Disturb
- Child lock

</details>

<details>
<summary><strong>Number Controls (3)</strong></summary>

| Control | Range | Mode |
|---------|-------|------|
| LED brightness | 0–100 % | slider |
| Smart mode work time | 1–120 min | box |
| Smart mode sleep time | 1–120 min | box |

</details>

<details>
<summary><strong>Time Controls (4)</strong></summary>

- LED on time / LED off time
- DND start time / DND end time

*Disabled by default — enable in entity settings.*

</details>

<details>
<summary><strong>Select & Buttons</strong></summary>

- **Mode selector**: Normal / Smart *(disabled by default — alternative to smart mode switch)*
- **Reset filter**: resets filter life indicator from the device card

</details>

---

## Services

> Since v1.0.0 all service calls require a **device target**.

### `petkit_ble.reset_filter`

```yaml
action: petkit_ble.reset_filter
target:
  device_id:
    - 0000000000000000000000
```

### `petkit_ble.set_device_config`

| Parameter | Type | Description |
|-----------|------|-------------|
| `smart_time_on` | int | Smart mode on duration (minutes) |
| `smart_time_off` | int | Smart mode off duration (minutes) |
| `led_brightness` | int | LED brightness (0–100 %) |
| `led_switch` | bool | LED on/off |
| `do_not_disturb` | bool | DND on/off |
| `is_locked` | bool | Child lock on/off |

```yaml
action: petkit_ble.set_device_config
target:
  device_id:
    - 0000000000000000000000
data:
  smart_time_on: 30
  smart_time_off: 60
  led_brightness: 80
  led_switch: true
  do_not_disturb: true
  is_locked: true
```

---

## Automation Examples

<details>
<summary><strong>Low Battery Alert</strong></summary>

```yaml
automation:
  - alias: "Petkit Low Battery Alert"
    trigger:
      platform: numeric_state
      entity_id: sensor.petkit_water_fountain_battery
      below: 20
    action:
      service: notify.mobile_app
      data:
        message: "Water fountain battery is low ({{ states('sensor.petkit_water_fountain_battery') }}%)"
```

</details>

<details>
<summary><strong>Filter Replacement Reminder</strong></summary>

```yaml
automation:
  - alias: "Petkit Filter Replacement"
    trigger:
      platform: numeric_state
      entity_id: sensor.petkit_water_fountain_filter_percentage
      below: 10
    action:
      service: notify.mobile_app
      data:
        message: "Time to replace the water fountain filter ({{ states('sensor.petkit_water_fountain_filter_percentage') }}% remaining)"
```

</details>

---

## Translations

| Language | Status |
|----------|--------|
| English | default (built-in) |
| Magyar / Hungarian | complete |
| Nederlands / Dutch | complete — *based on [aavdberg/ha-petkit](https://github.com/aavdberg/ha-petkit)* |
| Ukrainian | complete — *based on [aavdberg/ha-petkit](https://github.com/aavdberg/ha-petkit)* |

**Add your own:** copy `strings.json` → `translations/{lang_code}.json`, translate values, restart HA.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Device not found | Ensure BT is enabled, device is powered on, HA host is in range. Try `sudo systemctl restart bluetooth`. |
| Connection drops | Only **one** BLE connection at a time — close the Petkit app first. |
| Entities unavailable | Check debug logs: add `custom_components.petkit_ble: debug` to `logger:` in `configuration.yaml`. |

---

## Known Limitations

- Device supports only one active BLE connection — the official Petkit app and this integration cannot be used simultaneously
- CTW3-specific entities (pet detection, AC power, etc.) are disabled by default — enable them manually if your device supports them

---

## Contributing

Contributions are welcome! Feel free to open a PR or issue.

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Credits

- Originally based on [pdiegmann/ha-petkit-ble](https://github.com/pdiegmann/ha-petkit-ble) — extensively reworked with new entity types, device capability detection, multi-language support, and expanded device compatibility
- Inspired by [RobertD502's Petkit integration](https://github.com/RobertD502/homeassistant-petkit) and [petkitaio](https://github.com/RobertD502/petkitaio)
- BLE protocol analysis from [PetKit Eversweet Pro 3 research](https://colab.research.google.com/drive/1gWwLz1Wi_WujvvSaTJpPMW5i3YideSAb)
- Dutch & Ukrainian translations based on [aavdberg/ha-petkit](https://github.com/aavdberg/ha-petkit)

---

[commits-shield]: https://img.shields.io/github/commit-activity/y/v1k70rk4/ha-petkit-ble.svg?style=for-the-badge
[commits]: https://github.com/v1k70rk4/ha-petkit-ble/commits/main
[license-shield]: https://img.shields.io/github/license/v1k70rk4/ha-petkit-ble.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-%40v1k70rk4-blue.svg?style=for-the-badge
[releases-shield]: https://img.shields.io/github/release/v1k70rk4/ha-petkit-ble.svg?style=for-the-badge
[releases]: https://github.com/v1k70rk4/ha-petkit-ble/releases
