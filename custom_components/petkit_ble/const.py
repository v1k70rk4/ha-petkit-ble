"""Constants for the Petkit BLE integration."""

DOMAIN = "petkit_ble"

# Configuration keys
CONF_ADDRESS = "address"
CONF_UPDATE_INTERVAL = "update_interval"

# Default values
DEFAULT_SCAN_INTERVAL = 30  # seconds
DEFAULT_UPDATE_INTERVAL = 10  # seconds
HEARTBEAT_INTERVAL = 60     # seconds

# Device types
SUPPORTED_DEVICES = ["W4", "W5", "CTW2", "CTW3"]

# Entity attributes
ATTR_BATTERY = "battery"
ATTR_FILTER_PERCENTAGE = "filter_percentage"
ATTR_FILTER_TIME_LEFT = "filter_time_left"
ATTR_PUMP_RUNTIME = "pump_runtime"
ATTR_PUMP_RUNTIME_TODAY = "pump_runtime_today"
ATTR_PURIFIED_WATER = "purified_water"
ATTR_PURIFIED_WATER_TODAY = "purified_water_today"
ATTR_ENERGY_CONSUMED = "energy_consumed"
ATTR_RSSI = "rssi"
ATTR_VOLTAGE = "voltage"
ATTR_MODE = "mode"
ATTR_POWER_STATUS = "power_status"
ATTR_RUNNING_STATUS = "running_status"
ATTR_LED_BRIGHTNESS = "led_brightness"
ATTR_LED_SWITCH = "led_switch"
ATTR_DND_STATE = "dnd_state"
ATTR_IS_LOCKED = "is_locked"

# Warning attributes
ATTR_WARNING_BREAKDOWN = "warning_breakdown"
ATTR_WARNING_FILTER = "warning_filter"
ATTR_WARNING_WATER_MISSING = "warning_water_missing"

# Device modes
MODE_NORMAL = 1
MODE_SMART = 2

# Power states
POWER_OFF = 0
POWER_ON = 1

# Device capabilities
CAP_UVC = "uvc"
CAP_PET_DETECTION = "pet_detection"
CAP_BATTERY_TIMING = "battery_timing"
CAP_LED_SCHEDULE = "led_schedule"
CAP_DND_SCHEDULE = "dnd_schedule"


def get_device_capabilities(device) -> set[str]:
    """Determine device capabilities from device name and type."""
    name = getattr(device, "name", "") or ""
    device_type = getattr(device, "device_type", 0)

    # Base capabilities (all devices)
    caps: set[str] = set()

    # CTW3 series (device_type 24) has extra sensors
    if device_type == 24 or "CTW3" in name:
        caps.add(CAP_PET_DETECTION)
        caps.add(CAP_BATTERY_TIMING)

    # Non-CTW3 devices have LED/DND scheduling with byte pairs
    if device_type != 24 and "CTW3" not in name:
        caps.add(CAP_LED_SCHEDULE)
        caps.add(CAP_DND_SCHEDULE)

    # UVC models
    if "UVC" in name or "UV" in name:
        caps.add(CAP_UVC)

    return caps