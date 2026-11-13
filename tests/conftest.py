"""Shared fixtures for the Petkit BLE tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant.common import MockConfigEntry

from custom_components.petkit_ble.const import DOMAIN

ADDRESS = "A4:C1:38:00:11:22"


def frame(cmd: int, data: list[int] | bytes, type_: int = 2, seq: int = 0) -> bytearray:
    """Build a notification frame the way the fountain sends it."""
    data = list(data)
    return bytearray([0xFA, 0xFC, 0xFD, cmd, type_, seq, len(data), 0, *data, 0xFB])


def w5_config_payload(*, is_locked: int | None = 0) -> list[int]:
    """CMD 211 payload of a W4/W5: 13 bytes, plus the child lock byte."""
    payload = [30, 60, 1, 80, 0x01, 0xE0, 0x05, 0x28, 0, 0x05, 0x28, 0x01, 0xE0]
    if is_locked is not None:
        payload.append(is_locked)
    return payload


def w5_status_payload(*, pump_runtime: int = 3600, pump_runtime_today: int = 600) -> list[int]:
    """CMD 230 payload of a W4/W5 (29 bytes)."""
    return [
        1, 2, 0, 0, 0, 0,
        *pump_runtime.to_bytes(4, "big"),
        97, 1,
        *pump_runtime_today.to_bytes(4, "big"),
        30, 60, 1, 80,
        0x01, 0xE0, 0x05, 0x28,
        0,
        0x05, 0x28, 0x01, 0xE0,
    ]


def registry_device_id(hass, config_entry) -> str:
    """Return the device registry id of the fountain of this entry."""
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, ADDRESS), config_entry.entry_id
    )
    assert device is not None
    return device.id


def make_writable(coordinator) -> AsyncMock:
    """Pretend the W5 is connected and has answered CMD 211; return the write queue mock."""
    client = MagicMock()
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock()
    coordinator.device.device_type = 14
    coordinator.device.name = "Petkit_W5"
    coordinator._model_identified = True
    coordinator.ble_manager.connected_devices[ADDRESS] = client
    coordinator.device.config_received = True
    coordinator.ble_manager.message_producer = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator.ble_manager.message_producer


@pytest.fixture
def entry(hass) -> MockConfigEntry:
    """A config entry for one fountain, not yet set up."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS.lower(),
        data={"address": ADDRESS},
        options={"update_interval": 10},
        title="Petkit Water Fountain",
    )
    config_entry.add_to_hass(hass)
    return config_entry


@pytest.fixture
async def loaded_entry(hass, entry, enable_bluetooth):
    """The entry set up through Home Assistant, with the BLE loops switched off."""
    with patch(
        "custom_components.petkit_ble.coordinator.PetkitBLECoordinator.async_start",
        AsyncMock(),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    yield entry
    if entry.entry_id in hass.data.get(DOMAIN, {}):
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
