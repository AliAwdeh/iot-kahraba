import json
import threading
import time

import serial

from core.time_utils import now_iso
from mqtt.publisher import publish


class RelayManager:
    """
    Controls relays through Raspberry Pi Pico over USB serial.

    Pico command examples:
        STATUS
        ON 1
        OFF 1
    """

    def __init__(self, config, site_id):
        self.site_id = site_id

        self.enabled = config.get("enabled", False)
        self.mode = config.get("mode", "pico_serial")
        self.port = config.get("port", "/dev/ttyACM0")
        self.baudrate = int(config.get("baudrate", 115200))

        self.relays = {}
        self.states = {}
        self.serial_conn = None
        self.lock = threading.Lock()

        if not self.enabled:
            print("[RELAYS] Disabled")
            return

        for item in config.get("items", []):
            relay_id = item["id"]
            relay_number = int(item["number"])

            self.relays[relay_id] = relay_number
            self.states[relay_id] = "UNKNOWN"

        print(f"[RELAYS] Loaded Pico serial relays: {self.relays}")
        print(f"[RELAYS] Pico serial port: {self.port} baud={self.baudrate}")

        self.connect()
        self.refresh_status_from_pico()

    def connect(self):
        try:
            if self.serial_conn and self.serial_conn.is_open:
                return True

            self.serial_conn = serial.Serial(
                self.port,
                self.baudrate,
                timeout=2
            )

            time.sleep(2)

            while self.serial_conn.in_waiting:
                line = self.serial_conn.readline().decode(errors="ignore").strip()
                if line:
                    print(f"[RELAYS] Pico startup: {line}")

            print(f"[RELAYS] Connected to Pico on {self.port}")
            return True

        except Exception as e:
            print(f"[RELAYS] Could not connect to Pico on {self.port}: {e}")
            self.serial_conn = None
            return False

    def send_pico_command(self, command):
        with self.lock:
            try:
                if self.serial_conn is None or not self.serial_conn.is_open:
                    if not self.connect():
                        return {
                            "ok": False,
                            "error": "pico_serial_not_connected",
                            "command": command
                        }

                self.serial_conn.reset_input_buffer()
                self.serial_conn.write((command + "\n").encode())
                self.serial_conn.flush()

                response = self.serial_conn.readline().decode(errors="ignore").strip()

                print(f"[RELAYS SERIAL] {command} => {response}")

                return {
                    "ok": True,
                    "command": command,
                    "response": response
                }

            except Exception as e:
                print(f"[RELAYS] Serial error: {e}")

                try:
                    if self.serial_conn:
                        self.serial_conn.close()
                except Exception:
                    pass

                self.serial_conn = None

                return {
                    "ok": False,
                    "error": str(e),
                    "command": command
                }

    def parse_status_response(self, response):
        if not response:
            return

        text = response.strip()

        try:
            data = json.loads(text)

            if isinstance(data, dict):
                for relay_id, number in self.relays.items():
                    possible_keys = [
                        str(number),
                        relay_id,
                        f"relay_{number}"
                    ]

                    for key in possible_keys:
                        if key in data:
                            state = str(data[key]).upper()
                            if state in ["ON", "OFF"]:
                                self.states[relay_id] = state

                return

        except Exception:
            pass

        upper = text.upper()

        for relay_id, number in self.relays.items():
            if (
                f"{number}:ON" in upper
                or f"{number} ON" in upper
                or f"RELAY {number} ON" in upper
            ):
                self.states[relay_id] = "ON"

            elif (
                f"{number}:OFF" in upper
                or f"{number} OFF" in upper
                or f"RELAY {number} OFF" in upper
            ):
                self.states[relay_id] = "OFF"

        tokens = upper.replace(",", " ").split()
        only_states = [t for t in tokens if t in ["ON", "OFF"]]

        if len(only_states) >= len(self.relays):
            sorted_relays = sorted(self.relays.items(), key=lambda x: x[1])

            for index, (relay_id, number) in enumerate(sorted_relays):
                self.states[relay_id] = only_states[index]

    def refresh_status_from_pico(self):
        result = self.send_pico_command("STATUS")

        if result.get("ok"):
            self.parse_status_response(result.get("response", ""))

        return result

    def set_state(self, relay_id, state):
        if not self.enabled:
            return {
                "ok": False,
                "error": "relays_disabled",
                "relay_id": relay_id
            }

        if relay_id not in self.relays:
            return {
                "ok": False,
                "error": "unknown_relay",
                "relay_id": relay_id
            }

        state = str(state).upper()

        if state not in ["ON", "OFF"]:
            return {
                "ok": False,
                "error": "invalid_state",
                "relay_id": relay_id,
                "state": state
            }

        relay_number = self.relays[relay_id]
        command = f"{state} {relay_number}"

        result = self.send_pico_command(command)

        if not result.get("ok"):
            return {
                "ok": False,
                "error": result.get("error", "pico_command_failed"),
                "relay_id": relay_id,
                "desired_state": state,
                "pico_command": command
            }

        self.states[relay_id] = state
        self.refresh_status_from_pico()

        return {
            "ok": True,
            "relay_id": relay_id,
            "actual_state": self.states.get(relay_id, state),
            "desired_state": state,
            "pico_command": command,
            "pico_response": result.get("response"),
            "timestamp": now_iso()
        }

    def get_state(self, relay_id):
        return self.states.get(relay_id, "UNKNOWN")

    def get_all_states(self):
        return dict(self.states)

    def publish_status(self, client, relay_id, desired_state=None):
        actual_state = self.get_state(relay_id)

        topic = f"solar/{self.site_id}/relay/{relay_id}/status"

        payload = {
            "device_type": "relay",
            "relay_id": relay_id,
            "actual_state": actual_state,
            "desired_state": desired_state,
            "applied": actual_state == desired_state if desired_state else None,
            "source": "raspberry_pi_pico_serial",
            "timestamp": now_iso()
        }

        publish(client, topic, payload, qos=1, retain=True)

    def publish_all_statuses(self, client):
        self.refresh_status_from_pico()

        payload = {
            "device_type": "relay_group",
            "relays": self.get_all_states(),
            "source": "raspberry_pi_pico_serial",
            "timestamp": now_iso()
        }

        topic = f"solar/{self.site_id}/relay/all/status"
        publish(client, topic, payload, qos=1, retain=True)

        for relay_id in self.relays:
            self.publish_status(client, relay_id)
