# Contributing

Thanks for wanting to help. Bug reports, ideas and pull requests are all welcome.

## Questions, bugs and ideas

Open an [issue](https://github.com/v1k70rk4/ha-petkit-ble/issues/new). For a bug, these help the most:

- the versions of the integration and Home Assistant,
- the fountain model (W4, W5, CTW2, CTW3) and its firmware,
- how Home Assistant reaches it: a local Bluetooth adapter or an ESPHome Bluetooth proxy,
- the Home Assistant log lines for `custom_components.petkit_ble`, with debug logging on (see [Debug logging](readme.md#debug-logging)).

**Security problems are not reported in issues.** See [SECURITY.md](SECURITY.md) for the private way.

## Pull requests

1. Fork the repository and branch off `main`.
2. Keep a pull request to one change. Say what it changes and why; for a bug, how to reproduce it.
3. Open the pull request against `main`. CI runs the tests, Ruff, hassfest, the HACS validation and CodeQL, and it gets a review before it is merged.
   The first review comes from CodeRabbit, an automated reviewer. Its comments are suggestions: fix what it rightly points out, and if you don't agree with one, say why in a short reply before you resolve the thread. That way a reviewer can tell a choice from an oversight.

Before you open it:

- **Ruff is clean:** `ruff check custom_components/petkit_ble`, with the version pinned in `.github/workflows/ruff.yaml`.
- **The tests pass:** `python -m pytest`, see [`tests/README.md`](tests/README.md). A bug fix comes with a test that fails without it.
- **Test it on a real fountain** and say which model and firmware you used. The tests run without a device, so they cannot tell how a real one behaves.
- **Mind the BLE rules** in [`.github/copilot-instructions.md`](.github/copilot-instructions.md): the fountain takes one connection at a time, and every awaited BLE call needs a timeout. They come from real failures.
- **Changes to the device protocol** go into `custom_components/petkit_ble/PetkitW5BLEMQTT/`.
- **Text the user sees** goes into every file under `custom_components/petkit_ble/translations/` under the same key. If you don't speak a language, put the English text there and say so in the pull request.
- **Leave the version numbers and the changelog alone.** They are updated when a release is made.

## License

By contributing you agree that your contribution is released under the project's [MIT License](LICENSE).
