"""Setup, services and config flow through Home Assistant."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError, Unauthorized

from custom_components.petkit_ble import (
    SERVICE_RESET_FILTER_SCHEMA,
    SERVICE_SET_DEVICE_CONFIG_SCHEMA,
)
from custom_components.petkit_ble.const import DOMAIN
from custom_components.petkit_ble.PetkitW5BLEMQTT.utils import Utils

from .conftest import ADDRESS, frame, make_writable, registry_device_id, w5_config_payload




@pytest.mark.parametrize("schema", [SERVICE_RESET_FILTER_SCHEMA, SERVICE_SET_DEVICE_CONFIG_SCHEMA])
@pytest.mark.parametrize("data", [{}, {"device_id": []}])
def test_services_require_a_device_target(schema, data):
    with pytest.raises(vol.Invalid):
        schema(data)


@pytest.mark.parametrize("value", [-1, 101, "abc"])
def test_led_brightness_is_range_checked(value):
    with pytest.raises(vol.Invalid):
        SERVICE_SET_DEVICE_CONFIG_SCHEMA({"device_id": "x", "led_brightness": value})


async def test_setup_registers_services_and_unload_removes_them(hass, loaded_entry):
    assert hass.services.has_service(DOMAIN, "reset_filter")
    assert hass.services.has_service(DOMAIN, "set_device_config")
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    assert not hass.services.has_service(DOMAIN, "reset_filter")
    assert not hass.services.has_service(DOMAIN, "set_device_config")
    assert loaded_entry.entry_id not in hass.data[DOMAIN]


@pytest.mark.parametrize("service", ["reset_filter", "set_device_config"])
async def test_services_are_admin_only(hass, loaded_entry, hass_read_only_user, service):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    producer = make_writable(coordinator)
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            service,
            {"device_id": registry_device_id(hass, loaded_entry)},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    producer.assert_not_awaited()


async def test_service_rejects_a_device_of_another_integration(hass, loaded_entry):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    producer = make_writable(coordinator)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "reset_filter", {"device_id": "not-a-petkit-device"}, blocking=True
        )
    producer.assert_not_awaited()


async def test_config_write_is_refused_before_the_config_was_read(hass, loaded_entry):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    producer = make_writable(coordinator)
    coordinator.device.config_received = False
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "set_device_config",
            {"device_id": registry_device_id(hass, loaded_entry), "is_locked": True},
            blocking=True,
        )
    producer.assert_not_awaited()


async def test_config_write_is_refused_while_disconnected(hass, loaded_entry):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    producer = make_writable(coordinator)
    coordinator.ble_manager.connected_devices.clear()
    with pytest.raises(HomeAssistantError):
        await coordinator.async_update_config(is_locked=1)
    producer.assert_not_awaited()


async def test_config_write_is_refused_on_ctw3(hass, loaded_entry):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    producer = make_writable(coordinator)
    coordinator.device.device_type = 24
    with pytest.raises(HomeAssistantError):
        await coordinator.async_update_config(is_locked=1)
    producer.assert_not_awaited()


async def test_config_write_resends_the_values_the_device_reported(hass, loaded_entry):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    reported = w5_config_payload(is_locked=0)
    await coordinator.event_handlers.handle_notification("s", frame(211, reported))
    producer = make_writable(coordinator)

    await hass.services.async_call(
        DOMAIN,
        "set_device_config",
        {"device_id": registry_device_id(hass, loaded_entry), "is_locked": True},
        blocking=True,
    )

    sent = Utils.parse_bytearray(producer.await_args.args[0])
    assert sent["cmd"] == 221
    assert list(sent["data"]) == [*reported[:13], 1]


async def test_bluetooth_discovery_of_another_device_is_rejected(hass, enable_bluetooth):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "bluetooth"},
        data=SimpleNamespace(name="Mi Smart Band", address="11:22:33:44:55:66"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_discovery_of_a_configured_fountain_aborts(hass, entry, enable_bluetooth):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "bluetooth"},
        data=SimpleNamespace(name="Petkit_W5", address=ADDRESS),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow_bounds_the_update_interval(hass, loaded_entry):
    for bad in (1, 4, 301):
        result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
        with pytest.raises(vol.Invalid):
            await hass.config_entries.options.async_configure(
                result["flow_id"], {"update_interval": bad}
            )
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    with patch("custom_components.petkit_ble.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"update_interval": 30}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["update_interval"] == 30
