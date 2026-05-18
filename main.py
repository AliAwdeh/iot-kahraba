#!/usr/bin/env python3

import time

import core.state as app_state
from core.config import load_config
from core.threading_utils import start_thread

from mqtt.client import create_mqtt_client
from mqtt.handlers import MqttHandlers
from mqtt.publisher import configure_logging

from devices.relays import RelayManager
from devices.dht11 import DHT11Reader
from devices.must_inverter import MustInverterReader

from system.shutdown import ShutdownManager


def main():
    config = load_config("config.json")
    configure_logging(config.get("logging", {}))

    site_id = config["site_id"]

    mqtt_config = config["mqtt"]

    mqtt_broker = mqtt_config["broker"]
    mqtt_port = mqtt_config.get("port", 1883)
    mqtt_username = mqtt_config.get("username")
    mqtt_password = mqtt_config.get("password")

    mqtt_client = create_mqtt_client(
        username=mqtt_username,
        password=mqtt_password
    )

    relay_manager = RelayManager(
        config=config.get("relays", {}),
        site_id=site_id
    )

    dht11_reader = DHT11Reader(
        config=config.get("dht11", {}),
        site_id=site_id
    )

    inverter_reader = MustInverterReader(
        config=config.get("inverters", {}),
        site_id=site_id
    )

    mqtt_handlers = MqttHandlers(
        site_id=site_id,
        mqtt_broker=mqtt_broker,
        mqtt_port=mqtt_port,
        relay_manager=relay_manager
    )

    mqtt_client.on_connect = mqtt_handlers.on_connect
    mqtt_client.on_message = mqtt_handlers.on_message
    mqtt_client.on_disconnect = mqtt_handlers.on_disconnect

    shutdown_manager = ShutdownManager(
        mqtt_client=mqtt_client,
        site_id=site_id
    )

    shutdown_manager.register()

    print("[SYSTEM] Raspberry Pi IoT gateway starting")
    print(f"[SYSTEM] MQTT broker: {mqtt_broker}:{mqtt_port}")
    print("[SYSTEM] USB0 / must_1 = main solar inverter")
    print("[SYSTEM] USB1 / must_2 = water inverter")

    while app_state.RUNNING:
        try:
            mqtt_client.connect(mqtt_broker, mqtt_port, 60)
            break

        except Exception as e:
            print(f"[MQTT] Connection failed: {e}")
            time.sleep(5)

    mqtt_client.loop_start()

    start_thread("dht11_reader", dht11_reader.loop, mqtt_client)
    start_thread("inverter_reader", inverter_reader.loop, mqtt_client)

    print("[SYSTEM] IoT gateway running")

    try:
        while app_state.RUNNING:
            time.sleep(1)

    finally:
        shutdown_manager.handler()
        inverter_reader.close_all_ports()
        print("[SYSTEM] Stopped")


if __name__ == "__main__":
    main()
