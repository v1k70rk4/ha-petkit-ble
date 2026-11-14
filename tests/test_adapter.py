"""Connection handling rules of the Home Assistant Bluetooth adapter."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.petkit_ble.ha_bluetooth_adapter import HABluetoothAdapter
from custom_components.petkit_ble.PetkitW5BLEMQTT.device import Device
from custom_components.petkit_ble.PetkitW5BLEMQTT.event_handlers import EventHandlers

from .conftest import ADDRESS, frame

ADAPTER = "custom_components.petkit_ble.ha_bluetooth_adapter"


@pytest.fixture
def device() -> Device:
    return Device(ADDRESS)


@pytest.fixture
def adapter(hass, device) -> HABluetoothAdapter:
    handlers = EventHandlers(
        device=device, commands=None, logger=logging.getLogger("custom_components.petkit_ble")
    )
    return HABluetoothAdapter(
        hass=hass,
        address=ADDRESS,
        event_handler=handlers,
        logger=logging.getLogger("custom_components.petkit_ble"),
    )


async def _connect(adapter: HABluetoothAdapter, client: MagicMock):
    """Run connect_device against a fake client; return the disconnect callback."""
    with (
        patch(f"{ADAPTER}.bluetooth.async_ble_device_from_address", return_value=MagicMock()),
        patch(f"{ADAPTER}.establish_connection", AsyncMock(return_value=client)) as establish,
    ):
        assert await adapter.connect_device(ADDRESS) is True
    return establish.await_args.kwargs["disconnected_callback"], establish.await_args.args


async def test_connect_drops_queued_commands_and_blocks_config_writes(adapter, device):
    device.config_received = True
    await adapter.message_producer(b"stale-1")
    await adapter.message_producer(b"stale-2")

    client = MagicMock()
    _, args = await _connect(adapter, client)

    assert adapter.queue.empty()
    assert device.config_received is False
    assert adapter.connected_devices[ADDRESS] is client
    assert adapter.connection_status == "connected"
    # bleak-retry-connector#302 workaround: the lowercase address is passed on.
    assert args[2] == ADDRESS.lower()


async def test_late_disconnect_of_a_previous_client_keeps_the_current_one(adapter):
    old_client, new_client = MagicMock(), MagicMock()
    old_callback, _ = await _connect(adapter, old_client)
    new_callback, _ = await _connect(adapter, new_client)

    old_callback(old_client)
    assert adapter.connected_devices[ADDRESS] is new_client

    new_callback(new_client)
    assert ADDRESS not in adapter.connected_devices
    assert adapter.connection_status == "disconnected"


async def test_connect_fails_cleanly_when_the_device_is_not_advertising(adapter):
    with patch(f"{ADAPTER}.bluetooth.async_ble_device_from_address", return_value=None):
        assert await adapter.connect_device(ADDRESS) is False
    assert ADDRESS not in adapter.connected_devices
    assert adapter.connection_attempts == 1


async def test_producer_never_blocks_on_a_full_queue(adapter):
    for i in range(25):
        await adapter.message_producer(bytes([i]))
    assert adapter.queue.qsize() == 10


async def test_failed_write_disconnects_the_dead_client(adapter):
    client = MagicMock()
    client.is_connected = True
    client.write_gatt_char = AsyncMock(side_effect=OSError("gone"))
    client.disconnect = AsyncMock()
    adapter.connected_devices[ADDRESS] = client

    with patch.object(adapter, "ensure_connected", AsyncMock(return_value=False)):
        assert await adapter.write_characteristic(ADDRESS, "uuid", b"\x00") is False

    client.disconnect.assert_awaited_once()
    assert ADDRESS not in adapter.connected_devices


async def test_only_well_formed_frames_count_as_a_sign_of_life(adapter):
    await adapter._on_notification("s", b"\x00\x01garbage")
    assert adapter.last_notification is None
    await adapter._on_notification("s", frame(66, [0x0F, 0xA0, 87]))
    assert adapter.last_notification is not None


async def test_cmd_213_payload_never_reaches_the_log(adapter, device, caplog):
    device_id = [0xDE, 0xAD, 0xBE, 0xEF, 0x12, 0x34]
    raw = frame(213, [0, 0, *device_id, *b"20240101A12345\x00"])
    with caplog.at_level(logging.DEBUG):
        await adapter._on_notification("s", raw)

    assert device.device_id_bytes == device_id
    assert bytes(raw).hex() not in caplog.text
    assert bytes(device_id).hex() not in caplog.text
    assert str(device_id) not in caplog.text
    assert str(device.device_id) not in caplog.text


async def test_reconnect_loop_connects_again_after_a_ble_reset(adapter):
    """At a multiple of RESET_EVERY the loop resets once, then tries to connect.

    It used to `continue` after the reset with the counter unchanged, so it
    reset every ten seconds and never tried to connect again.
    """
    adapter._connection_attempts = 50
    connects = 0

    async def connect(address):
        nonlocal connects
        connects += 1
        if connects == 1:
            adapter._connection_attempts += 1
            return False
        adapter.connected_devices[address] = MagicMock()
        return True

    async def reset_once(address):
        # The old loop reset forever; fail instead of hanging the test.
        if reset.await_count > 2:
            raise AssertionError("reset again without a connection attempt")

    with (
        patch.object(adapter, "_reset_ble_stack", AsyncMock(side_effect=reset_once)) as reset,
        patch.object(adapter, "connect_device", side_effect=connect),
        patch.object(adapter, "start_notifications", AsyncMock(return_value=True)),
        patch(f"{ADAPTER}.asyncio.sleep", AsyncMock()),
    ):
        assert await adapter._reconnection_loop(ADDRESS) is True

    assert reset.await_count == 1
    assert connects == 2


async def test_reconnect_loop_backs_off_after_failed_notifications(adapter):
    """A link without working notifications is retried after a pause, not at once."""
    subscribed = iter([False, True])
    sleeps: list[float] = []

    async def connect(address):
        adapter.connected_devices[address] = MagicMock()
        return True

    async def sleep(delay):
        sleeps.append(delay)

    with (
        patch.object(adapter, "connect_device", side_effect=connect),
        patch.object(adapter, "start_notifications", AsyncMock(side_effect=lambda *a: next(subscribed))),
        patch.object(adapter, "_disconnect_client", AsyncMock(side_effect=lambda a: adapter.connected_devices.pop(a, None))),
        patch(f"{ADAPTER}.asyncio.sleep", side_effect=sleep),
    ):
        assert await adapter._reconnection_loop(ADDRESS) is True

    assert sleeps and min(sleeps) >= 10.0
