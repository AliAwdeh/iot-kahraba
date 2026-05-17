import paho.mqtt.client as mqtt


def create_mqtt_client(username=None, password=None):
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
    except Exception:
        client = mqtt.Client()

    if username and password:
        client.username_pw_set(username, password)

    return client
