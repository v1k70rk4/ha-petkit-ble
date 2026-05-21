"""Switch platform for Petkit BLE integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, MODE_NORMAL, MODE_SMART, POWER_OFF, POWER_ON
from .coordinator import PetkitBLECoordinator

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Petkit BLE switches."""
    coordinator: PetkitBLECoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PetkitPowerSwitch(coordinator),
        PetkitSmartModeSwitch(coordinator),
        PetkitLedSwitch(coordinator),
        PetkitDndSwitch(coordinator),
        PetkitChildLockSwitch(coordinator),
    ]

    async_add_entities(entities)

class PetkitSwitchBase(CoordinatorEntity[PetkitBLECoordinator], SwitchEntity):
    """Base class for Petkit switches."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the switch."""
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

class PetkitPowerSwitch(PetkitSwitchBase):
    """Power switch for the water fountain."""

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the power switch."""
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_power"
        self._attr_translation_key = "power"
        self._attr_icon = "mdi:power"

    @property
    def is_on(self) -> bool | None:
        """Return true if the fountain is on."""
        power_status = self.coordinator.current_data.get("status", {}).get("power_status")
        return power_status == POWER_ON if power_status is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the fountain on."""
        current_mode = self.coordinator.current_data.get("status", {}).get("mode", MODE_NORMAL)
        await self.coordinator.async_set_device_mode(POWER_ON, current_mode)
        self.coordinator.device._power_status = POWER_ON
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fountain off."""
        current_mode = self.coordinator.current_data.get("status", {}).get("mode", MODE_NORMAL)
        await self.coordinator.async_set_device_mode(POWER_OFF, current_mode)
        self.coordinator.device._power_status = POWER_OFF
        self.async_write_ha_state()

class PetkitSmartModeSwitch(PetkitSwitchBase):
    """Smart mode switch for the water fountain."""

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        """Initialize the smart mode switch."""
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_smart_mode"
        self._attr_translation_key = "smart_mode"
        self._attr_icon = "mdi:brain"

    @property
    def is_on(self) -> bool | None:
        """Return true if smart mode is enabled."""
        mode = self.coordinator.current_data.get("status", {}).get("mode")
        return mode == MODE_SMART if mode is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable smart mode."""
        current_power = self.coordinator.current_data.get("status", {}).get("power_status", POWER_ON)
        await self.coordinator.async_set_device_mode(current_power, MODE_SMART)
        self.coordinator.device._mode = MODE_SMART
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable smart mode (switch to normal mode)."""
        current_power = self.coordinator.current_data.get("status", {}).get("power_status", POWER_ON)
        await self.coordinator.async_set_device_mode(current_power, MODE_NORMAL)
        self.coordinator.device._mode = MODE_NORMAL
        self.async_write_ha_state()


class PetkitConfigSwitch(PetkitSwitchBase):
    """Base class for switches that modify device config via CMD 221."""

    async def _send_config(self, **overrides) -> None:
        """Send full config with overrides, keeping everything else unchanged."""
        await self.coordinator.async_update_config(**overrides)


class PetkitLedSwitch(PetkitConfigSwitch):
    """LED switch for the water fountain."""

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_led_switch"
        self._attr_translation_key = "led_switch"
        self._attr_icon = "mdi:led-on"

    @property
    def is_on(self) -> bool | None:
        led_switch = self.coordinator.current_data.get("status", {}).get("led_switch")
        return led_switch == 1 if led_switch is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send_config(led_switch=1)
        self.coordinator.device._led_switch = 1
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_config(led_switch=0)
        self.coordinator.device._led_switch = 0
        self.async_write_ha_state()


class PetkitDndSwitch(PetkitConfigSwitch):
    """Do Not Disturb switch for the water fountain."""

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_dnd_switch"
        self._attr_translation_key = "do_not_disturb"
        self._attr_icon = "mdi:moon-waning-crescent"

    @property
    def is_on(self) -> bool | None:
        dnd = self.coordinator.current_data.get("status", {}).get("do_not_disturb_switch")
        return dnd == 1 if dnd is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send_config(do_not_disturb_switch=1)
        self.coordinator.device._do_not_disturb_switch = 1
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_config(do_not_disturb_switch=0)
        self.coordinator.device._do_not_disturb_switch = 0
        self.async_write_ha_state()


class PetkitChildLockSwitch(PetkitConfigSwitch):
    """Child lock switch for the water fountain."""

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_child_lock"
        self._attr_translation_key = "child_lock"
        self._attr_icon = "mdi:lock"

    @property
    def is_on(self) -> bool | None:
        locked = self.coordinator.current_data.get("status", {}).get("is_locked")
        return locked == 1 if locked is not None else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._send_config(is_locked=1)
        self.coordinator.device._is_locked = 1
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send_config(is_locked=0)
        self.coordinator.device._is_locked = 0
        self.async_write_ha_state()
