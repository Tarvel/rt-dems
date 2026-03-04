#!/usr/bin/env python3
"""
data_simulator.py — Simulated Sensor & ML Data Publisher
==========================================================

Publishes fake sensor + ML data to the MQTT broker for testing
the full pipeline (logger ► SQLite, rule engine ► GPIO, dashboard).

Usage:
    python simulation/data_simulator.py

Press Ctrl+C to stop.
"""

import json
import random
import signal
import sys
import time

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BROKER_ADDRESS = "127.0.0.1"
BROKER_PORT = 1883

SENSOR_TOPIC = "room/sensors"
ML_TOPIC = "room/ml/predictions"

# How often to publish (seconds) — 5s for fast testing
PUBLISH_INTERVAL = 5

# ---------------------------------------------------------------------------
# MQTT setup
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("✓ Simulator connected to MQTT broker")
    else:
        print(f"✗ Connection failed (rc={rc})")


client = mqtt.Client(
    client_id="room-data-simulator",
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
)
client.on_connect = on_connect

# ---------------------------------------------------------------------------
# Simulation state
# ---------------------------------------------------------------------------
battery = 85.0
temperature = 26.0
occupancy_streak = 0  # Track how long occupancy stays 0 for testing


def generate_sensor_payload() -> dict:
    """Generate a realistic sensor reading with smooth transitions."""
    global battery, temperature

    # Battery drains slowly, occasional recharge bump
    if random.random() < 0.05:  # 5% chance of recharge event
        battery = min(100.0, battery + random.uniform(5.0, 15.0))
    else:
        battery = max(5.0, battery - random.uniform(0.05, 0.4))

    # Temperature drifts smoothly
    temperature += random.uniform(-0.3, 0.35)
    temperature = max(20.0, min(35.0, temperature))

    # Occupancy: mostly occupied, sometimes empty for a stretch
    global occupancy_streak
    if occupancy_streak > 0:
        occupancy_streak -= 1
        occ = 0
    elif random.random() < 0.15:  # 15% chance to start empty streak
        occupancy_streak = random.randint(1, 4)
        occ = 0
    else:
        occ = 1

    return {
        "temperature": round(temperature, 1),
        "humidity": round(random.uniform(40.0, 65.0), 1),
        "occupancy": occ,
        "voltage": round(random.uniform(215.0, 225.0), 1),
        "current": round(random.uniform(2.0, 8.0), 2),
        "battery_level": round(battery, 1),
    }


def generate_ml_payload() -> dict:
    """Generate a fake ML prediction."""
    peak = 2500.0
    predicted = round(random.uniform(1500, 3500), 1)
    return {
        "predicted_energy_range": predicted,
        "peak_demand": peak,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  Smart Room — Data Simulator")
    print("=" * 60)
    print(f"  Broker  : {BROKER_ADDRESS}:{BROKER_PORT}")
    print(f"  Topics  : {SENSOR_TOPIC}, {ML_TOPIC}")
    print(f"  Interval: every {PUBLISH_INTERVAL}s")
    print("=" * 60)

    try:
        client.connect(BROKER_ADDRESS, BROKER_PORT, keepalive=60)
    except OSError as e:
        print(f"\n✗ Cannot connect to broker: {e}")
        print("  Make sure Mosquitto is running: sudo systemctl start mosquitto")
        sys.exit(1)

    client.loop_start()
    time.sleep(0.5)  # Let connection establish

    tick = 0
    try:
        print("\nPublishing simulated data… Press Ctrl+C to stop.\n")
        while True:
            tick += 1
            sensor = generate_sensor_payload()
            ml = generate_ml_payload()

            client.publish(SENSOR_TOPIC, json.dumps(sensor), qos=1)
            client.publish(ML_TOPIC, json.dumps(ml), qos=1)

            occ_str = "OCCUPIED" if sensor["occupancy"] else "EMPTY   "
            print(
                f"  [{tick:>4}] "
                f"Temp: {sensor['temperature']:>5.1f}°C | "
                f"Batt: {sensor['battery_level']:>5.1f}% | "
                f"Occ: {occ_str} | "
                f"ML: {ml['predicted_energy_range']:.0f} vs {ml['peak_demand']:.0f}"
            )

            time.sleep(PUBLISH_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n✓ Simulation stopped.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()