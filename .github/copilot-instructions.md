# Copilot code review instructions

- Start review comments with a short, one-sentence summary of the suggested fix.
- Do not comment on code style, formatting or linting issues; Ruff runs in CI.
- Flag comments that over-explain straightforward code, narrate the obvious, or read like AI commentary (multi-sentence justifications for a single line).
- A pull request with a dependency version bump should only contain changes required for the bump. If it includes other changes, request that they are removed.
- Dependabot pull requests are identifiable by "Dependabot commands and options" in the description. All review instructions still apply to them.

# Project context

This repository is a custom Home Assistant integration, distributed through HACS, for Petkit water fountains (W4, W5, CTW2, CTW3 series). It talks to the devices locally over Bluetooth LE through Home Assistant's Bluetooth integration.

- Integration code: `custom_components/petkit_ble/`.
- Device protocol library: `custom_components/petkit_ble/PetkitW5BLEMQTT/`. This vendored copy is the one Home Assistant loads. The top-level `PetkitW5BLEMQTT/` and `main.py` are the original standalone tool; changes for the integration belong in the vendored copy.
- The minimum supported Home Assistant version is the `homeassistant` field in `hacs.json`. Do not suggest workarounds for older Home Assistant or Python versions.
- There is no test suite. CI runs hassfest, HACS validation, Ruff and CodeQL.

## BLE connection handling

These rules come from real failures. Review changes to `ha_bluetooth_adapter.py` and `coordinator.py` against them and flag violations:

- The device accepts only one BLE connection. A client must never stay connected while untracked: whenever a client is removed from `connected_devices`, it must also be disconnected.
- A disconnect callback must check that the disconnected client is the one currently tracked before evicting it. A late callback from a previous client must not remove the current one.
- Every awaited BLE operation (connect, write, notification subscribe) must be bounded by a timeout. The write queue producer must stay non-blocking (`put_nowait`).
- Reconnection runs in a single guarded background task. The poll loop must never await the reconnect lock.
- A reconnect only counts as successful when notifications are subscribed. After a reconnect the device may have power-cycled and lost its authenticated session; the auth handshake (`get_device_details`, then `init_device` / CMD 73 when uninitialized) must run again.
- Connection health is judged by received notifications (`last_notification`), not by successful writes. Writes to a device that has disappeared can succeed silently.
- `establish_connection` receives the lowercase MAC address (workaround for bleak-retry-connector#302). Internal dictionaries stay keyed by the configured address.
- The device has no real-time clock. Time sync after setup and periodically must stay.
- The device sends notifications in bursts. Per-notification logging must be at debug level; INFO or WARNING logs per packet trigger Home Assistant's "logging too frequently" warning and slow the event loop.

## Entities and device data

- Sensor and binary sensor values must be `None` (unknown) until the device reports data. Do not default to `0`, `"OK"` or a fake firmware version. Fields used to build the device config blob (smart mode times, LED, do not disturb, child lock) keep their numeric defaults.
- Build device info with `DeviceInfo(...)` keyword arguments. `DeviceInfo` is a dataclass since Home Assistant 2026.11: do not use dict operations (`|`, iteration, `len()`, `isinstance(..., dict)`) on it, and remember that an unset field reads as `UNDEFINED`, which is truthy.
- The internal `"Uninitialized"` sentinel must never reach the device registry (name, model, firmware).
- Model-specific entities (CTW3 sensors, schedule time entities, mode select) use `_attr_entity_registry_enabled_default = False`. New model-specific entities should follow this.
- CTW3 devices are detected by `device_type == 24`; their alias is never `"CTW3"`. Use the existing helpers in `const.py` and `parsers.py`.
- Constants used by more than one module belong in `const.py`. Never import a constant from one platform module into another.

## Translations

- Custom integrations load translations from `translations/<lang>.json`; there is no `strings.json`. `translations/en.json` is the source of truth.
- A new or renamed translation key must be added to every language file (`en`, `hu`, `nl`, `uk`). Flag pull requests that update only some of them.
- Entities use `_attr_has_entity_name = True` with a `translation_key`. Do not hardcode English entity names.

## Releases and assets

- The version is the `version` field in `manifest.json`. Release notes are written relative to the previous published tag.
- Brand assets live in `custom_components/petkit_ble/brand/` as transparent PNGs: icons 256×256 and 512×512 (`@2x`); logos with the shortest side 128–256 px and 256–512 px (`@2x`).

## Good practices

- Keep comments concise. Explain why (non-obvious constraints, surprising behaviour, workarounds), never what. Do not add section or divider comments, and do not justify a change by describing what the code looked like before.
- Keep try blocks as small as possible, and do not catch exceptions from code that is not expected to raise them.
- When validation guarantees that a key exists, use direct key access (`data["key"]`) instead of `.get("key")`.
- Do not suggest defensive checks for input that service schemas already validate.
- Service actions that change device configuration should be registered as admin-only (`async_register_admin_service`).

## AI policy

A human must review, understand and be able to explain every change before it is submitted. Do not open issues or pull requests, or post comments on behalf of the maintainer, without their review.
