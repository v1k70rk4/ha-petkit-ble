from .utils import Utils

class Parsers:
    @staticmethod
    def _device_alias(device_context):
        if isinstance(device_context, dict):
            return device_context.get("alias") or "Uninitialized"
        return device_context

    @staticmethod
    def _is_ctw3(device_context):
        if isinstance(device_context, dict):
            name = device_context.get("name") or ""
            return device_context.get("device_type") == 24 or "CTW3" in name
        return device_context == "CTW3"

    # Get Battery Synchronization
    @staticmethod
    def device_battery(data, alias):
        return {
            "voltage": ((data[0] * 16 * 16) + (data[1] & 255)) / 1000.0,  # Magic borrowed from Petkit
            "battery": data[2]
        }

    # Init data
    @staticmethod
    def device_init(data, alias):
        return {"serial": Utils.bytes_to_long(data[7:11])}

    # Synchronize data
    @staticmethod
    def device_synchronization(data, alias):
        return {"device_initialized": data[0]}

    # Device Information
    @staticmethod
    def device_firmware(data, alias):
        # According to the com.petkit.oversea app, the Hardware is actually [0] while firmware is [1]
        # they are however presented as v[0].[1] in the actual app
        firmware = float(f"{data[0]}.{data[1]}")
        return {"firmware": firmware }

    # Get device state
    @staticmethod
    def device_state(data, alias):
        if Parsers._is_ctw3(alias):
            if len(data) < 26:
                return {"error": "Insufficient data length for CTW3"}
            return {
                "power_status": data[0],
                "suspend_status": data[1],
                "mode": data[2],
                "electric_status": data[3],
                "dnd_state": data[4],
                "warning_breakdown": data[5],
                "warning_water_missing": data[6],
                "low_battery": data[7],
                "warning_filter": data[8],
                "pump_runtime": Utils.bytes_to_integer(data[9:13]),
                "filter_percentage": data[13],
                "running_status": data[14],
                "pump_runtime_today": Utils.bytes_to_integer(data[15:19]),
                "detect_status": data[19],
                "supply_voltage": Utils.bytes_to_short(data[20:22]),
                "battery_voltage": Utils.bytes_to_short(data[22:24]),
                "battery_percentage": data[24],
                "module_status": data[25],
            }
        else:
            if len(data) < 12:
                return {"error": "Insufficient data length for device state"}
            return {
                "power_status": data[0],
                "mode": data[1],
                "dnd_state": data[2],
                "warning_breakdown": data[3],
                "warning_water_missing": data[4],
                "warning_filter": data[5],
                "pump_runtime": Utils.bytes_to_integer(data[6:10]),
                "filter_percentage": Utils.byte_to_integer(data[10]) / 100,
                "running_status": Utils.byte_to_integer(data[11]),
            }

    # Get device configuration
    @staticmethod
    def device_configuration(data, alias):
        if Parsers._is_ctw3(alias):
            if len(data) < 9:
                return {"error": "Insufficient data length for CTW3 configuration"}
            battery_working_time = Utils.bytes_to_short(data[2:4])
            battery_sleep_time = Utils.bytes_to_short(data[4:6])

            is_locked = 0
            if len(data) > 9:
                is_locked = data[9]

            return {
                "smart_time_on": data[0],
                "smart_time_off": data[1],
                "battery_working_time": battery_working_time,
                "battery_time_on": Utils.minutes_to_timestamp(battery_working_time),
                "battery_sleep_time": battery_sleep_time,
                "battery_time_off": Utils.minutes_to_timestamp(battery_sleep_time),
                "led_switch": data[6],
                "led_brightness": data[7],
                "do_not_disturb_switch": data[8],
                "is_locked": is_locked,
            }
        else:
            if len(data) < 13:
                return {"error": "Insufficient data length for device configuration"}
            led_light_time_on = Utils.bytes_to_short(data[4:6])
            led_light_time_off = Utils.bytes_to_short(data[6:8])
            do_not_disturb_time_on = Utils.bytes_to_short(data[9:11])
            do_not_disturb_time_off = Utils.bytes_to_short(data[11:13])

            return {
                "smart_time_on": data[0],
                "smart_time_off": data[1],
                "led_switch": data[2],
                "led_brightness": data[3],
                "led_light_time_on": led_light_time_on,
                "led_light_time_on_readable": Utils.minutes_to_timestamp(led_light_time_on),
                "led_on_byte1": data[4],
                "led_on_byte2": data[5],
                "led_light_time_off": led_light_time_off,
                "led_light_time_off_readable": Utils.minutes_to_timestamp(led_light_time_off),
                "led_off_byte1": data[6],
                "led_off_byte2": data[7],
                "do_not_disturb_switch": data[8],
                "do_not_disturb_time_on": do_not_disturb_time_on,
                "do_not_disturb_time_on_readable": Utils.minutes_to_timestamp(do_not_disturb_time_on),
                "dnd_on_byte1": data[9],
                "dnd_on_byte2": data[10],
                "do_not_disturb_time_off": do_not_disturb_time_off,
                "do_not_disturb_time_off_readable": Utils.minutes_to_timestamp(do_not_disturb_time_off),
                "dnd_off_byte1": data[11],
                "dnd_off_byte2": data[12],
                "is_locked": data[13] if len(data) > 13 else None,
            }

    # Get device ID and serial
    @staticmethod
    def device_identifiers(data, alias):
        device_id_bytes, device_id = Utils.extract_device_id(data)
        serial = Utils.extract_serial_number(data)

        return {
            "device_id": device_id,
            "device_id_bytes": device_id_bytes,
            "serial": serial,
        }

    # Status
    @staticmethod
    def device_status(data, alias):
        device_alias = Parsers._device_alias(alias)
        if Parsers._is_ctw3(alias):
            if len(data) < 26:
                return {"error": "Insufficient data length for CTW3 status"}
            mode = data[2]
            filter_percentage = data[13] / 100.0
            pump_runtime = Utils.bytes_to_integer(data[9:13])
            pump_runtime_today = Utils.bytes_to_integer(data[15:19])

            smart_time_on = data[26] if len(data) > 26 else 0
            smart_time_off = data[27] if len(data) > 27 else 0
            led_switch = data[28] if len(data) > 28 else None
            led_brightness = data[29] if len(data) > 29 else None
            dnd_switch = data[34] if len(data) > 34 else None

            filter_time_left, purified_water, purified_water_today, energy_consumed = Utils.calculate_values(
                mode, filter_percentage, smart_time_on, smart_time_off, device_alias, pump_runtime_today, pump_runtime
            )

            return {
                "power_status": data[0],
                "suspend_status": data[1],
                "mode": mode,
                "electric_status": data[3],
                "dnd_state": data[4],
                "warning_breakdown": data[5],
                "warning_water_missing": data[6],
                "low_battery": data[7],
                "warning_filter": data[8],
                "pump_runtime": pump_runtime,
                "filter_percentage": filter_percentage,
                "running_status": data[14],
                "pump_runtime_today": pump_runtime_today,
                "detect_status": data[19],
                "supply_voltage": Utils.bytes_to_short(data[20:22]),
                "battery_voltage": Utils.bytes_to_short(data[22:24]),
                "battery_percentage": data[24],
                "module_status": data[25],
                "smart_time_on": smart_time_on,
                "smart_time_off": smart_time_off,
                "led_switch": led_switch,
                "led_brightness": led_brightness,
                "do_not_disturb_switch": dnd_switch,
                "pump_runtime_readable": Utils.get_timestamp_days(pump_runtime),
                "pump_runtime_today_readable": Utils.get_timestamp_hours(pump_runtime_today),
                "filter_time_left": filter_time_left,
                "purified_water": purified_water,
                "purified_water_today": purified_water_today,
                "energy_consumed": energy_consumed,
            }

        if len(data) < 29:
            return {"error": "Insufficient data length for device status"}
        mode = data[1]
        filter_percentage = Utils.byte_to_integer(data[10]) / 100
        smart_time_on = data[16]
        smart_time_off = data[17]
        pump_runtime_today = Utils.bytes_to_integer(data[12:16])
        pump_runtime = Utils.bytes_to_integer(data[6:10])

        filter_time_left, purified_water, purified_water_today, energy_consumed = Utils.calculate_values(mode, filter_percentage, smart_time_on, smart_time_off, device_alias, pump_runtime_today, pump_runtime)

        led_light_time_on = Utils.bytes_to_short(data[20:22])
        led_light_time_off = Utils.bytes_to_short(data[22:24])
        do_not_disturb_time_on = Utils.bytes_to_short(data[25:27])
        do_not_disturb_time_off = Utils.bytes_to_short(data[27:29])

        return {
            "power_status": data[0],
            "mode": mode,
            "dnd_state": data[2],
            "warning_breakdown": data[3],
            "warning_water_missing": data[4],
            "warning_filter": data[5],
            "pump_runtime": pump_runtime,
            "filter_percentage": filter_percentage,
            "running_status": Utils.byte_to_integer(data[11]),
            "pump_runtime_today": pump_runtime_today,
            "smart_time_on": smart_time_on,
            "smart_time_off": smart_time_off,
            "led_switch": data[18],
            "led_brightness": data[19],
            "led_light_time_on": led_light_time_on,
            "led_light_time_on_readable": Utils.minutes_to_timestamp(led_light_time_on),
            "led_on_byte1": data[20],
            "led_on_byte2": data[21],
            "led_light_time_off": led_light_time_off,
            "led_light_time_off_readable": Utils.minutes_to_timestamp(led_light_time_off),
            "led_off_byte1": data[22],
            "led_off_byte2": data[23],
            "do_not_disturb_switch": data[24],
            "do_not_disturb_time_on": do_not_disturb_time_on,
            "do_not_disturb_time_on_readable": Utils.minutes_to_timestamp(do_not_disturb_time_on),
            "dnd_on_byte1": data[25],
            "dnd_on_byte2": data[26],
            "do_not_disturb_time_off": do_not_disturb_time_off,
            "do_not_disturb_time_off_readable": Utils.minutes_to_timestamp(do_not_disturb_time_off),
            "dnd_off_byte1": data[27],
            "dnd_off_byte2": data[28],
            "pump_runtime_readable": Utils.get_timestamp_days(pump_runtime),
            "pump_runtime_today_readable": Utils.get_timestamp_hours(pump_runtime_today),
            "filter_time_left": filter_time_left,
            "purified_water": purified_water,
            "purified_water_today": purified_water_today,
            "energy_consumed": energy_consumed,
        }
