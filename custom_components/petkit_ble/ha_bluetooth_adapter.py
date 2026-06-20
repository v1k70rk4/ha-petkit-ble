"""Home Assistant Bluetooth adapter for Petkit BLE integration."""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class ConnectionStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


class HABluetoothAdapter:
    """Adapter to bridge HA's bluetooth integration with existing Petkit BLE library."""

    def __init__(self, hass: HomeAssistant, address: str, event_handler=None, logger=None):
        """Initialize the HA Bluetooth adapter."""
        self.hass = hass
        self.address = address
        self.event_handler = event_handler
        self.logger = logger or _LOGGER
        self.connected_devices: dict[str, Any] = {}
        self.available_devices: dict[str, Any] = {}
        self.connectiondata: dict[str, Any] = {}
        self.queue: asyncio.Queue = asyncio.Queue(10)
        self._client = None
        self._ble_device = None

        # Connection state
        self._connection_status = ConnectionStatus.DISCONNECTED
        self._last_seen: str | None = None
        self._last_notification: float | None = None  # monotonic; set only on real data
        self._connection_attempts = 0
        self._connection_error: str | None = None
        self._reconnect_lock = asyncio.Lock()
        self._reconnection_active = False

    # ── Public properties ──────────────────────────────────────────

    @property
    def connection_status(self) -> str:
        return self._connection_status.value

    @property
    def last_seen(self) -> str | None:
        return self._last_seen

    @property
    def last_notification(self) -> float | None:
        """Monotonic timestamp of the last received notification (real data)."""
        return self._last_notification

    @property
    def connection_attempts(self) -> int:
        return self._connection_attempts

    @property
    def connection_error(self) -> str | None:
        return self._connection_error

    # ── Scan ───────────────────────────────────────────────────────

    async def scan(self) -> dict[str, Any]:
        """Scan for Petkit BLE devices using HA's bluetooth integration."""
        try:
            discovered = bluetooth.async_discovered_service_info(self.hass)
            petkit_devices: dict[str, Any] = {}

            for info in discovered:
                if info.name and any(t in info.name for t in ["W4", "W5", "CTW2", "CTW3"]):
                    service_data = info.service_data or {"default": [0, 0, 0, 0, 0, 206]}
                    mock = type('MockDevice', (), {
                        'name': info.name,
                        'address': info.address,
                        'rssi': info.rssi,
                        'details': {
                            'props': {
                                'RSSI': info.rssi,
                                'ServiceData': service_data,
                            }
                        }
                    })()
                    petkit_devices[info.address] = mock
                    self.connectiondata[info.address] = mock

            self.available_devices = petkit_devices
            for addr, dev in petkit_devices.items():
                self.logger.info(f"Found device: {dev.name} ({addr})")
            return petkit_devices

        except Exception as err:
            self.logger.error(f"Error scanning for devices: {err}")
            return {}

    # ── Connect / Disconnect ───────────────────────────────────────

    async def connect_device(self, address: str) -> bool:
        """Connect to device using HA's bluetooth integration."""
        try:
            self._set_status(ConnectionStatus.CONNECTING if self._connection_attempts == 0
                             else ConnectionStatus.RECONNECTING)

            self._ble_device = bluetooth.async_ble_device_from_address(
                self.hass, address, connectable=True
            )
            if not self._ble_device:
                self._connection_attempts += 1
                if self._connection_attempts % 5 == 0:
                    self.logger.warning(f"Device not found after {self._connection_attempts} attempts")
                return False

            def _on_disconnect(disconnected_client) -> None:
                # Fires on a clean disconnect — drop the stale client so the next
                # poll/consumer cycle triggers reconnection instead of writing to a
                # dead handle. (Ungraceful power loss may not fire this — the
                # coordinator's liveness check covers that case.)
                #
                # Identity check is critical: a late callback from a PREVIOUS
                # client must not evict the current healthy one (which would
                # leave that client connected but untracked, holding the single
                # allowed BLE slot forever).
                if self.connected_devices.get(address) is not disconnected_client:
                    return
                self.logger.info(f"🔌 {address} disconnected")
                self.connected_devices.pop(address, None)
                self._set_status(ConnectionStatus.DISCONNECTED)

            from bleak import BleakClient
            self._client = await establish_connection(
                BleakClient, self._ble_device, address, timeout=10.0,
                disconnected_callback=_on_disconnect,
            )

            self.connected_devices[address] = self._client
            self._set_status(ConnectionStatus.CONNECTED)
            self._touch()
            self.logger.info(
                f"✅ Connected to {address} (attempt {self._connection_attempts + 1})"
            )
            self._connection_attempts = 0
            return True

        except asyncio.TimeoutError:
            self._connection_attempts += 1
            if self._connection_attempts % 3 == 0:
                self.logger.warning(f"⏱️ Timeout after {self._connection_attempts} attempts")
            return False

        except Exception as err:
            self._connection_attempts += 1
            if self._connection_attempts % 5 == 0:
                self.logger.warning(f"❌ Connection failed ({self._connection_attempts}x): {err}")
            return False

    async def disconnect_device(self, address: str) -> bool:
        """Disconnect from device."""
        try:
            client = self.connected_devices.pop(address, None)
            if client and hasattr(client, 'disconnect'):
                await client.disconnect()
            self._set_status(ConnectionStatus.DISCONNECTED)
            return True
        except Exception as err:
            self.connected_devices.pop(address, None)
            self.logger.warning(f"Error during disconnect: {err}")
            self._set_status(ConnectionStatus.DISCONNECTED)
            return False

    # ── Read / Write ───────────────────────────────────────────────

    async def read_characteristic(self, address: str, characteristic_uuid: str) -> bytes | None:
        """Read a GATT characteristic."""
        client = self.connected_devices.get(address)
        if not client:
            self.logger.debug(f"Device {address} not connected for read")
            return None
        try:
            data = await client.read_gatt_char(characteristic_uuid)
            return data
        except Exception as err:
            self.logger.error(f"Read failed ({characteristic_uuid}): {err}")
            return None

    async def write_characteristic(self, address: str, characteristic_uuid: str, data: bytes) -> bool:
        """Write a GATT characteristic. Triggers reconnection on failure."""
        client = self.connected_devices.get(address)
        if not client:
            self.logger.debug("Device not connected for write, requesting reconnect")
            asyncio.create_task(self.ensure_connected(address))
            return False

        if hasattr(client, 'is_connected') and not client.is_connected:
            self.logger.warning("Client stale, requesting reconnect")
            stale = self.connected_devices.pop(address, None)
            if stale:
                try:
                    await stale.disconnect()
                except Exception:
                    pass
            self._set_status(ConnectionStatus.RECONNECTING)
            asyncio.create_task(self.ensure_connected(address))
            return False

        try:
            # Bound the write — a vanished-but-"connected" device can otherwise
            # hang write_gatt_char forever, stalling the consumer and the whole
            # poll loop until an HA restart.
            await asyncio.wait_for(
                client.write_gatt_char(characteristic_uuid, data),
                timeout=10.0,
            )
            self._touch()
            return True
        except (Exception, asyncio.TimeoutError) as err:
            self.logger.warning(f"Write failed: {err}")
            # Physically disconnect the dead client — popping alone leaves it
            # holding the device's single BLE connection slot as an untracked
            # ghost, which blocks every future reconnect until an HA restart.
            stale = self.connected_devices.pop(address, None)
            if stale:
                try:
                    await stale.disconnect()
                except Exception:
                    pass
            self._set_status(ConnectionStatus.RECONNECTING)
            asyncio.create_task(self.ensure_connected(address))
            return False

    # ── Notifications ──────────────────────────────────────────────

    async def start_notifications(self, address: str, characteristic_uuid: str) -> bool:
        """Start GATT notifications, retrying while BlueZ resolves the GATT tree.

        Right after a reconnect the device's characteristics may not be resolved
        yet, which surfaces as 'UnknownObject' / 'Unlikely Error'. Those are
        transient — a short wait and retry usually succeeds. Returns False only
        if it genuinely can't subscribe, so the caller can tear the link down.
        """
        client = self.connected_devices.get(address)
        if not client:
            self.logger.error(f"Device {address} not connected")
            return False

        for attempt in range(4):
            if hasattr(client, "is_connected") and not client.is_connected:
                self.logger.debug("Client disconnected before notifications could start")
                return False

            try:
                await client.start_notify(characteristic_uuid, self._on_notification)
                self.logger.info(f"Notifications started ({characteristic_uuid})")
                return True
            except Exception as err:
                msg = str(err)

                if "NotPermitted" in msg or "acquired" in msg.lower():
                    # BlueZ still holds a stale subscription — clear it and retry.
                    self.logger.warning("Notifications still acquired, clearing and retrying...")
                    try:
                        await client.stop_notify(characteristic_uuid)
                    except Exception:
                        pass
                    await asyncio.sleep(0.3)
                    continue

                if any(s in msg for s in ("UnknownObject", "Unlikely", "UNLIKELY")) \
                        or "not connected" in msg.lower():
                    # GATT services not resolved yet — wait briefly and retry.
                    self.logger.debug(f"GATT not ready (attempt {attempt + 1}/4): {err}")
                    await asyncio.sleep(0.5)
                    continue

                self.logger.error(f"Error starting notifications: {err}")
                return False

        self.logger.warning("Could not start notifications after retries")
        return False

    async def stop_notifications(self, address: str, characteristic_uuid: str) -> bool:
        """Stop GATT notifications."""
        client = self.connected_devices.get(address)
        if not client:
            return False
        try:
            await client.stop_notify(characteristic_uuid)
            return True
        except Exception as err:
            self.logger.error(f"Error stopping notifications: {err}")
            return False

    async def _on_notification(self, sender, data):
        """Handle incoming BLE notification."""
        self._touch()
        self._last_notification = time.monotonic()
        self.logger.debug(f"📨 Notification from {sender}: {data.hex() if data else 'None'}")
        if self.event_handler:
            try:
                await self.event_handler.handle_notification(sender, data)
            except Exception as err:
                self.logger.error(f"Error processing notification: {err}")

    # ── Message queue ──────────────────────────────────────────────

    async def message_producer(self, message: bytes) -> None:
        """Add message to the write queue.

        Non-blocking: if the queue is full the consumer is stuck on a dead
        connection, so dropping the message is correct — blocking here would
        freeze the poll loop (and anything else awaiting a command).
        """
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.logger.warning("Write queue full — dropping command (device unresponsive?)")

    async def message_consumer(self, address: str, characteristic_uuid: str) -> None:
        """Consume messages from the queue and write them to the device."""
        while True:
            try:
                if not self.connected_devices.get(address):
                    await self.ensure_connected(address)
                    await asyncio.sleep(0.1)
                    continue

                message = await self.queue.get()
                await self.write_characteristic(address, characteristic_uuid, message)
                self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as err:
                self.logger.error(f"Message consumer error: {err}")
                await asyncio.sleep(1)

    # ── Reconnection (single entry point) ──────────────────────────

    async def force_reconnect(self, address: str) -> bool:
        """Tear down a stale-but-'connected' client and reconnect from scratch.

        Used when the device stops responding (e.g. unplugged) but the BLE stack
        still reports the client as connected, so the normal write-failure path
        never fires. Dropping the client makes ensure_connected actually run the
        reconnection loop again (and the attempt counter starts climbing).
        """
        self.logger.warning(f"💀 {address} unresponsive — forcing reconnect")
        client = self.connected_devices.pop(address, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        self._client = None
        self._set_status(ConnectionStatus.RECONNECTING)
        return await self.ensure_connected(address)

    async def ensure_connected(self, address: str) -> bool:
        """Single entry point for reconnection. Uses a lock to prevent parallel attempts."""
        if self.connected_devices.get(address):
            return True

        if self._reconnect_lock.locked():
            # Another reconnection is already running — wait for it
            async with self._reconnect_lock:
                return bool(self.connected_devices.get(address))

        async with self._reconnect_lock:
            self._reconnection_active = True
            try:
                return await self._reconnection_loop(address)
            finally:
                self._reconnection_active = False

    async def _reconnection_loop(self, address: str) -> bool:
        """Attempt reconnection with progressive delays and periodic BLE reset."""
        while not self.connected_devices.get(address):
            if self._connection_attempts % 5 == 0 or self._connection_attempts < 3:
                self.logger.info(f"🔁 Reconnection attempt #{self._connection_attempts + 1}")

            # Every 50 attempts, do a full BLE stack reset to recover from
            # corrupted BlueZ state that no amount of retries can fix
            if self._connection_attempts > 0 and self._connection_attempts % 50 == 0:
                self.logger.warning(
                    f"🔄 {self._connection_attempts} failed attempts — resetting BLE stack"
                )
                await self._reset_ble_stack(address)
                await asyncio.sleep(10)  # Give BlueZ time to recover
                continue

            if await self.connect_device(address):
                # A connection without working notifications is useless (no data
                # flows), so only count it as success if the subscribe works too.
                from .PetkitW5BLEMQTT.constants import Constants
                if await self.start_notifications(address, Constants.READ_UUID):
                    self.logger.info("📡 Notifications restarted")
                    return True

                # Connected but couldn't subscribe — tear it down and retry from
                # scratch instead of declaring a false success.
                self.logger.warning("Reconnected but notifications failed; tearing down to retry")
                client = self.connected_devices.pop(address, None)
                if client:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                self._connection_attempts += 1

            delay = self._retry_delay()
            if self._connection_attempts % 10 == 0:
                self.logger.debug(f"Next retry in {delay:.1f}s (attempt #{self._connection_attempts})")
            await asyncio.sleep(delay)

        return True

    async def _reset_ble_stack(self, address: str) -> None:
        """Full BLE stack reset — disconnect, discard client, re-discover device."""
        try:
            # Force disconnect stale client
            client = self.connected_devices.pop(address, None)
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass

            # Discard cached BLE device reference so HA rediscovers it fresh
            self._client = None
            self._ble_device = None

            # Re-scan to get a fresh BLE device handle from HA's bluetooth stack
            self.logger.info("🔍 Re-scanning for device...")
            await self.scan()

        except Exception as err:
            self.logger.warning(f"BLE stack reset error: {err}")

    def _retry_delay(self) -> float:
        """Progressive retry delay: fast at first, then backs off."""
        n = self._connection_attempts
        if n < 5:
            return 0.1
        if n < 10:
            return 0.5
        if n < 20:
            return 1.0
        if n < 50:
            return min(5.0, 1.0 + (n - 20) * 0.5)
        # After 50+ attempts, slow down significantly to avoid hammering
        return 15.0

    # ── Heartbeat (no-op, polling handled by coordinator) ──────────

    async def heartbeat(self, interval: int) -> None:
        """No-op — polling is handled by the coordinator."""
        pass

    # ── Internal helpers ───────────────────────────────────────────

    def _set_status(self, status: ConnectionStatus, error: str | None = None):
        """Update connection status, log only on change."""
        old = self._connection_status
        self._connection_status = status
        if error:
            self._connection_error = error
        if old != status:
            self.logger.info(f"Connection status: {old.value} → {status.value}")

    def _touch(self):
        """Update last-seen timestamp."""
        self._last_seen = dt_util.now().isoformat()

    def reset_connection_state(self):
        """Reset connection tracking for a clean restart."""
        self._connection_status = ConnectionStatus.DISCONNECTED
        self._connection_attempts = 0
        self._connection_error = None
