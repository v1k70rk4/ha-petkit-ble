"""Select platform for Petkit BLE integration."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, MODE_NORMAL, MODE_SMART, POWER_ON
from .coordinator import PetkitBLECoordinator

MODE_MAP = {
    "normal": MODE_NORMAL,
    "smart": MODE_SMART,
}
MODE_REVERSE = {v: k for k, v in MODE_MAP.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Petkit BLE select entities."""
    coordinator: PetkitBLECoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PetkitModeSelect(coordinator)])


class PetkitModeSelect(CoordinatorEntity[PetkitBLECoordinator], SelectEntity):
    """Mode select entity for the water fountain."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:fountain"
    _attr_options = ["normal", "smart"]
    _attr_translation_key = "mode"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: PetkitBLECoordinator) -> None:
        super().__init__(coordinator)
        device_id = coordinator.device.serial if coordinator.device.serial != "Uninitialized" else coordinator.address.replace(":", "")
        self._attr_unique_id = f"{device_id}_mode"

    @property
    def device_info(self) -> DeviceInfo:
        device_id = self.coordinator.device.serial if self.coordinator.device.serial != "Uninitialized" else self.coordinator.address
        device_name = self.coordinator.device.name_readable if self.coordinator.device.name_readable != "Uninitialized" else "Water Fountain"
        return {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_name,
            "manufacturer": "Petkit",
            "model": self.coordinator.device.product_name if self.coordinator.device.product_name not in (None, "", "Uninitialized") else "Water Fountain",
            "sw_version": str(self.coordinator.device.firmware) if self.coordinator.device.firmware else "Unknown",
        }

    @property
    def current_option(self) -> str | None:
        mode = self.coordinator.current_data.get("status", {}).get("mode")
        return MODE_REVERSE.get(mode)

    async def async_select_option(self, option: str) -> None:
        mode = MODE_MAP.get(option, MODE_NORMAL)
        current_power = self.coordinator.current_data.get("status", {}).get("power_status", POWER_ON)
        await self.coordinator.async_set_device_mode(current_power, mode)
        self.coordinator.device._mode = mode
        self.async_write_ha_state()
