from .utils import Utils
from .parsers import Parsers

class EventHandlers:
    def __init__(self, device, commands, logger):
        self.logger = logger
        self.device = device        
        
        # Registry of command values to handler methods
        self.handlers = {
            66: Parsers.device_battery,
            #73: Parsers.device_init,
            86: Parsers.device_synchronization,
            200: Parsers.device_firmware,
            210: Parsers.device_state,
            211: Parsers.device_configuration,
            213: Parsers.device_identifiers,
            230: Parsers.device_status,
        }

        # Previously: Messages were forwarded to MQTT for Home Assistant integration

    def _parser_context(self):
        return {
            "alias": self.device.alias,
            "device_type": self.device.device_type,
            "name": self.device.name,
        }

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
                self.logger.warning(f"Skipping command {cmd}: {data['error']}")
                return parsed_data

            # Update info (firmware, identifiers, sync)
            if cmd in [86, 200, 213]:
                self.logger.info(f"Updating device info with command {cmd}")
                self.device.info = data
                self.logger.info(f"Device info after update: firmware={self.device.firmware}, serial={self.device.serial}")

            # Update status
            if cmd in [66, 210, 211, 230]:
                self.device.status = data
            # Writes resend every field, so only a complete answer (child lock
            # byte included) makes them safe.
            if cmd == 211 and isinstance(data, dict) and data.get("is_locked") is not None:
                self.device.config_received = True
                
        # Previously: Device status forwarded to MQTT when cmd in [220, 221, 230]
        
        return parsed_data
