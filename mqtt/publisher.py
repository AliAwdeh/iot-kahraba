import json

from core.time_utils import now_iso


def safe_json(data):
    return json.dumps(data, ensure_ascii=False)


def publish(client, topic, payload, qos=1, retain=False):
    payload["published_at"] = now_iso()
    client.publish(topic, safe_json(payload), qos=qos, retain=retain)
    print(f"[MQTT PUB] {topic} -> {payload}")


def publish_error(client, site_id, source, error, extra=None):
    topic = f"solar/{site_id}/system/errors"

    payload = {
        "source": source,
        "status": "error",
        "error": str(error),
        "timestamp": now_iso()
    }

    if extra:
        payload.update(extra)

    publish(client, topic, payload, qos=1, retain=False)
