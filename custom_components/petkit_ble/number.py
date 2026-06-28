"""Number platform for Petkit BLE integration."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
    """Set up Petkit BLE number entities."""
    coordinator: PetkitBLECoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PetkitLedBrightnessNumber(coordinator),
        PetkitSmartWorkMinutesNumber(coordinator),
        PetkitSmartSleepMinutesNumber(coordinator),
    ]

    async_add_entities(entities)


class PetkitNumberBase(CoordinatorEntity[PetkitBLECoordinator], NumberEntity):
    """Base class for Petkit number entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)

    def _get_device_id(self) -> str:
        """Get device ID for unique_id generation."""
        if self.coordinator.device.serial != "Uninitialized":
            return self.coordinator.device.serial
        return self.coordinator.address.replace(":", "")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info dynamically."""
        device_id = self.coordinator.device.serial if self.coordinator.device.serial != "Uninitialized" else self.coordinator.address
        device_name = self.coordinator.device.name_readable if self.coordinator.device.name_readable != "Uninitialized" else "Water Fountain"
        return {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_name,
            "manufacturer": "Petkit",
            "model": self.coordinator.device.product_name if self.coordinator.device.product_name not in (None, "", "Uninitialized") else "Water Fountain",
            "sw_version": str(self.coordinator.device.firmware) if self.coordinator.device.firmware else "Unknown",
        }


class PetkitLedBrightnessNumber(PetkitNumberBase):
    """LED brightness number entity."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:brightness-6"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_led_brightness"
        self._attr_translation_key = "led_brightness"

    @property
    def native_value(self) -> float | None:
        val = self.coordinator.current_data.get("status", {}).get("led_brightness")
        return val if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_update_config(led_brightness=int(value))
        self.coordinator.device._led_brightness = int(value)
        self.async_write_ha_state()


class PetkitSmartWorkMinutesNumber(PetkitNumberBase):
    """Smart mode work time number entity."""

    _attr_native_min_value = 1
    _attr_native_max_value = 120
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-play"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_smart_work_minutes"
        self._attr_translation_key = "smart_work_minutes"

    @property
    def native_value(self) -> float | None:
        val = self.coordinator.current_data.get("status", {}).get("smart_time_on")
        return val if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_update_config(smart_time_on=int(value))
        self.coordinator.device._smart_time_on = int(value)
        self.async_write_ha_state()


class PetkitSmartSleepMinutesNumber(PetkitNumberBase):
    """Smart mode sleep time number entity."""

    _attr_native_min_value = 1
    _attr_native_max_value = 120
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-pause"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_smart_sleep_minutes"
        self._attr_translation_key = "smart_sleep_minutes"

    @property
    def native_value(self) -> float | None:
        val = self.coordinator.current_data.get("status", {}).get("smart_time_off")
        return val if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_update_config(smart_time_off=int(value))
        self.coordinator.device._smart_time_off = int(value)
        self.async_write_ha_state()
