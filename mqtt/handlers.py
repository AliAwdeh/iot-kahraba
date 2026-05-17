import json

from core.time_utils import now_iso
from mqtt.publisher import publish, publish_error


class MqttHandlers:
    def __init__(self, site_id, mqtt_broker, mqtt_port, relay_manager):
        self.site_id = site_id
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        self.relay_manager = relay_manager

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"[MQTT] Connected to {self.mqtt_broker}:{self.mqtt_port}")

            relay_desired_topic = f"solar/{self.site_id}/relay/+/desired"
            relay_get_topic = f"solar/{self.site_id}/relay/+/get"
            relay_all_get_topic = f"solar/{self.site_id}/relay/all/get"

            client.subscribe(relay_desired_topic, qos=1)
            client.subscribe(relay_get_topic, qos=1)
            client.subscribe(relay_all_get_topic, qos=1)

            print(f"[MQTT] Subscribed to {relay_desired_topic}")
            print(f"[MQTT] Subscribed to {relay_get_topic}")
            print(f"[MQTT] Subscribed to {relay_all_get_topic}")

            self.relay_manager.publish_all_statuses(client)

            publish(
                client,
                f"solar/{self.site_id}/system/status",
                {
                    "device_type": "raspberry_pi_gateway",
                    "status": "online",
                    "site_id": self.site_id,
                    "timestamp": now_iso()
                },
                qos=1,
                retain=True
            )

        else:
            print(f"[MQTT] Connection failed with code {rc}")

    def on_disconnect(self, client, userdata, rc):
        print(f"[MQTT] Disconnected with code {rc}")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        raw_payload = msg.payload.decode(errors="ignore")

        print(f"[MQTT RECV] {topic} -> {raw_payload}")

        try:
            payload = json.loads(raw_payload) if raw_payload else {}

        except json.JSONDecodeError:
            publish_error(
                client,
                self.site_id,
                "mqtt",
                "invalid_json",
                extra={
                    "topic": topic,
                    "payload": raw_payload
                }
            )
            return

        parts = topic.split("/")

        try:
            if len(parts) < 5:
                raise ValueError("invalid_topic_format")

            relay_id = parts[3]
            action = parts[4]

            if relay_id == "all" and action == "get":
                self.relay_manager.publish_all_statuses(client)
                return

            if action == "get":
                self.relay_manager.publish_status(client, relay_id)
                return

            if action == "desired":
                desired_state = str(
                    payload.get("state") or payload.get("action") or ""
                ).upper()

                result = self.relay_manager.set_state(relay_id, desired_state)

                if not result.get("ok"):
                    publish_error(
                        client,
                        self.site_id,
                        "relay",
                        result.get("error"),
                        extra={
                            "relay_id": relay_id,
                            "desired_state": desired_state
                        }
                    )
                    return

                self.relay_manager.publish_status(
                    client,
                    relay_id,
                    desired_state=desired_state
                )

                self.relay_manager.publish_all_statuses(client)
                return

        except Exception as e:
            publish_error(
                client,
                self.site_id,
                "relay_mqtt_handler",
                e,
                extra={
                    "topic": topic,
                    "payload": payload
                }
            )
