"""Regression tests for the findings of the 2026-11-13 security review."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError

from custom_components.petkit_ble.const import (
    DATA_UNDISCONNECTED,
    DOMAIN,
    SUPPORTED_NAME_PREFIXES,
    is_supported_device_name,
)
from custom_components.petkit_ble.ha_bluetooth_adapter import HABluetoothAdapter
from custom_components.petkit_ble.PetkitW5BLEMQTT.commands import Commands
from custom_components.petkit_ble.PetkitW5BLEMQTT.device import Device
from custom_components.petkit_ble.PetkitW5BLEMQTT.event_handlers import EventHandlers
from custom_components.petkit_ble.PetkitW5BLEMQTT.parsers import Parsers
from custom_components.petkit_ble.PetkitW5BLEMQTT.utils import Utils
from custom_components.petkit_ble.sensor import PetkitPumpRuntimeSensor

from .conftest import ADDRESS, frame, make_writable, registry_device_id, w5_status_payload

INTEGRATION = Path(__file__).parent.parent / "custom_components" / "petkit_ble"
ADAPTER = "custom_components.petkit_ble.ha_bluetooth_adapter"
LOGGER_NAME = "custom_components.petkit_ble"
DAY = 86400
W5 = {"alias": "W5", "device_type": 14, "name": "Petkit_W5"}


def _reconnect_tasks() -> list[asyncio.Task]:
    return [
        task
        for task in asyncio.all_tasks()
        if not task.done() and "ensure_connected" in repr(task.get_coro())
    ]


async def _cancel(tasks) -> None:
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


# -- Reconnects must not outlive the config entry -----------------------------


async def test_no_reconnect_task_survives_unload(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    with patch.object(adapter, "connect_device", AsyncMock(return_value=False)):
        # A write while the link is down starts a background reconnect.
        assert await adapter.write_characteristic(ADDRESS, "uuid", b"\x00") is False
        await asyncio.sleep(0.05)
        assert len(_reconnect_tasks()) == 1

        assert await hass.config_entries.async_unload(loaded_entry.entry_id)
        await asyncio.sleep(0.3)
        leftovers = _reconnect_tasks()
        try:
            assert leftovers == []
        finally:
            await _cancel(leftovers)


async def test_closed_adapter_does_not_reconnect(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    adapter.connect_device = AsyncMock(return_value=False)
    await adapter.async_close()

    assert await adapter.write_characteristic(ADDRESS, "uuid", b"\x00") is False
    assert await adapter.ensure_connected(ADDRESS) is False
    await asyncio.sleep(0.05)

    assert _reconnect_tasks() == []
    adapter.connect_device.assert_not_awaited()


async def test_failed_initialization_cancels_background_reconnects(hass, loaded_entry):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    adapter = coordinator.ble_manager
    with patch.object(adapter, "connect_device", AsyncMock(return_value=False)):
        await adapter.write_characteristic(ADDRESS, "uuid", b"\x00")
        await asyncio.sleep(0.05)
        assert len(_reconnect_tasks()) == 1
        await coordinator._cleanup()
        assert _reconnect_tasks() == []
        # Not closed: the next write may ask for a reconnect again.
        await adapter.write_characteristic(ADDRESS, "uuid", b"\x00")
        await asyncio.sleep(0.05)
        assert len(_reconnect_tasks()) == 1


async def test_reconnect_requests_share_one_pending_task(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    with patch.object(adapter, "connect_device", AsyncMock(return_value=False)):
        for _ in range(5):
            assert await adapter.write_characteristic(ADDRESS, "uuid", b"\x00") is False
        await asyncio.sleep(0.05)
        assert len(_reconnect_tasks()) == 1
        assert len(adapter._background_tasks) == 1
        await adapter.async_cancel_reconnects()
        assert _reconnect_tasks() == []


def _client_with_slow_first_disconnect() -> tuple[MagicMock, asyncio.Event]:
    """A client whose first disconnect() hangs until the event is set."""
    started = asyncio.Event()
    calls = 0

    async def disconnect():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await asyncio.Event().wait()

    client = MagicMock()
    client.is_connected = True
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock(side_effect=disconnect)
    return client, started


async def test_client_stays_tracked_when_its_disconnect_is_cancelled(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    client, started = _client_with_slow_first_disconnect()
    adapter.connected_devices[ADDRESS] = client

    task = asyncio.create_task(adapter.force_reconnect(ADDRESS))
    await started.wait()
    await _cancel([task])

    # Still tracked, so it can be disconnected for real.
    assert adapter.connected_devices[ADDRESS] is client
    assert await adapter.disconnect_device(ADDRESS) is True
    assert ADDRESS not in adapter.connected_devices
    assert client.disconnect.await_count == 2


async def test_unload_during_a_reconnect_teardown_still_disconnects(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    client, started = _client_with_slow_first_disconnect()

    async def connect(address):
        adapter.connected_devices[address] = client
        return True

    # Connected, but the notifications cannot be subscribed: the reconnect
    # loop tears the link down again, and the unload arrives in the middle.
    with (
        patch.object(adapter, "connect_device", AsyncMock(side_effect=connect)),
        patch.object(adapter, "start_notifications", AsyncMock(return_value=False)),
    ):
        adapter._request_reconnect(ADDRESS)
        await started.wait()
        assert await hass.config_entries.async_unload(loaded_entry.entry_id)

    assert ADDRESS not in adapter.connected_devices
    assert client.disconnect.await_count == 2
    assert _reconnect_tasks() == []


async def test_disconnect_does_not_drop_a_newer_client(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    old_client, new_client = MagicMock(), MagicMock()

    async def disconnect():
        adapter.connected_devices[ADDRESS] = new_client

    old_client.disconnect = AsyncMock(side_effect=disconnect)
    new_client.stop_notify = AsyncMock()
    new_client.disconnect = AsyncMock()
    adapter.connected_devices[ADDRESS] = old_client

    await adapter._disconnect_client(ADDRESS)
    assert adapter.connected_devices[ADDRESS] is new_client


def _client(disconnect_side_effect=None) -> MagicMock:
    client = MagicMock()
    client.is_connected = True
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock(side_effect=disconnect_side_effect)
    return client


async def test_failed_disconnect_is_retried_before_the_next_connect(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    client = _client([OSError("busy"), None])
    adapter.connected_devices[ADDRESS] = client

    assert await adapter._disconnect_client(ADDRESS) is False
    # Not taken for a working link any more, and not forgotten either.
    assert ADDRESS not in adapter.connected_devices
    assert adapter._undisconnected == [client]
    assert await adapter.write_characteristic(ADDRESS, "uuid", b"\x00") is False
    await adapter.async_cancel_reconnects()
    client.write_gatt_char.assert_not_called()

    new_client = _client()
    with (
        patch(f"{ADAPTER}.bluetooth.async_ble_device_from_address", return_value=MagicMock()),
        patch(f"{ADAPTER}.establish_connection", AsyncMock(return_value=new_client)),
    ):
        assert await adapter.connect_device(ADDRESS) is True

    assert client.disconnect.await_count == 2
    assert adapter._undisconnected == []
    assert adapter.connected_devices[ADDRESS] is new_client


async def test_unload_retries_a_failed_disconnect(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    client = _client([OSError("busy"), None])
    adapter.connected_devices[ADDRESS] = client

    assert await hass.config_entries.async_unload(loaded_entry.entry_id)

    assert client.disconnect.await_count == 2
    assert adapter._undisconnected == []
    assert ADDRESS not in adapter.connected_devices


async def test_unload_retries_a_disconnect_that_failed_earlier(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    client = _client([OSError("busy"), None])
    adapter.connected_devices[ADDRESS] = client
    await adapter._disconnect_client(ADDRESS)
    assert adapter._undisconnected == [client]

    # Nothing is tracked as connected, so only the adapter's close can do it.
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)

    assert client.disconnect.await_count == 2
    assert adapter._undisconnected == []


async def test_disconnect_that_keeps_failing_is_reported_and_kept(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    client = _client(OSError("busy"))
    adapter.connected_devices[ADDRESS] = client

    assert await adapter.disconnect_device(ADDRESS) is False
    assert ADDRESS not in adapter.connected_devices
    assert adapter._undisconnected == [client]
    assert adapter.connection_status == "disconnected"

    # Once the link is gone by itself there is nothing left to disconnect.
    client.is_connected = False
    assert await adapter._retry_failed_disconnects() is True
    assert adapter._undisconnected == []
    assert client.disconnect.await_count == 2


async def test_disconnect_that_keeps_failing_during_unload_is_retried_then_reported(
    hass, loaded_entry, caplog
):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    client = _client(OSError("busy"))
    adapter.connected_devices[ADDRESS] = client

    with patch(f"{ADAPTER}.LEFTOVER_DISCONNECT_DELAYS", (0.01, 0.01, 0.01)):
        assert await hass.config_entries.async_unload(loaded_entry.entry_id)
        # The unload itself tried twice; the entry is gone, the retries go on.
        assert client.disconnect.await_count == 2
        assert adapter._leftover_task is not None
        await adapter._leftover_task

    assert client.disconnect.await_count == 5
    assert adapter._undisconnected == [client]
    assert ADDRESS not in adapter.connected_devices
    assert "Could not disconnect" in caplog.text


async def test_disconnect_that_fails_during_unload_is_retried_until_it_works(
    hass, loaded_entry, caplog
):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    client = _client([OSError("busy"), OSError("busy"), OSError("busy"), None])
    adapter.connected_devices[ADDRESS] = client

    with patch(f"{ADAPTER}.LEFTOVER_DISCONNECT_DELAYS", (0.01, 0.01, 0.01)):
        assert await hass.config_entries.async_unload(loaded_entry.entry_id)
        await adapter._leftover_task

    assert client.disconnect.await_count == 4
    assert adapter._undisconnected == []
    assert "Could not disconnect" not in caplog.text


async def test_client_left_connected_after_unload_is_disconnected_on_the_next_setup(
    hass, loaded_entry
):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    client = _client(OSError("busy"))
    adapter.connected_devices[ADDRESS] = client

    with patch(f"{ADAPTER}.LEFTOVER_DISCONNECT_DELAYS", (0.01,)):
        assert await hass.config_entries.async_unload(loaded_entry.entry_id)
        await adapter._leftover_task
    # Twice during the unload, once more after it: every retry is used up.
    assert client.disconnect.await_count == 3
    del adapter

    # Home Assistant still owns the client, with no adapter left to hold it.
    assert hass.data[DATA_UNDISCONNECTED][ADDRESS] == [client]

    client.disconnect = AsyncMock()
    with patch(
        "custom_components.petkit_ble.coordinator.PetkitBLECoordinator.async_start", AsyncMock()
    ):
        assert await hass.config_entries.async_setup(loaded_entry.entry_id)
        await hass.async_block_till_done()
    new_adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    assert new_adapter._undisconnected == [client]

    new_client = _client()
    with (
        patch(f"{ADAPTER}.bluetooth.async_ble_device_from_address", return_value=MagicMock()),
        patch(f"{ADAPTER}.establish_connection", AsyncMock(return_value=new_client)),
    ):
        assert await new_adapter.connect_device(ADDRESS) is True

    # The old client is disconnected before the new connection is made.
    client.disconnect.assert_awaited_once()
    assert hass.data[DATA_UNDISCONNECTED][ADDRESS] == []
    assert new_adapter.connected_devices[ADDRESS] is new_client


async def test_adapters_of_other_fountains_do_not_share_failed_clients(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    other = HABluetoothAdapter(hass=hass, address="A4:C1:38:99:88:77", event_handler=MagicMock())
    client = _client([OSError("busy"), None])
    adapter.connected_devices[ADDRESS] = client
    await adapter._disconnect_client(ADDRESS)

    assert adapter._undisconnected == [client]
    assert other._undisconnected == []
    assert await other._retry_failed_disconnects() is True
    assert client.disconnect.await_count == 1


async def test_no_disconnect_retries_are_scheduled_while_the_entry_is_loaded(hass, loaded_entry):
    adapter = hass.data[DOMAIN][loaded_entry.entry_id].ble_manager
    client = _client(OSError("busy"))
    adapter.connected_devices[ADDRESS] = client

    assert await adapter.disconnect_device(ADDRESS) is False
    # The next connect retries it; no extra task is needed.
    assert adapter._leftover_task is None
    client.disconnect = AsyncMock()


# -- An absent fountain must not fill the log ---------------------------------


async def test_unreachable_fountain_does_not_flood_the_log(hass, loaded_entry, caplog):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    coordinator.ble_manager.scan = AsyncMock(return_value={})
    coordinator.ble_manager.connect_device = AsyncMock(return_value=False)
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        task = asyncio.create_task(coordinator._initialization_loop())
        await asyncio.sleep(1.3)  # three attempts at the initial 0.5 s pace
        await _cancel([task])
    loud = [r for r in caplog.records if r.name.startswith(LOGGER_NAME) and r.levelno >= logging.INFO]
    assert coordinator.ble_manager.connect_device.await_count >= 3
    assert [r.levelno for r in loud] == [logging.WARNING]
    assert "attempt 1 failed" in loud[0].getMessage()


async def test_polling_commands_are_logged_at_debug_only(caplog):
    ble = MagicMock()
    ble.message_producer = AsyncMock()
    commands = Commands(ble_manager=ble, device=Device(ADDRESS), logger=logging.getLogger(LOGGER_NAME))
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        await commands.get_battery()
        await commands.get_device_state()
        await commands.get_device_update()
        await commands.get_device_config()
    assert ble.message_producer.await_count == 4
    assert [r for r in caplog.records if r.levelno >= logging.INFO] == []


async def test_short_frames_are_logged_at_debug_only(caplog):
    handlers = EventHandlers(device=Device(ADDRESS), commands=None, logger=logging.getLogger(LOGGER_NAME))
    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        for _ in range(20):
            await handlers.handle_notification("s", frame(210, [0] * 5))
    assert [r for r in caplog.records if r.levelno >= logging.INFO] == []


# -- Only a fountain is offered for setup -------------------------------------


def test_manifest_only_matches_petkit_advertisements():
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    patterns = [matcher.get("local_name") for matcher in manifest["bluetooth"]]
    assert all(patterns), "a matcher without local_name matches every device"
    assert all(matcher["connectable"] is True for matcher in manifest["bluetooth"])
    assert {pattern.rstrip("*") for pattern in patterns} == set(SUPPORTED_NAME_PREFIXES)


@pytest.mark.parametrize("model_byte", [205, 206, 213, 214, 217, 223, 228, 246, 247, 248, 249])
def test_every_known_model_is_discovered(model_byte):
    name = Utils.get_device_properties(model_byte)["name"]
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    assert is_supported_device_name(name)
    assert any(fnmatchcase(name, matcher["local_name"]) for matcher in manifest["bluetooth"])


@pytest.mark.parametrize(
    "name",
    [
        "LG-W5 Soundbar",
        "ZW4-Tracker",
        "MyCTW2Clone",
        "XPetkit_W5",
        "petkit_w5",
        "Petkit_D4",
        "Petkit_",
        "Mi Smart Band",
        "",
        None,
    ],
)
async def test_discovery_rejects_other_devices(hass, enable_bluetooth, name):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "bluetooth"},
        data=SimpleNamespace(name=name, address="11:22:33:44:55:66"),
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


@pytest.mark.parametrize("name", ["Petkit_W4X", "Petkit_W5C", "Petkit_CTW2", "Petkit_CTW3UV_100"])
async def test_discovery_offers_a_fountain_for_confirmation(hass, enable_bluetooth, name):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "bluetooth"},
        data=SimpleNamespace(name=name, address=ADDRESS),
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": name, "address": ADDRESS}


# -- Input from the user ------------------------------------------------------


@pytest.mark.parametrize("address", ["not a mac", "A4:C1:38:00:11", "../../etc/passwd", "", "A4-C1-38-00-11-22"])
async def test_manual_step_rejects_an_invalid_address(hass, enable_bluetooth, address):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"address": address})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"address": "invalid_address"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_manual_step_accepts_and_normalizes_a_valid_address(hass, enable_bluetooth):
    with patch("custom_components.petkit_ble.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address": " a4:c1:38:00:11:22 "}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"address": ADDRESS}
    assert result["result"].unique_id == ADDRESS.lower()


@pytest.mark.parametrize("field", ["smart_time_on", "smart_time_off"])
@pytest.mark.parametrize("value", [0, 256, 300, 1440, -5])
async def test_service_rejects_a_smart_time_that_does_not_fit_a_byte(hass, loaded_entry, field, value):
    producer = make_writable(hass.data[DOMAIN][loaded_entry.entry_id])
    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await hass.services.async_call(
            DOMAIN,
            "set_device_config",
            {"device_id": registry_device_id(hass, loaded_entry), field: value},
            blocking=True,
        )
    producer.assert_not_awaited()


# -- Input from the fountain, or from something pretending to be one ----------


@pytest.mark.parametrize(
    ("cmd", "payload"),
    [(66, [1]), (66, []), (200, [1]), (200, []), (86, []), (213, [0, 0, 1, 2]), (213, [])],
)
async def test_short_payloads_are_ignored(cmd, payload):
    device = Device(ADDRESS)
    handlers = EventHandlers(device=device, commands=None, logger=logging.getLogger("test"))
    assert await handlers.handle_notification("s", frame(cmd, payload)) is not None
    assert device.firmware == 0
    assert device.serial == "Uninitialized"
    assert device.device_id_bytes == []
    assert device.status["battery"] is None


def test_short_service_data_falls_back_to_an_unknown_model():
    device = Device(ADDRESS)
    ble = MagicMock()
    ble.connectiondata = {
        ADDRESS: SimpleNamespace(details={"props": {"RSSI": -60, "ServiceData": {"x": b"\x01\x02"}}})
    }
    Commands(ble_manager=ble, device=device, logger=logging.getLogger("test")).init_device_data()
    assert device.product_name == "Petkit BLE Water Fountain"
    assert device.device_type == 0


def _init_from_advertisement(device: Device, name: str | None, service_data: bytes) -> None:
    ble = MagicMock()
    ble.connectiondata = {
        ADDRESS: SimpleNamespace(
            name=name, details={"props": {"RSSI": -60, "ServiceData": {"x": service_data}}}
        )
    }
    Commands(ble_manager=ble, device=device, logger=logging.getLogger("test")).init_device_data()


def test_short_service_data_keeps_an_identified_model():
    device = Device(ADDRESS)
    _init_from_advertisement(device, "Petkit_CTW3", bytes([0, 0, 0, 0, 0, 223]))
    assert device.device_type == 24

    _init_from_advertisement(device, None, b"\x01\x02")
    assert device.name == "Petkit_CTW3"
    assert device.device_type == 24
    assert Parsers._is_ctw3({"device_type": device.device_type, "name": device.name})


@pytest.mark.parametrize(
    ("name", "device_type", "product_name"),
    [("Petkit_CTW3_100", 24, "Eversweet Max 2"), ("Petkit_W4X", 14, "Eversweet 3 Pro")],
)
def test_short_service_data_falls_back_to_the_advertised_name(name, device_type, product_name):
    device = Device(ADDRESS)
    _init_from_advertisement(device, name, b"\x01\x02")
    assert device.name == name
    assert device.device_type == device_type
    assert device.product_name == product_name


def test_unknown_ctw3_variant_is_still_parsed_as_a_ctw3():
    device = Device(ADDRESS)
    _init_from_advertisement(device, "Petkit_CTW3_NEW", b"\x01\x02")
    assert device.device_type == 0
    assert Parsers._is_ctw3({"device_type": device.device_type, "name": device.name})
    # CTW3 layout: a 12 byte W4/W5 state frame is too short for it.
    assert "error" in Parsers.device_state(bytes(12), {"device_type": 0, "name": device.name})


async def _scan_and_identify(coordinator, name: str, service_data: dict) -> Device:
    """Run the adapter's scan on one advertisement, then the model detection."""
    advertisement = SimpleNamespace(name=name, address=ADDRESS, rssi=-60, service_data=service_data)
    with patch(f"{ADAPTER}.bluetooth.async_discovered_service_info", return_value=[advertisement]):
        assert ADDRESS in await coordinator.ble_manager.scan()
    coordinator.commands.init_device_data()
    return coordinator.device


@pytest.mark.parametrize(
    ("name", "device_type"),
    [("Petkit_CTW3", 24), ("Petkit_CTW3UV_100", 24), ("Petkit_W5", 14), ("Petkit_W4XUVC", 14)],
)
async def test_scan_without_service_data_identifies_the_model_by_name(hass, loaded_entry, name, device_type):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    device = await _scan_and_identify(coordinator, name, {})
    assert device.name == name
    assert device.device_type == device_type
    assert coordinator.is_ctw3 is (device_type == 24)


async def test_scan_without_service_data_keeps_a_known_ctw3_on_reconnect(hass, loaded_entry):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    full = {"0000c1a4-0000-1000-8000-00805f9b34fb": bytes([0x38, 0x00, 0x11, 0x22, 0x00, 247])}
    device = await _scan_and_identify(coordinator, "Petkit_CTW3_100", full)
    assert (device.name, device.device_type) == ("Petkit_CTW3_100", 24)

    # The next advertisement carries no service data and a name that is in no table.
    device = await _scan_and_identify(coordinator, "CTW3", {})
    assert (device.name, device.device_type) == ("Petkit_CTW3_100", 24)
    assert coordinator.is_ctw3


async def test_scan_without_service_data_never_invents_a_w5(hass, loaded_entry):
    coordinator = hass.data[DOMAIN][loaded_entry.entry_id]
    device = await _scan_and_identify(coordinator, "Petkit_CTW3_NEW", {})
    assert device.name == "Petkit_CTW3_NEW"
    assert device.alias == "Unknown"
    assert coordinator.is_ctw3


@pytest.mark.parametrize(
    ("model_byte", "name", "device_type"),
    [(214, "Petkit_W4X", 14), (223, "Petkit_CTW3", 24)],
)
def test_service_data_selects_the_model(model_byte, name, device_type):
    # Layout of a real advertisement: the first two bytes of the Bluetooth
    # address serve as the 16 bit "UUID", the other four follow, then 0 and the model.
    service_data = {
        "0000c1a4-0000-1000-8000-00805f9b34fb": bytes([0x38, 0x00, 0x11, 0x22, 0x00, model_byte])
    }
    device = Device(ADDRESS)
    ble = MagicMock()
    ble.connectiondata = {
        ADDRESS: SimpleNamespace(details={"props": {"RSSI": -60, "ServiceData": service_data}})
    }
    Commands(ble_manager=ble, device=device, logger=logging.getLogger("test")).init_device_data()
    assert device.name == name
    assert device.device_type == device_type


# -- Values shown to the user -------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0 days, 0 hours"), (3 * DAY, "3 days, 0 hours"), (40 * DAY + 7200, "40 days, 2 hours"), (400 * DAY + 3599, "400 days, 0 hours")],
)
def test_runtime_is_formatted_as_elapsed_days(seconds, expected):
    assert Utils.get_timestamp_days(seconds) == expected


def _runtime_sensor(device: Device) -> PetkitPumpRuntimeSensor:
    coordinator = MagicMock()
    coordinator.address = ADDRESS
    coordinator.device = device
    coordinator.current_data = {"status": device.status}
    return PetkitPumpRuntimeSensor(coordinator)


@pytest.mark.parametrize(("seconds", "hours"), [(3 * DAY, 72.0), (40 * DAY, 960.0), (5400, 1.5), (0, 0.0)])
def test_pump_runtime_sensor_reports_total_hours(seconds, hours):
    device = Device(ADDRESS)
    device.status = Parsers.device_status(bytes(w5_status_payload(pump_runtime=seconds)), W5)
    assert _runtime_sensor(device).native_value == hours


def test_pump_runtime_sensor_is_unknown_until_the_device_reports():
    assert _runtime_sensor(Device(ADDRESS)).native_value is None


@pytest.mark.parametrize(("raw", "version"), [([1, 10], "1.10"), ([1, 1], "1.1"), ([2, 0], "2.0")])
def test_firmware_version_keeps_its_digits(raw, version):
    assert Parsers.device_firmware(bytes(raw), None)["firmware"] == version


def test_device_clock_counts_from_2000_utc():
    expected = (datetime.now(timezone.utc) - datetime(2000, 1, 1, tzinfo=timezone.utc)).total_seconds()
    assert abs(Utils.get_seconds() - expected) < 5
    payload = Utils.time_in_bytes()
    assert len(payload) == 6
    assert int.from_bytes(bytes(payload[1:5]), "big") == pytest.approx(expected, abs=5)
