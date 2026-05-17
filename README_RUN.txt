Raspberry Pi Solar Gateway

Run:

cd raspberry_pi_solar_gateway
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 main.py

Expected devices:
- /dev/ttyUSB0 = must_1 main solar inverter
- /dev/ttyUSB1 = must_2 water inverter
- /dev/ttyACM0 = Raspberry Pi Pico relay controller

Check devices:
lsusb
ls /dev/ttyUSB*
ls /dev/ttyACM*

MQTT broker is configured in config.json:
192.168.0.223:1883
