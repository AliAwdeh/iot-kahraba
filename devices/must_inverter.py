import time

import minimalmodbus
import serial

import core.state as app_state
from core.time_utils import now_iso
from mqtt.publisher import publish, publish_error


class MustInverterReader:
    """
    MUST inverter Modbus RTU reader.

    This version uses block reads instead of reading registers one by one.

    It reads:
        PV / solar charger block:
            Base address: 15200
            Count: 22 registers

        Inverter / grid / load block:
            Base address: 25200
            Count: 79 registers
    """

    PV_BASE_ADDRESS = 15200
    PV_REGISTER_COUNT = 22

    INVERTER_BASE_ADDRESS = 25200
    INVERTER_REGISTER_COUNT = 79

    WORK_STATE_MAP = {
        0: "PowerOn",
        1: "SelfTest",
        2: "OffGrid",
        3: "Grid-Tie",
        4: "Bypass",
        5: "Stop",
        6: "Grid Charging",
    }

    DEVICE_FIELDS = {
        "device_type",
        "device_id",
        "name",
        "role",
        "port",
        "slave_address",
        "baudrate",
        "timestamp",
        "status",
        "errors",
        "published_at",
    }

    PV_SOLAR_FIELDS = {
        "pv_voltage",
        "pv_current",
        "pv_power",
        "panel_voltage",
        "panel_power",
        "charger_current",
        "pv_charger_temperature",
        "external_temperature",
        "pv_relay",
        "mppt_state",
        "charging_state",
        "charger_work_state",
        "pv_error_message",
        "pv_warning_message",
        "accumulated_pv_power",
        "accumulated_runtime",
        "rated_charger_current",
        "pv_data",
    }

    def __init__(self, config, site_id):
        self.site_id = site_id

        self.enabled = config.get("enabled", False)
        self.interval = int(config.get("interval_seconds", 10))
        self.block_delay = float(config.get("block_delay_seconds", 0.2))

        self.low_battery_voltage = float(config.get("low_battery_voltage", 45.0))
        self.overcharge_battery_voltage = float(
            config.get("overcharge_battery_voltage", 58.0)
        )
        self.pv_high_temperature = float(config.get("pv_high_temperature", 70))
        self.inverter_high_temperature = float(config.get("inverter_high_temperature", 80))

        self.inverters = []
        self.latest_main_payload = None

        if not self.enabled:
            print("[INVERTERS] Disabled")
            return

        for item in config.get("items", []):
            inverter = {
                "id": item["id"],
                "name": item.get("name", item["id"]),
                "role": item.get("role", "unknown"),
                "port": item["port"],
                "slave_address": int(item.get("slave_address", 4)),
                "baudrate": int(item.get("baudrate", 19200)),
                "instrument": None,
                "load_error": None
            }

            try:
                inverter["instrument"] = self.create_instrument(
                    port=inverter["port"],
                    slave_id=inverter["slave_address"],
                    baudrate=inverter["baudrate"]
                )

                print(
                    f"[INVERTER] Loaded {item['id']} "
                    f"({item.get('name', item['id'])}) on {item['port']} "
                    f"slave={item.get('slave_address', 4)} "
                    f"baud={item.get('baudrate', 19200)}"
                )

            except Exception as e:
                inverter["load_error"] = str(e)
                print(
                    f"[INVERTER] {item['id']} unavailable on {item['port']}: {e}"
                )

            self.inverters.append(inverter)

    def create_instrument(self, port, slave_id, baudrate):
        instrument = minimalmodbus.Instrument(port, slave_id)

        instrument.serial.baudrate = baudrate
        instrument.serial.bytesize = 8
        instrument.serial.parity = serial.PARITY_NONE
        instrument.serial.stopbits = 1
        instrument.serial.timeout = 1.5

        instrument.mode = minimalmodbus.MODE_RTU
        instrument.clear_buffers_before_each_transaction = True
        instrument.close_port_after_each_call = False

        return instrument

    def is_main_inverter(self, inverter_or_payload):
        return (
            inverter_or_payload.get("role") == "main"
            or inverter_or_payload.get("id") == "must_1"
            or inverter_or_payload.get("device_id") == "must_1"
            or inverter_or_payload.get("port") == "/dev/ttyUSB0"
        )

    def signed_16(self, value):
        value = int(value)

        if value & 0x8000:
            value -= 0x10000

        return value

    def combine_energy(self, high, low):
        return round(
            self.signed_16(high) * 1000 + self.signed_16(low) * 0.1,
            2
        )

    def decode_pv_charger_block(self, raw):
        if raw is None:
            raise ValueError("No PV registers received")

        if len(raw) < 22:
            raise ValueError(f"Expected 22 PV registers, got {len(raw)}")

        return {
            "charger_work_state": self.signed_16(raw[1]),
            "mppt_state": self.signed_16(raw[2]),
            "charging_state": self.signed_16(raw[3]),

            "panel_voltage": round(self.signed_16(raw[5]) * 0.1, 2),
            "battery_voltage": round(self.signed_16(raw[6]) * 0.1, 2),
            "charger_current": round(self.signed_16(raw[7]) * 0.1, 2),
            "panel_power": self.signed_16(raw[8]),

            "pv_charger_temperature": self.signed_16(raw[9]),
            "external_temperature": self.signed_16(raw[10]),

            "battery_relay": bool(self.signed_16(raw[11])),
            "pv_relay": bool(self.signed_16(raw[12])),

            "pv_error_message": self.signed_16(raw[13]),
            "pv_warning_message": self.signed_16(raw[14]),

            "battery_voltage_grade": self.signed_16(raw[15]),
            "rated_charger_current": round(self.signed_16(raw[16]) * 0.1, 2),

            "accumulated_pv_power": self.combine_energy(raw[17], raw[18]),
            "accumulated_day": self.signed_16(raw[19]),
            "accumulated_hour": self.signed_16(raw[20]),
            "accumulated_minute": self.signed_16(raw[21]),

            "raw_pv_registers": raw
        }

    def decode_inverter_block(self, raw):
        if raw is None:
            raise ValueError("No inverter registers received")

        if len(raw) < 79:
            raise ValueError(f"Expected 79 inverter registers, got {len(raw)}")

        inverter_work_state_code = self.signed_16(raw[0])

        return {
            "inverter_work_state": inverter_work_state_code,
            "inverter_work_state_text": self.WORK_STATE_MAP.get(
                inverter_work_state_code,
                f"Unknown({inverter_work_state_code})"
            ),

            "ac_voltage_grade": self.signed_16(raw[1]),

            "inverter_battery_voltage": round(self.signed_16(raw[5]) * 0.1, 2),
            "inverter_voltage": round(self.signed_16(raw[6]) * 0.1, 2),
            "grid_voltage": round(self.signed_16(raw[7]) * 0.1, 2),
            "bus_voltage": round(self.signed_16(raw[8]) * 0.1, 2),

            "control_current": round(self.signed_16(raw[9]) * 0.1, 2),
            "inverter_current": round(self.signed_16(raw[10]) * 0.1, 2),
            "load_current": round(self.signed_16(raw[11]) * 0.1, 2),

            "inverter_power_candidate": self.signed_16(raw[13]),
            "grid_power_candidate": self.signed_16(raw[14]),

            "load_power": self.signed_16(raw[15]),
            "load_percent": self.signed_16(raw[16]),

            "apparent_power_1": self.signed_16(raw[17]),
            "apparent_load_power": self.signed_16(raw[18]),
            "apparent_power_3": self.signed_16(raw[19]),

            "reactive_power_1": self.signed_16(raw[21]),
            "reactive_power_2": self.signed_16(raw[22]),
            "reactive_power_3": self.signed_16(raw[23]),

            "inverter_frequency": round(self.signed_16(raw[25]) * 0.01, 2),
            "grid_frequency": round(self.signed_16(raw[26]) * 0.01, 2),

            "transformer_temperature": self.signed_16(raw[33]),
            "dc_radiator_temperature": self.signed_16(raw[34]),
            "internal_temperature": self.signed_16(raw[35]),

            "grid_relay": bool(self.signed_16(raw[37])),
            "load_relay": bool(self.signed_16(raw[38])),
            "n_line_relay": bool(self.signed_16(raw[39])),
            "earth_relay": bool(self.signed_16(raw[41])),

            "accumulated_charger_power_25200": self.combine_energy(raw[44], raw[45]),
            "accumulated_discharger_power": self.combine_energy(raw[46], raw[47]),
            "accumulated_buy_power": self.combine_energy(raw[48], raw[49]),
            "accumulated_sell_power": self.combine_energy(raw[50], raw[51]),
            "accumulated_load_power": self.combine_energy(raw[52], raw[53]),
            "accumulated_self_use_power": self.combine_energy(raw[54], raw[55]),
            "accumulated_pv_sell_power": self.combine_energy(raw[56], raw[57]),
            "accumulated_grid_charger_power": self.combine_energy(raw[58], raw[59]),

            "inverter_error_message_1": self.signed_16(raw[60]),
            "inverter_error_message_2": self.signed_16(raw[61]),
            "inverter_error_message_3": self.signed_16(raw[62]),

            "inverter_warning_message_1": self.signed_16(raw[64]),
            "inverter_warning_message_2": self.signed_16(raw[65]),

            "software_version_raw": self.signed_16(raw[71]),
            "rated_power_w": self.signed_16(raw[77]),
            "arrow_flag": self.signed_16(raw[78]),

            "raw_inverter_registers": raw
        }

    def calculate_system_status(self, data):
        """
        Project-level status logic.

        Battery voltage statuses are only checked from the main inverter:
            must_1 / role=main / USB0

        The water inverter:
            must_2 / role=water / USB1

        should not trigger battery voltage statuses for the whole system.
        """

        role = data.get("role")
        device_id = data.get("device_id")
        main_data_source = data.get("main_data_source")
        main_data_device_id = (
            main_data_source.get("device_id")
            if isinstance(main_data_source, dict)
            else None
        )

        is_main_inverter = (
            role == "main"
            or device_id == "must_1"
            or main_data_device_id == "must_1"
        )

        if data.get("pv_error_message", 0) not in [0, None]:
            return "PV_ERROR"

        if data.get("pv_warning_message", 0) not in [0, None]:
            return "PV_WARNING"

        battery_voltage = None
        inverter_data = data.get("inverter_data")

        if isinstance(inverter_data, dict):
            battery_voltage = inverter_data.get("inverter_battery_voltage")

        if battery_voltage is None:
            battery_voltage = data.get("battery_voltage")

        if (
            is_main_inverter
            and battery_voltage is not None
            and battery_voltage > self.overcharge_battery_voltage
        ):
            return "OVER_CHARGING"

        if (
            is_main_inverter
            and battery_voltage is not None
            and battery_voltage < self.low_battery_voltage
        ):
            return "LOW_BATTERY"

        pv_temp = data.get("pv_charger_temperature")

        if (
            pv_temp is not None
            and pv_temp > self.pv_high_temperature
        ):
            return "PV_HIGH_TEMPERATURE"

        inverter_temp = data.get("dc_radiator_temperature")

        if (
            inverter_temp is not None
            and inverter_temp > self.inverter_high_temperature
        ):
            return "INVERTER_HIGH_TEMPERATURE"

        return "NORMAL"

    def use_main_data_for_non_solar_fields(self, payload):
        """
        Fallback for auxiliary inverters when their own data is unavailable.
        PV / solar values stay empty; main system values come from USB0.
        """
        if self.latest_main_payload is None:
            for key in list(payload.keys()):
                if key in self.DEVICE_FIELDS or key in self.PV_SOLAR_FIELDS:
                    continue

                payload[key] = None

            payload["main_data_source"] = "unavailable"
            return payload

        for key, value in self.latest_main_payload.items():
            if key in self.DEVICE_FIELDS or key in self.PV_SOLAR_FIELDS:
                continue

            payload[key] = value

        payload["main_data_source"] = {
            "device_id": self.latest_main_payload.get("device_id"),
            "port": self.latest_main_payload.get("port"),
        }

        return payload

    def read_raw_blocks(self, instrument):
        errors = {}

        pv_registers = None
        inverter_registers = None

        try:
            pv_registers = instrument.read_registers(
                registeraddress=self.PV_BASE_ADDRESS,
                number_of_registers=self.PV_REGISTER_COUNT,
                functioncode=3
            )

        except Exception as e:
            errors["pv_block_15200"] = str(e)

        time.sleep(self.block_delay)

        try:
            inverter_registers = instrument.read_registers(
                registeraddress=self.INVERTER_BASE_ADDRESS,
                number_of_registers=self.INVERTER_REGISTER_COUNT,
                functioncode=3
            )

        except Exception as e:
            errors["inverter_block_25200"] = str(e)

        return pv_registers, inverter_registers, errors

    def build_empty_payload(self, inverter, status, errors):
        return {
            "device_type": "must_inverter",
            "device_id": inverter["id"],
            "name": inverter["name"],
            "role": inverter["role"],
            "port": inverter["port"],
            "slave_address": inverter["slave_address"],
            "baudrate": inverter["baudrate"],

            "timestamp": now_iso(),
            "status": status,
            "errors": errors,

            "grid_voltage": None,
            "grid_frequency": None,
            "ac_output_voltage": None,
            "ac_output_frequency": None,
            "load_percent": None,
            "battery_voltage": None,
            "battery_capacity_percent": None,
            "inverter_temperature_c": None,
            "pv_voltage": None,
            "pv_current": None,
            "pv_power": None,

            "panel_voltage": None,
            "panel_power": None,
            "charger_current": None,

            "inverter_voltage": None,
            "bus_voltage": None,
            "control_current": None,
            "inverter_current": None,
            "load_current": None,
            "load_power": None,

            "inverter_power_candidate": None,
            "grid_power_candidate": None,
            "apparent_power_1": None,
            "apparent_load_power": None,
            "apparent_power_3": None,
            "reactive_power_1": None,
            "reactive_power_2": None,
            "reactive_power_3": None,

            "inverter_frequency": None,

            "pv_charger_temperature": None,
            "external_temperature": None,
            "transformer_temperature": None,
            "dc_radiator_temperature": None,
            "internal_temperature": None,

            "pv_relay": None,
            "battery_relay": None,
            "grid_relay": None,
            "load_relay": None,
            "n_line_relay": None,
            "earth_relay": None,

            "mppt_state": None,
            "charging_state": None,
            "charger_work_state": None,
            "inverter_work_state": None,
            "inverter_work_state_text": None,
            "work_state": None,

            "pv_error_message": None,
            "pv_warning_message": None,
            "inverter_error_message_1": None,
            "inverter_error_message_2": None,
            "inverter_error_message_3": None,
            "inverter_warning_message_1": None,
            "inverter_warning_message_2": None,

            "accumulated_pv_power": None,
            "accumulated_runtime": None,
            "rated_charger_current": None,
            "rated_power_w": None,

            "system_status": "UNKNOWN",

            "pv_data": None,
            "inverter_data": None
        }

    def read_inverter(self, inverter):
        instrument = inverter["instrument"]

        if instrument is None:
            payload = self.build_empty_payload(
                inverter=inverter,
                status="not_detected",
                errors={"instrument": inverter.get("load_error") or "not detected"}
            )
            payload["system_status"] = "NOT_DETECTED"

            if not self.is_main_inverter(inverter):
                payload = self.use_main_data_for_non_solar_fields(payload)
                payload["system_status"] = self.calculate_system_status(payload)

            return payload

        pv_registers, inverter_registers, errors = self.read_raw_blocks(instrument)

        pv_data = None
        inverter_data = None

        try:
            if pv_registers is not None:
                pv_data = self.decode_pv_charger_block(pv_registers)

        except Exception as e:
            errors["pv_decode"] = str(e)

        try:
            if inverter_registers is not None:
                inverter_data = self.decode_inverter_block(inverter_registers)

        except Exception as e:
            errors["inverter_decode"] = str(e)

        if pv_data is None and inverter_data is None:
            payload = self.build_empty_payload(
                inverter=inverter,
                status="read_failed",
                errors=errors
            )
            payload["system_status"] = "READ_FAILED"

            if not self.is_main_inverter(inverter):
                payload = self.use_main_data_for_non_solar_fields(payload)
                payload["system_status"] = self.calculate_system_status(payload)

            return payload

        status = "ok" if not errors else "partial_error"

        payload = self.build_empty_payload(
            inverter=inverter,
            status=status,
            errors=errors
        )

        payload["pv_data"] = pv_data
        payload["inverter_data"] = inverter_data

        if pv_data:
            payload.update({
                "battery_voltage": pv_data["battery_voltage"],
                "panel_voltage": pv_data["panel_voltage"],
                "panel_power": pv_data["panel_power"],
                "charger_current": pv_data["charger_current"],

                "pv_voltage": pv_data["panel_voltage"],
                "pv_current": pv_data["charger_current"],
                "pv_power": pv_data["panel_power"],

                "pv_charger_temperature": pv_data["pv_charger_temperature"],
                "external_temperature": pv_data["external_temperature"],

                "pv_relay": pv_data["pv_relay"],
                "battery_relay": pv_data["battery_relay"],

                "mppt_state": pv_data["mppt_state"],
                "charging_state": pv_data["charging_state"],
                "charger_work_state": pv_data["charger_work_state"],

                "pv_error_message": pv_data["pv_error_message"],
                "pv_warning_message": pv_data["pv_warning_message"],

                "accumulated_pv_power": pv_data["accumulated_pv_power"],
                "accumulated_runtime": {
                    "days": pv_data["accumulated_day"],
                    "hours": pv_data["accumulated_hour"],
                    "minutes": pv_data["accumulated_minute"]
                },

                "rated_charger_current": pv_data["rated_charger_current"]
            })

        if inverter_data:
            payload.update({
                "battery_voltage": inverter_data["inverter_battery_voltage"],
                "inverter_voltage": inverter_data["inverter_voltage"],
                "grid_voltage": inverter_data["grid_voltage"],
                "bus_voltage": inverter_data["bus_voltage"],

                "control_current": inverter_data["control_current"],
                "inverter_current": inverter_data["inverter_current"],
                "load_current": inverter_data["load_current"],

                "load_power": inverter_data["load_power"],
                "load_percent": inverter_data["load_percent"],

                "inverter_power_candidate": inverter_data["inverter_power_candidate"],
                "grid_power_candidate": inverter_data["grid_power_candidate"],
                "apparent_power_1": inverter_data["apparent_power_1"],
                "apparent_load_power": inverter_data["apparent_load_power"],
                "apparent_power_3": inverter_data["apparent_power_3"],
                "reactive_power_1": inverter_data["reactive_power_1"],
                "reactive_power_2": inverter_data["reactive_power_2"],
                "reactive_power_3": inverter_data["reactive_power_3"],

                "inverter_frequency": inverter_data["inverter_frequency"],
                "grid_frequency": inverter_data["grid_frequency"],

                "transformer_temperature": inverter_data["transformer_temperature"],
                "dc_radiator_temperature": inverter_data["dc_radiator_temperature"],
                "internal_temperature": inverter_data["internal_temperature"],

                "grid_relay": inverter_data["grid_relay"],
                "load_relay": inverter_data["load_relay"],
                "n_line_relay": inverter_data["n_line_relay"],
                "earth_relay": inverter_data["earth_relay"],

                "inverter_work_state": inverter_data["inverter_work_state"],
                "inverter_work_state_text": inverter_data["inverter_work_state_text"],
                "work_state": inverter_data["inverter_work_state_text"],

                "inverter_error_message_1": inverter_data["inverter_error_message_1"],
                "inverter_error_message_2": inverter_data["inverter_error_message_2"],
                "inverter_error_message_3": inverter_data["inverter_error_message_3"],
                "inverter_warning_message_1": inverter_data["inverter_warning_message_1"],
                "inverter_warning_message_2": inverter_data["inverter_warning_message_2"],

                "rated_power_w": inverter_data["rated_power_w"],

                "ac_output_voltage": inverter_data["inverter_voltage"],
                "ac_output_frequency": inverter_data["inverter_frequency"],

                "inverter_temperature_c": inverter_data["dc_radiator_temperature"],

                "active_power_w": inverter_data["inverter_power_candidate"],
                "grid_power_w": inverter_data["grid_power_candidate"],
                "load_power_w": inverter_data["load_power"],

                "inverter_complex_power_va": inverter_data["apparent_power_1"],
                "load_complex_power_va": inverter_data["apparent_load_power"],
                "grid_complex_power_va": inverter_data["apparent_power_3"],

                "inverter_reactive_power_var": inverter_data["reactive_power_1"],
                "grid_reactive_power_var": inverter_data["reactive_power_2"],
                "load_reactive_power_var": inverter_data["reactive_power_3"]
            })

        if self.is_main_inverter(inverter):
            payload["system_status"] = self.calculate_system_status(payload)
            self.latest_main_payload = payload.copy()
        else:
            payload["system_status"] = self.calculate_system_status(payload)

        return payload

    def close_all_ports(self):
        for inverter in self.inverters:
            try:
                instrument = inverter.get("instrument")

                if instrument and instrument.serial:
                    instrument.serial.close()

            except Exception:
                pass

    def loop(self, client):
        if not self.enabled:
            return

        while app_state.RUNNING:
            for inverter in self.inverters:
                try:
                    payload = self.read_inverter(inverter)

                    topic = f"solar/{self.site_id}/inverter/{inverter['id']}/telemetry"

                    publish(client, topic, payload, qos=1, retain=False)

                except Exception as e:
                    print(f"[INVERTER] Error reading {inverter['id']}: {e}")

                    publish_error(
                        client,
                        self.site_id,
                        "inverter",
                        e,
                        extra={
                            "device_id": inverter["id"],
                            "port": inverter["port"]
                        }
                    )

            time.sleep(self.interval)

        self.close_all_ports()
