"""Sensor platform for Petkit BLE integration."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfTime, UnitOfVolume
from homeassistant.core import HomeAssistant
from datetime import datetime
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN
from .coordinator import PetkitBLECoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Petkit BLE sensors."""
    coordinator: PetkitBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        PetkitBatteryLevelSensor(coordinator),
        PetkitFilterPercentageSensor(coordinator),
        PetkitFilterTimeLeftSensor(coordinator),
        PetkitPumpRuntimeSensor(coordinator),
        PetkitPumpRuntimeTodaySensor(coordinator),
        PetkitPurifiedWaterSensor(coordinator),
        PetkitPurifiedWaterTodaySensor(coordinator),
        PetkitEnergyConsumedSensor(coordinator),
        PetkitRSSISensor(coordinator),
        PetkitVoltageSensor(coordinator),
        PetkitConnectionStatusSensor(coordinator),
        PetkitConnectionAttemptsSensor(coordinator),
        PetkitLastSeenSensor(coordinator),
        PetkitFirmwareSensor(coordinator),
    ]
    
    async_add_entities(entities)

class PetkitSensorBase(CoordinatorEntity[PetkitBLECoordinator], SensorEntity):
    """Base class for Petkit sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info dynamically."""
        device_id = self.coordinator.device.serial if self.coordinator.device.serial != "Uninitialized" else self.coordinator.address
        device_name = self.coordinator.device.name_readable if self.coordinator.device.name_readable != "Uninitialized" else "Water Fountain"
        return {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_name,
            "manufacturer": "Petkit",
            "model": self.coordinator.device.product_name or "Water Fountain",
            "sw_version": str(self.coordinator.device.firmware) if self.coordinator.device.firmware else "Unknown",
        }

    def _get_device_id(self) -> str:
        """Get device ID for unique_id generation."""
        if self.coordinator.device.serial != "Uninitialized":
            return self.coordinator.device.serial
        return self.coordinator.address.replace(":", "")

    @staticmethod
    def _round_float(value, precision: int = 2) -> float | None:
        """Return a rounded float value for display-friendly sensors."""
        if value is None:
            return None
        try:
            return round(float(value), precision)
        except (TypeError, ValueError):
            return None

class PetkitBatteryLevelSensor(PetkitSensorBase):
    """Battery level sensor."""
    
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the battery sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_battery"
        self._attr_translation_key = "battery"
    
    @property
    def native_value(self) -> int | None:
        """Return the battery level."""
        return self.coordinator.current_data.get("status", {}).get("battery")

class PetkitFilterPercentageSensor(PetkitSensorBase):
    """Filter percentage sensor."""
    
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:air-filter"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the filter percentage sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_filter_percentage"
        self._attr_translation_key = "filter_remaining"
    
    @property
    def native_value(self) -> float | None:
        """Return the filter percentage remaining."""
        raw_value = self.coordinator.current_data.get("status", {}).get("filter_percentage")
        if raw_value is not None:
            if raw_value <= 1.0:
                # W4/W5/CTW2 parser returns 0-1 range (e.g. 0.97 = 97%)
                return round(raw_value * 100, 1)
            else:
                # CTW3 parser returns already as percentage (e.g. 13 = 13%)
                return round(float(raw_value), 1)
        return None

class PetkitFilterTimeLeftSensor(PetkitSensorBase):
    """Filter time left sensor."""
    
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:clock-outline"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the filter time left sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_filter_time_left"
        self._attr_translation_key = "filter_days_left"
    
    @property
    def native_value(self) -> int | None:
        """Return the filter time left in days."""
        return self.coordinator.current_data.get("status", {}).get("filter_time_left")

class PetkitPumpRuntimeSensor(PetkitSensorBase):
    """Pump runtime sensor."""
    
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:pump"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the pump runtime sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_pump_runtime"
        self._attr_translation_key = "pump_total_runtime"
    
    @property
    def native_value(self) -> float | None:
        """Return the pump runtime in hours."""
        readable = self.coordinator.current_data.get("status", {}).get("pump_runtime_readable")
        if readable and isinstance(readable, str):
            # Parse "3 days, 19 hours" or "5 hours" to numeric hours
            try:
                total_hours = 0
                if "day" in readable:
                    parts = readable.split(", ")
                    days_part = parts[0].split()[0]
                    total_hours += int(days_part) * 24
                    if len(parts) > 1:
                        hours_part = parts[1].split()[0]
                        total_hours += int(hours_part)
                elif "hour" in readable:
                    hours_part = readable.split()[0]
                    total_hours = int(hours_part)
                return round(float(total_hours), 1)
            except (ValueError, IndexError):
                return None
        # Handle numeric values directly (in seconds, convert to hours)
        raw_runtime = self.coordinator.current_data.get("status", {}).get("pump_runtime")
        if raw_runtime is not None and isinstance(raw_runtime, (int, float)):
            return round(float(raw_runtime) / 3600, 1)  # Convert seconds to hours
        return None

class PetkitPumpRuntimeTodaySensor(PetkitSensorBase):
    """Pump runtime today sensor."""
    
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:pump"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the pump runtime today sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_pump_runtime_today"
        self._attr_translation_key = "pump_today_runtime"
    
    @property
    def native_value(self) -> float | None:
        """Return the pump runtime today in hours."""
        readable = self.coordinator.current_data.get("status", {}).get("pump_runtime_today_readable")
        if readable and isinstance(readable, str):
            # Parse "5:50h" to numeric hours (5.83)
            try:
                if ":" in readable:
                    # Format like "5:50h"
                    time_part = readable.replace("h", "")
                    hours, minutes = time_part.split(":")
                    return round(float(hours) + float(minutes) / 60.0, 2)
                elif "h" in readable:
                    # Format like "5h"
                    hours_part = readable.replace("h", "")
                    return round(float(hours_part), 2)
                else:
                    # Try to parse as plain number
                    return round(float(readable), 2)
            except (ValueError, IndexError):
                return None
        # Handle numeric values directly (in seconds, convert to hours)
        raw_runtime = self.coordinator.current_data.get("status", {}).get("pump_runtime_today")
        if raw_runtime is not None and isinstance(raw_runtime, (int, float)):
            return round(float(raw_runtime) / 3600, 2)  # Convert seconds to hours
        return None

class PetkitPurifiedWaterSensor(PetkitSensorBase):
    """Purified water sensor."""
    
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:water"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the purified water sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_purified_water"
        self._attr_translation_key = "total_water_purified"
    
    @property
    def native_value(self) -> float | None:
        """Return the total purified water."""
        return self._round_float(
            self.coordinator.current_data.get("status", {}).get("purified_water")
        )

class PetkitPurifiedWaterTodaySensor(PetkitSensorBase):
    """Purified water today sensor."""
    
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:water"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the purified water today sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_purified_water_today"
        self._attr_translation_key = "water_purified_today"
    
    @property
    def native_value(self) -> float | None:
        """Return today's purified water."""
        return self._round_float(
            self.coordinator.current_data.get("status", {}).get("purified_water_today")
        )

class PetkitEnergyConsumedSensor(PetkitSensorBase):
    """Energy consumed sensor."""
    
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the energy consumed sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_energy_consumed"
        self._attr_translation_key = "energy_consumption"
    
    @property
    def native_value(self) -> float | None:
        """Return the energy consumed."""
        return self.coordinator.current_data.get("status", {}).get("energy_consumed")

class PetkitRSSISensor(PetkitSensorBase):
    """RSSI sensor."""
    
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the RSSI sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_rssi"
        self._attr_translation_key = "signal_strength"
    
    @property
    def native_value(self) -> int | None:
        """Return the RSSI value."""
        return self.coordinator.current_data.get("status", {}).get("rssi")

class PetkitVoltageSensor(PetkitSensorBase):
    """Voltage sensor."""
    
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_native_unit_of_measurement = "V"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the voltage sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_voltage"
        self._attr_translation_key = "voltage"
    
    @property
    def native_value(self) -> float | None:
        """Return the voltage."""
        return self.coordinator.current_data.get("status", {}).get("voltage")

class PetkitConnectionStatusSensor(PetkitSensorBase):
    """Connection status sensor."""
    
    _attr_icon = "mdi:bluetooth-connect"
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the connection status sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_connection_status"
        self._attr_translation_key = "connection"
    
    @property
    def native_value(self) -> str | None:
        """Return the connection status."""
        return self.coordinator.current_data.get("status", {}).get("connection_status", "unknown")

class PetkitConnectionAttemptsSensor(PetkitSensorBase):
    """Connection attempts sensor."""
    
    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the connection attempts sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_connection_attempts"
        self._attr_translation_key = "connection_attempts"
    
    @property
    def native_value(self) -> int | None:
        """Return the number of connection attempts."""
        return self.coordinator.current_data.get("status", {}).get("connection_attempts", 0)

class PetkitLastSeenSensor(PetkitSensorBase):
    """Last seen sensor."""
    
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"
    _attr_entity_registry_enabled_default = False
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the last seen sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_last_seen"
        self._attr_translation_key = "last_seen"
    
    @property
    def native_value(self) -> datetime | None:
        """Return the last seen timestamp."""
        last_seen = self.coordinator.current_data.get("status", {}).get("last_seen")
        if last_seen:
            # Handle both timestamp (float) and ISO string formats for backward compatibility
            if isinstance(last_seen, str):
                try:
                    from datetime import datetime as dt
                    # Parse ISO format string to datetime
                    return dt.fromisoformat(last_seen.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    return None
            else:
                # Legacy numeric timestamp format
                return datetime.fromtimestamp(last_seen)
        return None


class PetkitFirmwareSensor(PetkitSensorBase):
    """Firmware version sensor."""

    _attr_icon = "mdi:chip"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the firmware sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_firmware"
        self._attr_translation_key = "firmware"

    @property
    def native_value(self) -> str | None:
        """Return the firmware version."""
        fw = self.coordinator.current_data.get("firmware")
        return str(fw) if fw else None
