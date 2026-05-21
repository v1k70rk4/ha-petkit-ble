"""Binary sensor platform for Petkit BLE integration."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    """Set up Petkit BLE binary sensors."""
    coordinator: PetkitBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        PetkitFilterProblemSensor(coordinator),
        PetkitWaterMissingSensor(coordinator),
        PetkitBreakdownSensor(coordinator),
        PetkitRunningSensor(coordinator),
        # CTW3 sensors (disabled by default, enable for CTW3 devices)
        PetkitPetDetectedSensor(coordinator),
        PetkitAcPowerSensor(coordinator),
        PetkitLowBatterySensor(coordinator),
        PetkitSuspendedSensor(coordinator),
    ]

    async_add_entities(entities)

class PetkitBinarySensorBase(CoordinatorEntity[PetkitBLECoordinator], BinarySensorEntity):
    """Base class for Petkit binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the binary sensor."""
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

class PetkitFilterProblemSensor(PetkitBinarySensorBase):
    """Filter problem binary sensor."""
    
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the filter problem sensor."""
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_filter_problem"
        self._attr_translation_key = "filter_problem"
        self._attr_icon = "mdi:air-filter"
    
    @property
    def is_on(self) -> bool | None:
        """Return true if there's a filter problem."""
        warning = self.coordinator.current_data.get("status", {}).get("warning_filter")
        return bool(warning) if warning is not None else None

class PetkitWaterMissingSensor(PetkitBinarySensorBase):
    """Water missing binary sensor."""
    
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the water missing sensor."""
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_water_missing"
        self._attr_translation_key = "water_missing"
        self._attr_icon = "mdi:water-alert"
    
    @property
    def is_on(self) -> bool | None:
        """Return true if water is missing."""
        warning = self.coordinator.current_data.get("status", {}).get("warning_water_missing")
        return bool(warning) if warning is not None else None

class PetkitBreakdownSensor(PetkitBinarySensorBase):
    """Breakdown binary sensor."""
    
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the breakdown sensor."""
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_breakdown"
        self._attr_translation_key = "breakdown"
        self._attr_icon = "mdi:alert-circle"
    
    @property
    def is_on(self) -> bool | None:
        """Return true if there's a breakdown."""
        warning = self.coordinator.current_data.get("status", {}).get("warning_breakdown")
        return bool(warning) if warning is not None else None

class PetkitRunningSensor(PetkitBinarySensorBase):
    """Running status binary sensor."""
    
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    
    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the running sensor."""
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_running"
        self._attr_translation_key = "running"
        self._attr_icon = "mdi:play-circle"
    
    @property
    def is_on(self) -> bool | None:
        """Return true if the fountain is running."""
        running_status = self.coordinator.current_data.get("status", {}).get("running_status")
        return bool(running_status) if running_status is not None else None


class PetkitPetDetectedSensor(PetkitBinarySensorBase):
    """Pet detected binary sensor (CTW3 only)."""

    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:cat"

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_pet_detected"
        self._attr_translation_key = "pet_detected"

    @property
    def is_on(self) -> bool | None:
        val = self.coordinator.current_data.get("status", {}).get("detect_status")
        return bool(val) if val is not None else None


class PetkitAcPowerSensor(PetkitBinarySensorBase):
    """AC power binary sensor (CTW3 only)."""

    _attr_device_class = BinarySensorDeviceClass.PLUG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_ac_power"
        self._attr_translation_key = "on_ac_power"

    @property
    def is_on(self) -> bool | None:
        val = self.coordinator.current_data.get("status", {}).get("electric_status")
        return bool(val) if val is not None else None


class PetkitLowBatterySensor(PetkitBinarySensorBase):
    """Low battery binary sensor (CTW3 only)."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_low_battery"
        self._attr_translation_key = "low_battery"

    @property
    def is_on(self) -> bool | None:
        val = self.coordinator.current_data.get("status", {}).get("low_battery")
        return bool(val) if val is not None else None


class PetkitSuspendedSensor(PetkitBinarySensorBase):
    """Pump suspended binary sensor (CTW3 only)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:pump-off"

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_suspended"
        self._attr_translation_key = "suspended"

    @property
    def is_on(self) -> bool | None:
        val = self.coordinator.current_data.get("status", {}).get("suspend_status")
        return bool(val) if val is not None else None