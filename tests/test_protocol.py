"""Frame parsing and command building: what the fountain sends is untrusted."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.petkit_ble.PetkitW5BLEMQTT.commands import Commands
from custom_components.petkit_ble.PetkitW5BLEMQTT.device import Device
from custom_components.petkit_ble.PetkitW5BLEMQTT.event_handlers import EventHandlers
from custom_components.petkit_ble.PetkitW5BLEMQTT.parsers import Parsers
from custom_components.petkit_ble.PetkitW5BLEMQTT.utils import Utils

from .conftest import ADDRESS, frame, w5_config_payload, w5_status_payload

W5 = {"alias": "W5", "device_type": 14, "name": "Petkit_W5"}
CTW3 = {"alias": "W5", "device_type": 24, "name": "Petkit_CTW3"}


@pytest.fixture
def device() -> Device:
    dev = Device(ADDRESS)
    dev.device_type = 14
    dev.name = "Petkit_W5"
    dev.alias = "W5"
    return dev


@pytest.fixture
def handlers(device) -> EventHandlers:
    return EventHandlers(device=device, commands=None, logger=logging.getLogger("test"))


def test_build_command_layout():
    assert Utils.build_command(seq=7, cmd=220, type=1, data=[1, 2]) == bytearray(
        [0xFA, 0xFC, 0xFD, 220, 1, 7, 2, 0, 1, 2, 0xFB]
    )


def test_parse_roundtrip():
    parsed = Utils.parse_bytearray(Utils.build_command(3, 66, 2, [9, 8, 7]))
    assert parsed["cmd"] == 66
    assert parsed["seq"] == 3
    assert list(parsed["data"]) == [9, 8, 7]


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        None,
        b"\xfa\xfc\xfd",
        bytes([0xFA, 0xFC, 0xFD, 66, 2, 0, 0, 0]),  # no end byte
        bytes([0x00, 0xFC, 0xFD, 66, 2, 0, 0, 0, 0xFB]),  # wrong header
        bytes([0xFA, 0xFC, 0xFD, 66, 2, 0, 0, 0, 0x00]),  # wrong end byte
        bytes([0xFA, 0xFC, 0xFD, 66, 2, 0, 5, 0, 1, 0xFB]),  # length says 5, has 1
        bytes([0xFA, 0xFC, 0xFD, 66, 2, 0, 0, 0, 1, 2, 0xFB]),  # length says 0, has 2
    ],
)
def test_malformed_frames_are_rejected(raw):
    assert Utils.parse_bytearray(raw) is None


def test_w5_state_and_short_state():
    data = bytes([1, 2, 0, 0, 1, 0, 0, 0, 0x0E, 0x10, 97, 1])
    state = Parsers.device_state(data, W5)
    assert state["power_status"] == 1
    assert state["mode"] == 2
    assert state["warning_water_missing"] == 1
    assert state["pump_runtime"] == 3600
    assert state["filter_percentage"] == 0.97
    assert "error" in Parsers.device_state(data[:11], W5)


def test_ctw3_layout_is_selected_by_device_type():
    assert Parsers._is_ctw3(CTW3)
    assert not Parsers._is_ctw3(W5)
    assert "error" in Parsers.device_state(bytes(25), CTW3)
    assert Parsers.device_state(bytes(26), CTW3)["battery_percentage"] == 0
    assert "error" in Parsers.device_configuration(bytes(8), CTW3)
    assert "error" in Parsers.device_status(bytes(25), CTW3)


def test_w5_config_with_and_without_child_lock_byte():
    full = Parsers.device_configuration(bytes(w5_config_payload(is_locked=1)), W5)
    assert full["smart_time_on"] == 30
    assert full["smart_time_off"] == 60
    assert full["led_light_time_on"] == 480
    assert full["do_not_disturb_time_on"] == 1320
    assert full["is_locked"] == 1
    short = Parsers.device_configuration(bytes(w5_config_payload(is_locked=None)), W5)
    assert short["is_locked"] is None
    assert "error" in Parsers.device_configuration(bytes(12), W5)


def test_w5_status():
    status = Parsers.device_status(bytes(w5_status_payload()), W5)
    assert status["pump_runtime"] == 3600
    assert status["pump_runtime_today"] == 600
    assert status["led_brightness"] == 80
    assert status["filter_percentage"] == 0.97
    assert "error" in Parsers.device_status(bytes(28), W5)


async def test_handler_ignores_malformed_notification(handlers, device):
    assert await handlers.handle_notification("s", b"\x01\x02\x03") is None
    assert await handlers.handle_notification("s", b"") is None
    assert device.config_received is False


async def test_config_is_writable_only_after_a_complete_cmd_211(handlers, device):
    await handlers.handle_notification("s", frame(211, w5_config_payload(is_locked=None)))
    assert device.config_received is False

    await handlers.handle_notification("s", frame(211, w5_config_payload(is_locked=1)))
    assert device.config_received is True
    assert device.config["is_locked"] == 1
    assert device.config["smart_time_on"] == 30

    # A later short answer must block writes again.
    await handlers.handle_notification("s", frame(211, w5_config_payload(is_locked=None)))
    assert device.config_received is False


async def test_short_state_frame_does_not_change_the_device(handlers, device):
    await handlers.handle_notification("s", frame(210, [0] * 5))
    assert device.status["power_status"] == 0
    assert device.status["running_status"] is None


async def test_unknown_command_is_ignored(handlers, device):
    before = dict(device.status)
    assert await handlers.handle_notification("s", frame(99, [1, 2, 3])) is not None
    assert device.status == before


async def test_cmd_213_sets_identity(handlers, device):
    serial = b"20240101A12345\x00"
    await handlers.handle_notification("s", frame(213, [0, 0, 1, 2, 3, 4, 5, 6, *serial]))
    assert device.device_id_bytes == [1, 2, 3, 4, 5, 6]
    assert device.serial.startswith("20240101A12345")


async def test_init_command_carries_id_and_derived_secret(device):
    ble = MagicMock()
    ble.message_producer = AsyncMock()
    commands = Commands(ble_manager=ble, device=device, logger=logging.getLogger("test"))
    device.device_id_bytes = [1, 2, 3, 4, 5, 6]
    await commands.init_device()
    sent = Utils.parse_bytearray(ble.message_producer.await_args.args[0])
    assert sent["cmd"] == 73
    assert list(sent["data"]) == [0, 0, 0, 0, 1, 2, 3, 4, 5, 6, 0, 0, 6, 5, 4, 3, 2, 1]


async def test_sequence_wraps_at_one_byte(device):
    ble = MagicMock()
    ble.message_producer = AsyncMock()
    commands = Commands(ble_manager=ble, device=device, logger=logging.getLogger("test"))
    commands.sequence = 255
    await commands.get_battery()
    await commands.get_battery()
    first, second = (call.args[0] for call in ble.message_producer.await_args_list)
    assert first[5] == 255
    assert second[5] == 0
