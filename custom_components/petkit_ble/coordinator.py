"""Data update coordinator for Petkit BLE integration."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_processor import ActiveBluetoothProcessorCoordinator
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import UpdateFailed

from .const import CONF_ADDRESS, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, DOMAIN
from .ha_bluetooth_adapter import HABluetoothAdapter
from .PetkitW5BLEMQTT.commands import Commands
from .PetkitW5BLEMQTT.constants import Constants
from .PetkitW5BLEMQTT.device import Device
from .PetkitW5BLEMQTT.event_handlers import EventHandlers
from .PetkitW5BLEMQTT.parsers import Parsers

_LOGGER = logging.getLogger(__name__)

# Placeholder serial used until the device answers CMD 213.
FALLBACK_SERIAL_PREFIX = "PETKIT_"

# Raise a repair issue when the fountain keeps advertising but has sent no data
# for this long: it is in range, so the Bluetooth stack on the host is stuck
# (seen as bluetoothd "No matching connection for device" until a host reboot).
UNREACHABLE_AFTER = 3600.0  # seconds
REACHABILITY_CHECK_INTERVAL = timedelta(minutes=5)


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
        # Baseline for the reachability check until the first notification.
        self._started_at: float = time.monotonic()
        self._issue_id = f"cannot_connect_in_range_{entry.entry_id}"

        entry.async_on_unload(entry.add_update_listener(self._on_options_updated))
        entry.async_on_unload(
            async_track_time_interval(
                hass, self._check_reachability, REACHABILITY_CHECK_INTERVAL
            )
        )

    # ── Lifecycle ──────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Start the coordinator and kick off initialization."""
        super().async_start()
        if not self._initialized:
            self._initialization_task = asyncio.create_task(self._initialization_loop())

    async def async_shutdown(self) -> None:
        """Shutdown and release resources."""
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        await self._cleanup()

    @callback
    def _check_reachability(self, _now=None) -> None:
        """Raise or clear the "in range but unreachable" repair issue.

        A fountain that is off or out of range stops advertising and its
        entities go unavailable, which needs no issue. One that keeps
        advertising but sends no data for an hour points at the host's
        Bluetooth stack, which only a host or adapter restart fixes.
        """
        last = self.ble_manager.last_notification or self._started_at
        silent = time.monotonic() - last
        in_range = bluetooth.async_address_present(
            self.hass, self.address, connectable=True
        )
        if in_range and silent >= UNREACHABLE_AFTER:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="cannot_connect_in_range",
                translation_placeholders={
                    "name": self.entry.title,
                    "hours": str(int(silent // 3600)),
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)

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

            # The device only answers CMD 213 (device id + serial) once the
            # session is authenticated, so ask again after CMD 73.
            await self.commands.get_device_details(force=True)
            await asyncio.sleep(1.0)

            await self.commands.get_device_info()
            await asyncio.sleep(0.75)

            # Read the settings (CMD 211); config writes wait for this answer.
            await self.commands.get_device_config()
            await asyncio.sleep(0.5)

            # Fill in defaults for any uninitialized fields
            if self.device.serial == "Uninitialized":
                self.device.serial = f"{FALLBACK_SERIAL_PREFIX}{self.address.replace(':', '')[-6:]}"
            if self.device.name == "Uninitialized":
                self.device.name = "Water Fountain"
                self.device.name_readable = "Water Fountain"
            if not self.device.product_name or self.device.product_name == "Uninitialized":
                self.device.product_name = "Petkit BLE Water Fountain"
            # Firmware intentionally left as reported — no fake default, so the
            # firmware sensor shows "unknown" until the device reports a version

            self._initialized = True
            _LOGGER.info(f"Device ready: {self.device.name_readable} ({self.device.serial})")
            self._sync_device_registry()

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
                    ok = await self.ble_manager.force_reconnect(self.address)
                else:
                    ok = await self.ble_manager.ensure_connected(self.address)
                # A reconnect only opens the link + notifications. If the device
                # power-cycled it has forgotten its authenticated session (CMD 73)
                # and will drop us after a few seconds → connect/disconnect flap.
                # Re-run the auth handshake, exactly like a fresh setup does.
                if ok and self.ble_manager.connected_devices.get(self.address):
                    await self._reinit_device()
            except Exception as err:
                _LOGGER.warning(f"Reconnect task error: {err}")

        self._reconnect_task = asyncio.create_task(_run())

    def _sync_device_registry(self) -> None:
        """Push the reported serial and firmware into the device registry.

        Entity device_info is only read when the entities are added, which is
        before the device has answered, so the registry would otherwise keep
        an empty serial and "Unknown" firmware forever.
        """
        dev_reg = dr.async_get(self.hass)
        entry = dev_reg.async_get_device_by_identifier(
            (DOMAIN, self.address), self.entry.entry_id
        )
        if entry is None:
            return
        changes: dict[str, str] = {}
        serial = self._clean_serial()
        if self._serial_known() and entry.serial_number != serial:
            changes["serial_number"] = serial
        if self.device.firmware and entry.sw_version != str(self.device.firmware):
            changes["sw_version"] = str(self.device.firmware)
        if changes:
            dev_reg.async_update_device(entry.id, **changes)

    def _clean_serial(self) -> str:
        """Return the serial without the NUL/space padding of the CMD 213 field."""
        return str(self.device.serial or "").strip("\x00 ")

    def _serial_known(self) -> bool:
        """Return True once the device has reported its real serial (CMD 213)."""
        serial = self._clean_serial()
        return (
            bool(serial)
            and serial != "Uninitialized"
            and not serial.startswith(FALLBACK_SERIAL_PREFIX)
        )

    async def _reinit_device(self) -> None:
        """Re-authenticate after every reconnect.

        A power-cycled fountain forgets its CMD 73 session and drops the link a
        few seconds later. Nothing reports that state on this path: CMD 213
        carries no init flag and is only answered after authentication, and
        CMD 86 is never sent. So CMD 73 is always re-sent; it is harmless on a
        device that only briefly dropped. It is built from the device's fixed
        id, so the cached id stays valid across reconnects.
        """
        try:
            _LOGGER.info("Re-authenticating after reconnect")
            await self.commands.init_device()
            await asyncio.sleep(1.0)
            # The serial is only answered after CMD 73.
            if not self._serial_known():
                await self.commands.get_device_details(force=True)
                await asyncio.sleep(1.0)
        except Exception as err:
            _LOGGER.warning(f"Re-init after reconnect failed: {err}")

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
        # The status frames carry no child lock byte, so read the config too.
        await self.commands.get_device_config()
        await asyncio.sleep(0.4)

        # Device info (firmware/serial) is only fetched once at init. If that
        # handshake was interrupted (e.g. a flapping connection), re-fetch it
        # here until firmware is known, so it self-heals without a reload.
        if not self.device.firmware:
            await self.commands.get_device_info()
            await asyncio.sleep(0.4)
        # The device clears its id after CMD 73, so the serial (not device_id)
        # tells whether CMD 213 has been answered.
        if not self._serial_known():
            await self.commands.get_device_details(force=True)
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
        self._sync_device_registry()
        _LOGGER.debug("Poll complete")

    # ── Public actions ─────────────────────────────────────────────

    async def async_set_device_mode(self, state: int, mode: int) -> None:
        await self.commands.set_device_mode(state, mode)

    async def async_reset_filter(self) -> None:
        await self.commands.set_reset_filter()

    @property
    def is_ctw3(self) -> bool:
        """Return True for the CTW3 (Eversweet Max), whose config layout differs."""
        return Parsers._is_ctw3(
            {"device_type": self.device.device_type, "name": self.device.name}
        )

    @property
    def config_writable(self) -> bool:
        """Return True when a CMD 221 config write is safe to send."""
        return self.device.config_received and not self.is_ctw3

    def _ensure_config_writable(self) -> None:
        """Refuse a config write that would send wrong or default settings."""
        if self.is_ctw3:
            # The CTW3 reports its config in a different layout (battery work
            # and sleep times); the W4/W5 write layout would corrupt it.
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="config_write_unsupported"
            )
        if not self.device.config_received:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="config_not_received"
            )

    async def async_set_device_config(self, config_data: list) -> None:
        self._ensure_config_writable()
        await self.commands.set_device_config(config_data)

    async def async_update_config(self, **overrides) -> None:
        """Update device config with overrides, keeping everything else unchanged."""
        self._ensure_config_writable()
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
