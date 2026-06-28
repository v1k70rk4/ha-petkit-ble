"""Data update coordinator for Petkit BLE integration."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_processor import ActiveBluetoothProcessorCoordinator
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import CONF_ADDRESS, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
from .ha_bluetooth_adapter import HABluetoothAdapter

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from PetkitW5BLEMQTT.device import Device
from PetkitW5BLEMQTT.event_handlers import EventHandlers
from PetkitW5BLEMQTT.commands import Commands
from PetkitW5BLEMQTT.constants import Constants

_LOGGER = logging.getLogger(__name__)


class PetkitBLEData:
    """Data class for Petkit BLE device."""

    def __init__(self, device: Device) -> None:
        self.device = device

    def update(self, service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
        """Update RSSI from bluetooth advertisement."""
        if hasattr(self.device, '_rssi'):
            self.device.status = {"rssi": service_info.rssi}


class PetkitBLECoordinator(ActiveBluetoothProcessorCoordinator[PetkitBLEData]):
    """Petkit BLE data update coordinator."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.address = entry.data[CONF_ADDRESS]
        self.update_interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

        # Core BLE components
        self.device = Device(self.address)
        self.commands = Commands(ble_manager=None, device=self.device, logger=_LOGGER)
        self.event_handlers = EventHandlers(
            device=self.device, commands=self.commands, logger=_LOGGER
        )
        self.ble_manager = HABluetoothAdapter(
            hass=hass, address=self.address,
            event_handler=self.event_handlers, logger=_LOGGER,
        )

        # Wire up circular refs
        self.commands.ble_manager = self.ble_manager
        self.commands.mac = self.address
        self.device.set_ble_manager(self.ble_manager)

        self.data = PetkitBLEData(self.device)

        # ── Poll callbacks (required by ActiveBluetoothProcessorCoordinator) ──
        # We do our own polling in _start_regular_polling, so _async_poll
        # just returns existing data without issuing BLE commands.
        async def _async_poll(service_info: bluetooth.BluetoothServiceInfoBleak) -> PetkitBLEData:
            self.data.update(service_info)
            return self.data

        def _needs_poll(service_info: bluetooth.BluetoothServiceInfoBleak, last_poll: float | None) -> bool:
            return False  # We poll on our own schedule

        super().__init__(
            hass, _LOGGER,
            address=self.address,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            update_method=self.data.update,
            needs_poll_method=_needs_poll,
            poll_method=_async_poll,
            connectable=True,
        )

        self._consumer_task: asyncio.Task | None = None
        self._polling_task: asyncio.Task | None = None
        self._initialized = False
        self._listeners: set = set()
        self._initialization_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._poll_count: int = 0
        # Force a reconnect if no real data arrives for this long. Time-based so
        # detection doesn't depend on the (possibly large) poll interval.
        self._data_timeout: float = max(30.0, self.update_interval * 3)

        entry.async_on_unload(entry.add_update_listener(self._on_options_updated))

    # ── Lifecycle ──────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Start the coordinator and kick off initialization."""
        super().async_start()
        if not self._initialized:
            self._initialization_task = asyncio.create_task(self._initialization_loop())

    async def async_shutdown(self) -> None:
        """Shutdown and release resources."""
        await self._cleanup()

    # ── Initialization ─────────────────────────────────────────────

    async def _initialization_loop(self) -> None:
        """Retry initialization until successful."""
        attempt = 0
        while not self._initialized:
            try:
                attempt += 1
                _LOGGER.info(f"Initialization attempt {attempt}")
                await self._initialize_device()
                if self._initialized:
                    _LOGGER.info("✅ Device initialization successful")
                    return
            except Exception as err:
                _LOGGER.warning(f"Initialization attempt {attempt} failed: {err}")

            # Progressive delay: 0.5s → 1s → 2s → 5s
            delay = 0.5 if attempt < 5 else (1.0 if attempt < 10 else (2.0 if attempt < 20 else 5.0))
            await asyncio.sleep(delay)

    async def _initialize_device(self) -> None:
        """Initialize the BLE connection and device."""
        try:
            _LOGGER.info(f"Connecting to {self.address}")

            # Discover device
            await self.ble_manager.scan()

            # Connect
            if not await self.ble_manager.connect_device(self.address):
                raise UpdateFailed(f"Could not connect to {self.address}")

            # Start message consumer
            self._consumer_task = asyncio.create_task(
                self.ble_manager.message_consumer(self.address, Constants.WRITE_UUID)
            )

            # Start notifications
            await self.ble_manager.start_notifications(self.address, Constants.READ_UUID)
            await asyncio.sleep(0.2)

            # Wait for client readiness
            client = self.ble_manager.connected_devices.get(self.address)
            if client and hasattr(client, 'is_connected'):
                for _ in range(5):
                    if client.is_connected:
                        break
                    await asyncio.sleep(0.2)
                else:
                    raise UpdateFailed("Client not ready after 5 checks")

            # Init device data from scan results
            if self.address in self.ble_manager.connectiondata:
                self.commands.init_device_data()
            else:
                _LOGGER.warning(f"No scan data for {self.address}, using defaults")
                self.device.name = "Petkit Water Fountain"
                self.device.name_readable = "Petkit Water Fountain"
                self.device.product_name = "Petkit BLE Water Fountain"
                self.device.device_type = 14
                self.device.type_code = 14

            # Minimal BLE handshake
            await self.commands.get_device_details()
            await asyncio.sleep(1.0)

            if not self.device.device_initialized:
                await self.commands.init_device()
                await asyncio.sleep(1.5)

            await self.commands.get_device_info()
            await asyncio.sleep(0.75)

            # Fill in defaults for any uninitialized fields
            if self.device.serial == "Uninitialized":
                self.device.serial = f"PETKIT_{self.address.replace(':', '')[-6:]}"
            if self.device.name == "Uninitialized":
                self.device.name = "Water Fountain"
                self.device.name_readable = "Water Fountain"
            if not self.device.product_name or self.device.product_name == "Uninitialized":
                self.device.product_name = "Petkit BLE Water Fountain"
            # Firmware intentionally left as reported — no fake default, so the
            # firmware sensor shows "unknown" until the device reports a version

            self._initialized = True
            _LOGGER.info(f"Device ready: {self.device.name_readable} ({self.device.serial})")

            self.async_update_listeners()

            # Start the single polling loop
            self._polling_task = asyncio.create_task(self._polling_loop())

        except Exception as err:
            import traceback
            _LOGGER.error(f"Initialization failed: {err}")
            _LOGGER.debug("Traceback:\n%s", traceback.format_exc())
            await self._cleanup()

    # ── Polling ────────────────────────────────────────────────────

    async def _polling_loop(self) -> None:
        """Single polling loop that periodically refreshes device data."""
        _LOGGER.info(f"Polling started (interval: {self.update_interval}s)")
        while self._initialized:
            try:
                await asyncio.sleep(self.update_interval)
                if not self._initialized:
                    break
                await self._poll_device()
            except asyncio.CancelledError:
                _LOGGER.info("Polling stopped")
                break
            except Exception as err:
                _LOGGER.warning(f"Poll error: {err}")
                await asyncio.sleep(5)

    def _trigger_reconnect(self, *, force: bool = False) -> None:
        """Kick off reconnection in the background (never blocks the poll loop).

        A single guarded task does the work; repeated calls while it's running
        are no-ops. Keeping this off the poll loop is what lets the liveness
        check keep running every interval instead of stalling on the reconnect
        lock for minutes.
        """
        if self._reconnect_task and not self._reconnect_task.done():
            return

        async def _run() -> None:
            try:
                if force:
                    await self.ble_manager.force_reconnect(self.address)
                else:
                    await self.ble_manager.ensure_connected(self.address)
            except Exception as err:
                _LOGGER.warning(f"Reconnect task error: {err}")

        self._reconnect_task = asyncio.create_task(_run())

    async def _poll_device(self) -> None:
        """Send BLE commands to refresh device data."""
        if not self.ble_manager.connected_devices.get(self.address):
            _LOGGER.debug("Device not connected, requesting reconnect")
            self._trigger_reconnect()
            self.async_update_listeners()  # push connecting/reconnecting status
            return

        _LOGGER.debug("Polling device...")

        await self.commands.get_battery()
        await asyncio.sleep(0.4)
        await self.commands.get_device_state()
        await asyncio.sleep(0.4)
        await self.commands.get_device_update()
        await asyncio.sleep(0.4)

        # Device info (firmware/serial) is only fetched once at init. If that
        # handshake was interrupted (e.g. a flapping connection), re-fetch it
        # here until firmware is known, so it self-heals without a reload.
        if not self.device.firmware:
            await self.commands.get_device_details()
            await asyncio.sleep(0.4)

        # Liveness check (time-based): writes to a vanished-but-"connected"
        # device can silently "succeed" without ever returning data, so we can't
        # rely on write failures. If no notification has arrived for too long,
        # the device is gone (e.g. unplugged) — force a reconnect so recovery
        # (and detecting a replug) doesn't require an HA restart. Non-blocking so
        # the next poll still runs and re-evaluates.
        last = self.ble_manager.last_notification
        idle = None if last is None else (time.monotonic() - last)
        if idle is None or idle > self._data_timeout:
            _LOGGER.warning(
                "No data for %s — forcing reconnect",
                "ever" if idle is None else f"{idle:.0f}s",
            )
            self._trigger_reconnect(force=True)
            self.async_update_listeners()  # push reconnecting status
            return

        # Sync device clock every ~60 minutes to prevent drift
        self._poll_count += 1
        polls_per_hour = max(1, 3600 // self.update_interval)
        if self._poll_count % polls_per_hour == 0:
            _LOGGER.debug("Periodic time sync")
            await self.commands.set_datetime()
            await asyncio.sleep(0.2)

        self.async_update_listeners()
        _LOGGER.debug("Poll complete")

    # ── Public actions ─────────────────────────────────────────────

    async def async_set_device_mode(self, state: int, mode: int) -> None:
        await self.commands.set_device_mode(state, mode)

    async def async_reset_filter(self) -> None:
        await self.commands.set_reset_filter()

    async def async_set_device_config(self, config_data: list) -> None:
        await self.commands.set_device_config(config_data)

    async def async_update_config(self, **overrides) -> None:
        """Update device config with overrides, keeping everything else unchanged."""
        config = self.device.config
        config_data = [
            overrides.get("smart_time_on", config.get("smart_time_on", 30)),
            overrides.get("smart_time_off", config.get("smart_time_off", 60)),
            overrides.get("led_switch", config.get("led_switch", 1)),
            overrides.get("led_brightness", config.get("led_brightness", 80)),
            overrides.get("led_on_byte1", config.get("led_on_byte1", 0)),
            overrides.get("led_on_byte2", config.get("led_on_byte2", 0)),
            overrides.get("led_off_byte1", config.get("led_off_byte1", 0)),
            overrides.get("led_off_byte2", config.get("led_off_byte2", 0)),
            overrides.get("do_not_disturb_switch", config.get("do_not_disturb_switch", 0)),
            overrides.get("dnd_on_byte1", config.get("dnd_on_byte1", 0)),
            overrides.get("dnd_on_byte2", config.get("dnd_on_byte2", 0)),
            overrides.get("dnd_off_byte1", config.get("dnd_off_byte1", 0)),
            overrides.get("dnd_off_byte2", config.get("dnd_off_byte2", 0)),
            overrides.get("is_locked", config.get("is_locked", 0)),
        ]
        await self.async_set_device_config(config_data)

    async def async_request_refresh(self) -> None:
        """On-demand refresh (e.g. after a switch toggle)."""
        if not self._initialized:
            return
        try:
            await self._poll_device()
        except Exception as err:
            _LOGGER.warning(f"Refresh failed: {err}")

    # ── Options ────────────────────────────────────────────────────

    async def _on_options_updated(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        old = self.update_interval
        self.update_interval = entry.options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
        self._data_timeout = max(30.0, self.update_interval * 3)
        if old != self.update_interval:
            _LOGGER.info(f"Update interval changed: {old}s → {self.update_interval}s")

    # ── Cleanup ────────────────────────────────────────────────────

    async def _cleanup(self) -> None:
        """Cancel tasks and disconnect."""
        current_task = asyncio.current_task()
        tasks = (
            ("_consumer_task", self._consumer_task),
            ("_polling_task", self._polling_task),
            ("_initialization_task", self._initialization_task),
            ("_reconnect_task", self._reconnect_task),
        )
        for attr, task in tasks:
            if task is current_task:
                continue
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if task is not current_task:
                setattr(self, attr, None)

        if self.address in self.ble_manager.connected_devices:
            await self.ble_manager.stop_notifications(self.address, Constants.READ_UUID)
            await self.ble_manager.disconnect_device(self.address)

        self._initialized = False

    # ── Listener management ────────────────────────────────────────

    def async_add_listener(self, update_callback, context=None) -> callable:
        self._listeners.add(update_callback)
        def remove():
            self._listeners.discard(update_callback)
        return remove

    def async_remove_listener(self, update_callback) -> None:
        self._listeners.discard(update_callback)

    def async_update_listeners(self) -> None:
        for cb in self._listeners:
            cb()

    # ── Data access ────────────────────────────────────────────────

    @property
    def current_data(self) -> dict[str, Any]:
        return {
            "status": self.device.status,
            "config": self.device.config,
            "info": self.device.info,
            "name": self.device.name_readable,
            "product_name": self.device.product_name,
            "firmware": self.device.firmware,
            "serial": self.device.serial,
        }
