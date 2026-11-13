"""Constants for the Petkit BLE integration."""

DOMAIN = "petkit_ble"

# hass.data key: per Bluetooth address, the clients that could not be
# disconnected. Kept outside the config entry's own data so that it outlives
# an unload and the entry's next setup can disconnect them.
DATA_UNDISCONNECTED = f"{DOMAIN}_undisconnected"

# Configuration keys
CONF_ADDRESS = "address"
CONF_UPDATE_INTERVAL = "update_interval"

# Default values
DEFAULT_UPDATE_INTERVAL = 10  # seconds

# A fountain advertises its model as its Bluetooth name (Petkit_W4X, Petkit_CTW3_100, ...).
# The Bluetooth matchers in manifest.json list the same prefixes.
SUPPORTED_NAME_PREFIXES = ("Petkit_W4", "Petkit_W5", "Petkit_CTW2", "Petkit_CTW3")


def is_supported_device_name(name: str | None) -> bool:
    """Return True if a Bluetooth name is that of a supported fountain.

    Matched from the start of the name: a model code somewhere inside the name
    of an unrelated device must not offer it for setup as a fountain.
    """
    return bool(name) and name.startswith(SUPPORTED_NAME_PREFIXES)


# Device modes
MODE_NORMAL = 1
MODE_SMART = 2

# Power states
POWER_OFF = 0
POWER_ON = 1
