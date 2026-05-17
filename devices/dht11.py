import time

import board
import adafruit_dht

import core.state as app_state
from core.time_utils import now_iso
from mqtt.publisher import publish, publish_error


class DHT11Reader:
    def __init__(self, config, site_id):
        self.site_id = site_id

        self.enabled = config.get("enabled", False)
        self.device_id = config.get("device_id", "dht11_1")
        self.interval = int(config.get("interval_seconds", 5))
        self.sensor = None

        if not self.enabled:
            print("[DHT11] Disabled")
            return

        gpio_name = config.get("gpio", "D4")

        if gpio_name == "D4":
            pin = board.D4
        else:
            raise ValueError("This script currently supports DHT11 on D4 / GPIO4 only")

        self.sensor = adafruit_dht.DHT11(pin)

        print(f"[DHT11] Loaded on {gpio_name} / GPIO4 / physical pin 7")

    def loop(self, client):
        if not self.enabled:
            return

        topic = f"solar/{self.site_id}/sensor/{self.device_id}/telemetry"

        while app_state.RUNNING:
            try:
                temperature = self.sensor.temperature
                humidity = self.sensor.humidity

                if temperature is not None and humidity is not None:
                    payload = {
                        "device_type": "dht11",
                        "device_id": self.device_id,
                        "temperature_c": temperature,
                        "humidity_percent": humidity,
                        "status": "ok",
                        "timestamp": now_iso()
                    }

                    publish(client, topic, payload, qos=1, retain=False)

                else:
                    payload = {
                        "device_type": "dht11",
                        "device_id": self.device_id,
                        "status": "read_failed",
                        "error": "temperature_or_humidity_none",
                        "timestamp": now_iso()
                    }

                    publish(client, topic, payload, qos=1, retain=False)

            except RuntimeError as e:
                print(f"[DHT11] Read error: {e}")

            except Exception as e:
                print(f"[DHT11] Fatal error: {e}")
                publish_error(client, self.site_id, "dht11", e)

            time.sleep(self.interval)
