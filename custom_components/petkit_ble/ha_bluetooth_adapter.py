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

# After an unload: the waits, in seconds, before each further attempt to
# disconnect a client whose disconnect keeps failing.
LEFTOVER_DISCONNECT_DELAYS = (1.0, 5.0, 15.0, 60.0)

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
        self.connectiondata: dict[str, Any] = {}
        self.queue: asyncio.Queue = asyncio.Queue(10)
        self._ble_device = None

        # Connection state
        self._connection_status = ConnectionStatus.DISCONNECTED
        self._last_seen: str | None = None
        self._last_notification: float | None = None  # monotonic; set only on real data
        self._connection_attempts = 0
        self._connection_error: str | None = None
        self._reconnect_lock = asyncio.Lock()
        # Reconnects started from the write path. They are tracked so that an
        # unload can cancel them; an untracked one would keep reconnecting and
        # hold the fountain's only connection after the entry is gone.
        self._background_tasks: set[asyncio.Task] = set()
        self._closed = False
        # Clients whose disconnect() raised. One may still hold the fountain's
        # only connection, so it is kept here, never used as a connection, and
        # disconnected again before the next connect and on unload.
        self._undisconnected: list[Any] = []
        self._leftover_task: asyncio.Task | None = None

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
                # Looser than the discovery check on purpose: this only reads
                # the model byte for an address that is already configured, and
                # missing it would treat a CTW3 as a W5.
                if info.name and any(t in info.name for t in ["W4", "W5", "CTW2", "CTW3"]):
                    # No made-up model byte when there is no service data: the
                    # model then comes from an earlier connection or the name.
                    service_data = info.service_data or {}
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

            for addr, dev in petkit_devices.items():
                self.logger.debug("Found device: %s (%s)", dev.name, addr)
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

            # A client that could not be disconnected earlier may still hold
            # the fountain's only connection; this connect would fail behind it.
            await self._retry_failed_disconnects()

            self._ble_device = bluetooth.async_ble_device_from_address(
                self.hass, address, connectable=True
            )
            if not self._ble_device:
                # bleak-retry-connector#302: recent versions (HA 2026.7 beta)
                # regressed on MAC case — also try the lowercase form for lookup.
                self._ble_device = bluetooth.async_ble_device_from_address(
                    self.hass, address.lower(), connectable=True
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
            # Workaround for bleak-retry-connector#302: an uppercase MAC fails to
            # connect on recent versions (HA 2026.7 beta) while lowercase works.
            # Harmless on older versions (matched case-insensitively there).
            self.logger.debug(
                "establish_connection: ble_device.address=%r name=%r details=%r passing addr=%r",
                getattr(self._ble_device, "address", None),
                getattr(self._ble_device, "name", None),
                getattr(self._ble_device, "details", None),
                address.lower(),
            )
            client = await establish_connection(
                BleakClient, self._ble_device, address.lower(), timeout=10.0,
                disconnected_callback=_on_disconnect,
            )

            # Drop commands queued for the previous link before the consumer
            # can send them: a config write among them would overwrite settings
            # changed while disconnected. Polls are re-queued on the next cycle.
            dropped = 0
            while not self.queue.empty():
                self.queue.get_nowait()
                self.queue.task_done()
                dropped += 1
            if dropped:
                self.logger.debug("Dropped %s command(s) queued before reconnect", dropped)
            # Settings may have changed while the link was down; config writes
            # wait for a fresh CMD 211 answer on this connection.
            self.event_handler.device.config_received = False
            self.connected_devices[address] = client
            self._set_status(ConnectionStatus.CONNECTED)
            self._touch()
            self.logger.info(
                f"✅ Connected to {address} (attempt {self._connection_attempts + 1})"
            )
            self._connection_attempts = 0
            return True

        except asyncio.TimeoutError:
            self._connection_attempts += 1
            self.logger.debug("Connect timeout (attempt %s)", self._connection_attempts)
            if self._connection_attempts % 3 == 0:
                self.logger.warning(f"⏱️ Timeout after {self._connection_attempts} attempts")
            return False

        except Exception as err:
            self._connection_attempts += 1
            # Full detail every attempt at debug level for diagnosing connect failures.
            self.logger.debug(
                "Connect failed (attempt %s): %s: %s",
                self._connection_attempts, type(err).__name__, err,
            )
            if self._connection_attempts % 5 == 0:
                self.logger.warning(
                    f"❌ Connection failed ({self._connection_attempts}x): {type(err).__name__}: {err}"
                )
            return False

    async def _disconnect_client(self, address: str) -> bool:
        """Disconnect the tracked client, then stop tracking it.

        The client stays in connected_devices while its disconnect is awaited.
        A task cancelled at that point (an unload during a reconnect) then
        leaves it tracked, so the cleanup that follows can still disconnect
        it; untracked, it would keep the fountain's only connection.

        A client whose disconnect raises may be connected still. It leaves
        connected_devices, so nothing writes to it or takes it for a working
        link, and goes to _undisconnected to be disconnected again later.
        Returns False in that case.
        """
        client = self.connected_devices.get(address)
        if client is None:
            return True
        ok = True
        try:
            await client.disconnect()
        except Exception as err:
            ok = False
            self.logger.debug("Disconnect failed: %s: %s", type(err).__name__, err)
        # Only if it is still the tracked one: a reconnect may have replaced it.
        if self.connected_devices.get(address) is client:
            self.connected_devices.pop(address, None)
        if not ok and client not in self._undisconnected:
            self._undisconnected.append(client)
        return ok

    async def _retry_failed_disconnects(self) -> bool:
        """Disconnect again the clients whose disconnect failed; True if none is left."""
        for client in list(self._undisconnected):
            if getattr(client, "is_connected", True):
                try:
                    await client.disconnect()
                except Exception as err:
                    self.logger.debug(
                        "Disconnect failed again: %s: %s", type(err).__name__, err
                    )
                    continue
            self._undisconnected.remove(client)
        return not self._undisconnected

    async def disconnect_device(self, address: str) -> bool:
        """Disconnect from device; False if a client could not be disconnected."""
        await self._disconnect_client(address)
        clean = await self._retry_failed_disconnects()
        if not clean:
            self.logger.warning("Error during disconnect")
            self._schedule_leftover_disconnects()
        self._set_status(ConnectionStatus.DISCONNECTED)
        return clean

    def _schedule_leftover_disconnects(self) -> None:
        """Once closed, keep trying for a while to disconnect what is left.

        While the entry is loaded the next connect retries a failed disconnect.
        After the unload nothing would, and a client left connected would keep
        the fountain's only connection, also from the entry's next setup.
        """
        if not self._closed or not self._undisconnected:
            return
        if self._leftover_task and not self._leftover_task.done():
            return
        self._leftover_task = self.hass.async_create_background_task(
            self._disconnect_leftovers(), name=f"petkit_ble disconnect {self.address}"
        )

    async def _disconnect_leftovers(self) -> None:
        """Retry the failed disconnects a few times, then say so and stop."""
        for delay in LEFTOVER_DISCONNECT_DELAYS:
            await asyncio.sleep(delay)
            if await self._retry_failed_disconnects():
                return
        self.logger.warning(
            "Could not disconnect from %s after unloading. If it cannot be reached "
            "again, restart the Bluetooth adapter or Home Assistant",
            self.address,
        )

    # ── Read / Write ───────────────────────────────────────────────

    async def write_characteristic(self, address: str, characteristic_uuid: str, data: bytes) -> bool:
        """Write a GATT characteristic. Triggers reconnection on failure."""
        client = self.connected_devices.get(address)
        if not client:
            self.logger.debug("Device not connected for write, requesting reconnect")
            self._request_reconnect(address)
            return False

        if hasattr(client, 'is_connected') and not client.is_connected:
            self.logger.warning("Client stale, requesting reconnect")
            await self._disconnect_client(address)
            self._set_status(ConnectionStatus.RECONNECTING)
            self._request_reconnect(address)
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
            await self._disconnect_client(address)
            self._set_status(ConnectionStatus.RECONNECTING)
            self._request_reconnect(address)
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
        # CMD 213 (0xD5) replies carry the device id the auth secret is
        # derived from; never log their raw bytes.
        if data and len(data) > 3 and data[3] == 0xD5:
            shown = f"<CMD 213, {len(data)} bytes redacted>"
        else:
            shown = data.hex() if data else "None"
        self.logger.debug(f"📨 Notification from {sender}: {shown}")
        if not self.event_handler:
            return
        # Only a well-formed frame proves the device is alive; the handler
        # returns None for empty or malformed notifications, which must not
        # postpone the liveness reconnect. A handler error happens after the
        # frame was validated, so it still counts as a sign of life.
        try:
            parsed = await self.event_handler.handle_notification(sender, data)
        except Exception as err:
            self.logger.error(f"Error processing notification: {err}")
            parsed = True
        if parsed is not None:
            self._last_notification = time.monotonic()

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

    def _request_reconnect(self, address: str) -> None:
        """Start a tracked background reconnect, unless the adapter is closed.

        One at a time: while one is pending, further requests are covered by it.
        """
        if self._closed or any(not task.done() for task in self._background_tasks):
            return
        task = self.hass.async_create_background_task(
            self.ensure_connected(address), name=f"petkit_ble reconnect {address}"
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def async_cancel_reconnects(self) -> None:
        """Cancel the background reconnects started from the write path."""
        current = asyncio.current_task()
        tasks = [
            task
            for task in self._background_tasks
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def async_close(self) -> None:
        """Stop reconnecting for good; called when the config entry unloads."""
        self._closed = True
        await self.async_cancel_reconnects()
        if not await self._retry_failed_disconnects():
            self._schedule_leftover_disconnects()

    async def force_reconnect(self, address: str) -> bool:
        """Tear down a stale-but-'connected' client and reconnect from scratch.

        Used when the device stops responding (e.g. unplugged) but the BLE stack
        still reports the client as connected, so the normal write-failure path
        never fires. Dropping the client makes ensure_connected actually run the
        reconnection loop again (and the attempt counter starts climbing).
        """
        self.logger.warning(f"💀 {address} unresponsive — forcing reconnect")
        await self._disconnect_client(address)
        self._set_status(ConnectionStatus.RECONNECTING)
        return await self.ensure_connected(address)

    async def ensure_connected(self, address: str) -> bool:
        """Single entry point for reconnection. Uses a lock to prevent parallel attempts."""
        if self.connected_devices.get(address):
            return True
        if self._closed:
            return False

        if self._reconnect_lock.locked():
            # Another reconnection is already running — wait for it
            async with self._reconnect_lock:
                return bool(self.connected_devices.get(address))

        async with self._reconnect_lock:
            return await self._reconnection_loop(address)

    async def _reconnection_loop(self, address: str) -> bool:
        """Attempt reconnection with progressive delays and periodic BLE reset."""
        while not self._closed and not self.connected_devices.get(address):
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
                await self._disconnect_client(address)
                self._connection_attempts += 1

            delay = self._retry_delay()
            if self._connection_attempts % 10 == 0:
                self.logger.debug(f"Next retry in {delay:.1f}s (attempt #{self._connection_attempts})")
            await asyncio.sleep(delay)

        return bool(self.connected_devices.get(address))

    async def _reset_ble_stack(self, address: str) -> None:
        """Full BLE stack reset — disconnect, discard client, re-discover device."""
        try:
            # Force disconnect stale client
            await self._disconnect_client(address)

            # Discard cached BLE device reference so HA rediscovers it fresh
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

