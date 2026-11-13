# Tests

The integration's tests run on Home Assistant itself, through
[pytest-homeassistant](https://github.com/home-assistant/core/tree/dev/packages/pytest-homeassistant),
Home Assistant's own test package:
a Home Assistant instance in memory, with a mocked Bluetooth stack and no real fountain.

| File | What it covers |
|------|----------------|
| `test_protocol.py` | frames from the fountain: malformed ones rejected, W4/W5 and CTW3 layouts, config writes allowed only after a complete CMD 211 answer, the commands that are sent |
| `test_adapter.py` | the connection rules: queued commands dropped on reconnect, a late disconnect of an old client ignored, a dead client disconnected, a non-blocking write queue, CMD 213 kept out of the log |
| `test_integration.py` | setup and unload, the admin-only actions and their device target, config writes refused before the config was read, while disconnected and on CTW3, the config and options flows |
| `test_hardening.py` | what must not come back: a reconnect outliving its config entry, an absent fountain filling the log, another Bluetooth device offered for setup as a fountain, an address or a smart mode time that is not one, short frames and short advertisement data, the pump runtime and firmware shown wrongly |

They need Linux (or WSL) and Python 3.14.2 or newer, like Home Assistant 2026.11:

```bash
python -m pip install -r requirements_test.txt
python -m pytest
```

GitHub Actions runs them on every pull request and on main (`.github/workflows/tests.yaml`).
Every bug fix comes with a test that fails without the fix.
