"""Petkit BLE Water Fountain integration for Home Assistant."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN
from .coordinator import PetkitBLECoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.TIME,
    Platform.SELECT,
]

# Service schemas
SERVICE_RESET_FILTER = "reset_filter"
SERVICE_SET_DEVICE_CONFIG = "set_device_config"

ATTR_DEVICE_ID = "device_id"
DEVICE_IDS = vol.All(cv.ensure_list, [cv.string], vol.Length(min=1))

# A device target is mandatory: these services change device settings, so an
# empty target must never fall through to "every fountain".
SERVICE_RESET_FILTER_SCHEMA = vol.Schema(
    {vol.Required(ATTR_DEVICE_ID): DEVICE_IDS},
    extra=vol.ALLOW_EXTRA,
)

SERVICE_SET_DEVICE_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): DEVICE_IDS,
        vol.Optional("smart_time_on"): cv.positive_int,
        vol.Optional("smart_time_off"): cv.positive_int,
        vol.Optional("led_brightness"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Optional("led_switch"): cv.boolean,
        vol.Optional("do_not_disturb"): cv.boolean,
        vol.Optional("is_locked"): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)


def _get_coordinators_from_service_call(
    hass: HomeAssistant, call: ServiceCall
) -> list[PetkitBLECoordinator]:
    """Resolve target device_ids to coordinators.

    Every targeted device must belong to a loaded entry of this integration;
    otherwise the call is rejected before anything is changed.
    """
    dev_reg = dr.async_get(hass)
    entries = hass.data.get(DOMAIN, {})
    coordinators: list[PetkitBLECoordinator] = []
    for device_id in call.data[ATTR_DEVICE_ID]:
        device_entry = dev_reg.async_get(device_id)
        matches = [
            entries[entry_id]
            for entry_id in (device_entry.config_entries if device_entry else ())
            if entry_id in entries
        ]
        if not matches:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
            )
        coordinators.extend(c for c in matches if c not in coordinators)
    return coordinators


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Petkit BLE from a config entry."""
    coordinator = PetkitBLECoordinator(hass, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start the coordinator
    entry.async_create_task(hass, coordinator.async_start())

    # Only register services once (first entry)
    if len(hass.data[DOMAIN]) > 1:
        return True

    async def handle_reset_filter(call: ServiceCall) -> None:
        """Handle the reset filter service call."""
        for coord in _get_coordinators_from_service_call(hass, call):
            await coord.async_reset_filter()
            await coord.async_request_refresh()

    async def handle_set_device_config(call: ServiceCall) -> None:
        """Handle the set device config service call."""
        for coord in _get_coordinators_from_service_call(hass, call):
            current_config = coord.device.config

            smart_time_on = call.data.get("smart_time_on", current_config.get("smart_time_on", 30))
            smart_time_off = call.data.get("smart_time_off", current_config.get("smart_time_off", 60))
            led_brightness = call.data.get("led_brightness", current_config.get("led_brightness", 80))
            led_switch = int(call.data.get("led_switch", current_config.get("led_switch", 1)))
            do_not_disturb = int(call.data.get("do_not_disturb", current_config.get("do_not_disturb_switch", 0)))
            is_locked = int(call.data.get("is_locked", current_config.get("is_locked", 0)))

            config_data = [
                smart_time_on,
                smart_time_off,
                led_switch,
                led_brightness,
                current_config.get("led_on_byte1", 0),
                current_config.get("led_on_byte2", 0),
                current_config.get("led_off_byte1", 0),
                current_config.get("led_off_byte2", 0),
                do_not_disturb,
                current_config.get("dnd_on_byte1", 0),
                current_config.get("dnd_on_byte2", 0),
                current_config.get("dnd_off_byte1", 0),
                current_config.get("dnd_off_byte2", 0),
                is_locked,
            ]

            await coord.async_set_device_config(config_data)
            await coord.async_request_refresh()

    # Admin-only: these services change device settings directly, so a
    # restricted (non-admin) Home Assistant user must not be able to call them.
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RESET_FILTER,
        handle_reset_filter,
        schema=SERVICE_RESET_FILTER_SCHEMA,
    )

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SET_DEVICE_CONFIG,
        handle_set_device_config,
        schema=SERVICE_SET_DEVICE_CONFIG_SCHEMA,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()

        # Remove services if no more entries
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_RESET_FILTER)
            hass.services.async_remove(DOMAIN, SERVICE_SET_DEVICE_CONFIG)

    return unload_ok
