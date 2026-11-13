# PetkitW5BLE/utils.py
import time
import math
from datetime import datetime, timezone

class Utils:

    @staticmethod
    def build_command(seq: int, cmd: int, type: int, data: list[int]) -> bytearray:
        header = [250, 252, 253] # In hex our headers are: FA FC FD
        end_byte = [251] # In hex the end byte is FB
        length = len(data)
        start_data = 0
        command = header + [cmd, type, seq, length, start_data] + data + end_byte
        return bytearray(command)

    @staticmethod
    def parse_bytearray(byte_array: bytearray):
        """Parse a notification frame, or return None if it is malformed.

        Frame layout: FA FC FD | cmd | type | seq | len | 0 | data[len] | FB
        """
        if (
            not byte_array
            or len(byte_array) < 9
            or bytes(byte_array[:3]) != b"\xfa\xfc\xfd"
            or byte_array[-1] != 0xFB
            or len(byte_array) != 9 + byte_array[6]
        ):
            return None
        return {
            "header": byte_array[:3],
            "cmd": byte_array[3],
            "type": byte_array[4],
            "seq": byte_array[5],
            "data_length": byte_array[6],
            "data_start": byte_array[7],
            "data": byte_array[8:-1],
            "end_byte": byte_array[-1]
        }
        
    # Byte manipulation

    @staticmethod
    def byte_to_integer(byte):
        return byte & 0xFF

    @staticmethod
    def bytes_to_integer(bytes, byteorder='big'):
        return int.from_bytes(bytes, byteorder=byteorder)

    @staticmethod
    def bytes_to_short(bytes, byteorder='big'):
        return int.from_bytes(bytes, byteorder=byteorder, signed=True)

    @staticmethod
    def bytes_to_unsigned_integers(byte_array):
        return [b for b in byte_array]
        
    @staticmethod
    def combine_byte_arrays(byte_arrays_dict: dict) -> bytearray:
        combined_array = bytearray()
        for byte_array in byte_arrays_dict.values():
            combined_array.extend(byte_array)
        return combined_array
    
    @staticmethod
    def pad_array(data, target_length):
        return [0] * (target_length - len(data)) + data
    
    @staticmethod
    def reverse_unsigned_array(array):
        # Ensure input is a list of integers
        if not all(isinstance(x, int) for x in array):
            raise ValueError("All elements in the array must be integers")
        return array[::-1]
    
    @staticmethod
    def replace_last_two_if_zero(array):
        if len(array) >= 2 and array[-1] == 0 and array[-2] == 0:
            array[-2] = 13
            array[-1] = 37
        return array
    
    # Time functions
    @staticmethod
    def get_seconds():
        """Seconds since 2000-01-01 UTC, the epoch of the device clock."""
        reference_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
        return int((datetime.now(timezone.utc) - reference_time).total_seconds())

    @staticmethod
    def get_seconds_without_timezone():
        date_format = "%Y-%m-%dT%H:%M:%S.%f%z"
        now = datetime.now(timezone.utc).strftime(date_format)
        reference_time = datetime.strptime("2000-01-01T00:00:00.000+0000", date_format)
        current_time = datetime.strptime(now, date_format)
        seconds = int((current_time - reference_time).total_seconds())
        return seconds

    @staticmethod
    def get_timezone_offset():
        offset = datetime.now().astimezone().utcoffset()
        return int(offset.total_seconds() / 3600.0) + 12

    @staticmethod
    def time_in_bytes():
        datetime_object = [0, 0, 0, 0, 0, 13]
        seconds = Utils.get_seconds()
        
        # Might become relevant later
        #if ble_device_type == 4:
        #    seconds = get_seconds_without_timezone()
        #    datetime_object[0] = (get_timezone_offset() & 255)
        
        datetime_object[1] = (seconds >> 24) & 255
        datetime_object[2] = (seconds >> 16) & 255
        datetime_object[3] = (seconds >> 8) & 255
        datetime_object[4] = (seconds) & 255

        return datetime_object

    @staticmethod
    def get_timestamp_days(timestamp):
        # Elapsed time, not a calendar date: strftime's day of the month would
        # be a day ahead and start over every month.
        days, remainder = divmod(int(timestamp), 86400)
        return f"{days} days, {remainder // 3600} hours"
        
    @staticmethod
    def get_timestamp_hours(timestamp):
        return time.strftime("%-H:%-Mh", time.gmtime(timestamp))
    
    @staticmethod
    def minutes_to_timestamp(minutes): 
        hours = minutes // 60 
        minutes = minutes % 60 
        return f"{hours:02d}:{minutes:02d}"
        
    # Extract data functions

    @staticmethod
    def extract_device_id(data):
        bytes = data[2:8]  # Extract first 8 bytes for device ID
        device_id = int.from_bytes(bytes, byteorder='big')
        return Utils.bytes_to_unsigned_integers(bytes), device_id

    @staticmethod
    def extract_serial_number(data):
        bytes = data[8:23]  # Extract next 14 bytes for serial number
        serial_number = ''.join(chr(b) for b in bytes)
        return serial_number

    # canculateWxEnergyForType
    # device.alias, device.type_code, 
    @staticmethod
    def calculate_energy_usage(alias, pump_runtime_today):
        f = 0
        f2 = 2.0

        if alias == "W5C":
            f = 0.182
        else:
            f = 0.75 * pump_runtime_today
        
        f2 = 3600000
        
        return f / f2    

    # device.alias, device.type_code,
    @staticmethod
    def calculate_water_purified(alias, pump_runtime_today):
        # Keep this estimate stable even after the BLE alias is discovered.
        # Device-specific multipliers caused a one-time history jump in HA.
        water_liters = ((1.5 * pump_runtime_today) / 60.0) / 2.0
        return round(water_liters, 2)
    
    # canculateWxFilterLeftDays
    # if device.mode = 1 (normal)
    # device.filter_percentage, 1, 0
    # if device.mode = 2 (smart)
    # device.filter_percentage, device.smart_time_on, device.smart_time_off
    @staticmethod
    def calculate_remaining_filter_time(filter_percentage, time_on, time_off):
        if time_on == 0:
            return math.ceil(filter_percentage * 60)
        return math.ceil(((filter_percentage * 30.0) * (time_on + time_off)) / time_on)
    
    @staticmethod
    def calculate_values(mode, filter_percentage, smart_time_on, smart_time_off, alias, pump_runtime_today, pump_runtime):
        time_on = smart_time_on
        time_off = smart_time_off
        
        if mode == 1:
            time_on = 1
            time_off = 0
    
        filter_time_left = Utils.calculate_remaining_filter_time(filter_percentage, time_on, time_off)
        water_purified_today = Utils.calculate_water_purified(alias, pump_runtime_today)
        water_purified = Utils.calculate_water_purified(alias, pump_runtime)
        energy_consumed = format(Utils.calculate_energy_usage(alias, pump_runtime), 'f')
    
        return filter_time_left, water_purified, water_purified_today, energy_consumed
    
    @staticmethod
    def get_device_properties(device_integer_identifier, advertised_name=None):
        device_mapping = {
            205: {"name": "Petkit_W5C", "alias": "W5C", "product_name": "Eversweet Mini", "device_type": 14, "type_code": 2},
            206: {"name": "Petkit_W5", "alias": "W5", "product_name": "Eversweet Mini", "device_type": 14, "type_code": 1},
            213: {"name": "Petkit_W5N", "alias": "W5N", "product_name": "Eversweet Mini", "device_type": 14, "type_code": 3},
            214: {"name": "Petkit_W4X", "alias": "W4X", "product_name": "Eversweet 3 Pro", "device_type": 14, "type_code": 4},
            217: {"name": "Petkit_CTW2", "alias": "CTW2", "product_name": "Eversweet Solo 2", "device_type": 14, "type_code": 5},
            223: {"name": "Petkit_CTW3", "alias": "W5", "product_name": "Eversweet Max", "device_type": 24, "type_code": 1},
            228: {"name": "Petkit_W4XUVC", "alias": "W4X", "product_name": "Eversweet 3 Pro (UVC)", "device_type": 14, "type_code": 6},
            246: {"name": "Petkit_CTW3_2", "alias": "W5", "product_name": "Eversweet Max", "device_type": 24, "type_code": 1},
            247: {"name": "Petkit_CTW3_100", "alias": "W5C", "product_name": "Eversweet Max 2", "device_type": 24, "type_code": 2},
            248: {"name": "Petkit_CTW3UV", "alias": "W5N", "product_name": "Eversweet Max", "device_type": 24, "type_code": 3},
            249: {"name": "Petkit_CTW3UV_100", "alias": "W4X", "product_name": "Eversweet Max 2", "device_type": 24, "type_code": 4}
        }

        if device_integer_identifier in device_mapping:
            return device_mapping[device_integer_identifier]

        # No usable model byte: the fountain advertises its model as its name
        # too (Petkit_CTW3_100, ...), so try that before giving up.
        for properties in device_mapping.values():
            if properties["name"] == advertised_name:
                return properties

        # Unknown model. Keeping the advertised name keeps a CTW3 recognisable
        # by it, so it is not parsed with the W4/W5 layout.
        return {
            "name": advertised_name or f"Petkit_Unknown_{device_integer_identifier}",
            "alias": "Unknown",
            "product_name": "Petkit BLE Water Fountain",
            "device_type": 0,
            "type_code": 0,
        }
        
