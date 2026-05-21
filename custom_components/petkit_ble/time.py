"""Time platform for Petkit BLE integration."""
from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
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
    """Set up Petkit BLE time entities."""
    coordinator: PetkitBLECoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PetkitLedOnTime(coordinator),
        PetkitLedOffTime(coordinator),
        PetkitDndStartTime(coordinator),
        PetkitDndEndTime(coordinator),
    ]

    async_add_entities(entities)


class PetkitTimeBase(CoordinatorEntity[PetkitBLECoordinator], TimeEntity):
    """Base class for Petkit time entities."""

    _attr_has_entity_name = True
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the time entity."""
        super().__init__(coordinator)

    def _get_device_id(self) -> str:
        if self.coordinator.device.serial != "Uninitialized":
            return self.coordinator.device.serial
        return self.coordinator.address.replace(":", "")

    @property
    def device_info(self) -> DeviceInfo:
        device_id = self.coordinator.device.serial if self.coordinator.device.serial != "Uninitialized" else self.coordinator.address
        device_name = self.coordinator.device.name_readable if self.coordinator.device.name_readable != "Uninitialized" else "Water Fountain"
        return {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_name,
            "manufacturer": "Petkit",
            "model": self.coordinator.device.product_name or "Water Fountain",
            "sw_version": str(self.coordinator.device.firmware) if self.coordinator.device.firmware else "Unknown",
        }

    @staticmethod
    def _minutes_to_time(total_minutes: int) -> time | None:
        """Convert total minutes since midnight to time object."""
        if total_minutes is None or total_minutes == 0:
            return None
        hours = (total_minutes // 60) % 24
        minutes = total_minutes % 60
        return time(hour=hours, minute=minutes)

    @staticmethod
    def _time_to_bytes(t: time) -> tuple[int, int]:
        """Convert time object to byte pair (big-endian total minutes)."""
        total_minutes = t.hour * 60 + t.minute
        return total_minutes >> 8, total_minutes & 0xFF


class PetkitLedOnTime(PetkitTimeBase):
    """LED on time entity."""

    _attr_icon = "mdi:lightbulb-on-outline"

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_led_on_time"
        self._attr_translation_key = "led_on_time"

    @property
    def native_value(self) -> time | None:
        minutes = self.coordinator.current_data.get("status", {}).get("led_light_time_on")
        return self._minutes_to_time(minutes)

    async def async_set_value(self, value: time) -> None:
        b1, b2 = self._time_to_bytes(value)
        await self.coordinator.async_update_config(led_on_byte1=b1, led_on_byte2=b2)
        total = value.hour * 60 + value.minute
        self.coordinator.device._led_light_time_on = total
        self.coordinator.device._led_on_byte1 = b1
        self.coordinator.device._led_on_byte2 = b2
        self.async_write_ha_state()


class PetkitLedOffTime(PetkitTimeBase):
    """LED off time entity."""

    _attr_icon = "mdi:lightbulb-off-outline"

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_led_off_time"
        self._attr_translation_key = "led_off_time"

    @property
    def native_value(self) -> time | None:
        minutes = self.coordinator.current_data.get("status", {}).get("led_light_time_off")
        return self._minutes_to_time(minutes)

    async def async_set_value(self, value: time) -> None:
        b1, b2 = self._time_to_bytes(value)
        await self.coordinator.async_update_config(led_off_byte1=b1, led_off_byte2=b2)
        total = value.hour * 60 + value.minute
        self.coordinator.device._led_light_time_off = total
        self.coordinator.device._led_off_byte1 = b1
        self.coordinator.device._led_off_byte2 = b2
        self.async_write_ha_state()


class PetkitDndStartTime(PetkitTimeBase):
    """DND start time entity."""

    _attr_icon = "mdi:moon-waning-crescent"

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_dnd_start_time"
        self._attr_translation_key = "dnd_start_time"

    @property
    def native_value(self) -> time | None:
        minutes = self.coordinator.current_data.get("status", {}).get("do_not_disturb_time_on")
        return self._minutes_to_time(minutes)

    async def async_set_value(self, value: time) -> None:
        b1, b2 = self._time_to_bytes(value)
        await self.coordinator.async_update_config(dnd_on_byte1=b1, dnd_on_byte2=b2)
        total = value.hour * 60 + value.minute
        self.coordinator.device._do_not_disturb_time_on = total
        self.coordinator.device._dnd_on_byte1 = b1
        self.coordinator.device._dnd_on_byte2 = b2
        self.async_write_ha_state()


class PetkitDndEndTime(PetkitTimeBase):
    """DND end time entity."""

    _attr_icon = "mdi:moon-first-quarter"

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{self._get_device_id()}_dnd_end_time"
        self._attr_translation_key = "dnd_end_time"

    @property
    def native_value(self) -> time | None:
        minutes = self.coordinator.current_data.get("status", {}).get("do_not_disturb_time_off")
        return self._minutes_to_time(minutes)

    async def async_set_value(self, value: time) -> None:
        b1, b2 = self._time_to_bytes(value)
        await self.coordinator.async_update_config(dnd_off_byte1=b1, dnd_off_byte2=b2)
        total = value.hour * 60 + value.minute
        self.coordinator.device._do_not_disturb_time_off = total
        self.coordinator.device._dnd_off_byte1 = b1
        self.coordinator.device._dnd_off_byte2 = b2
        self.async_write_ha_state()
