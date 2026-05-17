import time
import signal

import core.state as app_state
from core.time_utils import now_iso
from mqtt.publisher import publish


class ShutdownManager:
    def __init__(self, mqtt_client, site_id):
        self.mqtt_client = mqtt_client
        self.site_id = site_id

    def handler(self, signum=None, frame=None):
        if not app_state.RUNNING:
            return

        print("[SYSTEM] Shutting down...")
        app_state.RUNNING = False

        try:
            publish(
                self.mqtt_client,
                f"solar/{self.site_id}/system/status",
                {
                    "device_type": "raspberry_pi_gateway",
                    "status": "offline",
                    "site_id": self.site_id,
                    "timestamp": now_iso()
                },
                qos=1,
                retain=True
            )

        except Exception:
            pass

        time.sleep(1)

        try:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

        except Exception:
            pass

    def register(self):
        signal.signal(signal.SIGINT, self.handler)
        signal.signal(signal.SIGTERM, self.handler)
