from .parsers import Parsers
from .utils import Utils

# Lifetime totals that only ever grow, and the values derived from them.
LIFETIME_TOTALS = ("pump_runtime", "pump_runtime_readable", "purified_water", "energy_consumed")
# After a reconnect the fountain reports the total it last stored, a few
# minutes behind what it already sent. A drop up to this share of the total is
# such a step back, and the shown totals hold until the fountain catches up;
# a bigger one is a real reset (e.g. after a power loss) and is taken. Home
# Assistant's recorder uses the same 10% to tell a glitch from a meter reset.
STEP_BACK_SHARE = 0.1

class EventHandlers:
    def __init__(self, device, commands, logger):
        self.logger = logger
        self.device = device        
        
        # Registry of command values to handler methods
        self.handlers = {
            66: Parsers.device_battery,
            86: Parsers.device_synchronization,
            200: Parsers.device_firmware,
            210: Parsers.device_state,
            211: Parsers.device_configuration,
            213: Parsers.device_identifiers,
            230: Parsers.device_status,
        }

    def _parser_context(self):
        return {
            "alias": self.device.alias,
            "device_type": self.device.device_type,
            "name": self.device.name,
        }

    def _hold_lifetime_totals(self, data):
        """Keep the lifetime totals when the fountain steps back after a reconnect."""
        new = data.get("pump_runtime")
        shown = self.device.status.get("pump_runtime")
        if not isinstance(new, int) or not isinstance(shown, int):
            return data
        if (1 - STEP_BACK_SHARE) * shown <= new < shown:
            self.logger.debug(
                "Pump runtime stepped back from %s to %s; keeping the totals", shown, new
            )
            # Only what is already shown is held: a total that has no value
            # yet (e.g. energy after a CMD 210 frame alone) takes this one.
            shown_totals = self.device.status
            return {
                key: value
                for key, value in data.items()
                if key not in LIFETIME_TOTALS or shown_totals.get(key) is None
            }
        return data

    async def handle_notification(self, sender, message):
        parsed_data = Utils.parse_bytearray(message)
        if parsed_data is None:
            self.logger.debug("Ignoring malformed notification (%d bytes)", len(message or b""))
            return None
        cmd = parsed_data['cmd']
        self.logger.debug(f"Received command {cmd}")
        
        # CMD 213 carries the device id that the BLE auth secret is derived
        # from, so its payload is never logged.
        sensitive = cmd == 213
        if not sensitive:
            self.logger.debug(f"Parsed data:\n{parsed_data}")

        data = None

        if cmd in self.handlers:
            handler = self.handlers[cmd]
            data = handler(parsed_data['data'], self._parser_context())
            if not sensitive:
                self.logger.debug(f"Parsed data\n{data}")

            if isinstance(data, dict) and "error" in data:
                # Debug only: a device that keeps sending short frames must
                # not be able to fill the log.
                self.logger.debug("Skipping command %s: %s", cmd, data["error"])
                return parsed_data

            # Update info (firmware, identifiers, sync)
            if cmd in [86, 200, 213]:
                self.device.info = data
                self.logger.debug(
                    "Device info after command %s: firmware=%s, serial=%s",
                    cmd, self.device.firmware, self.device.serial,
                )

            # Update status
            if cmd in [66, 210, 211, 230]:
                data = self._hold_lifetime_totals(data)
                self.device.status = data
            # Writes resend every field, so only a complete answer (child lock
            # byte included) makes them safe; a short one blocks them again.
            if cmd == 211:
                self.device.config_received = data.get("is_locked") is not None
            if cmd in (210, 230) and "power_status" in data and "mode" in data:
                self.device.status_received = True

        return parsed_data
