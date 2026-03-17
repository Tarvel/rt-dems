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

import csv
import json
import random
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BROKER_ADDRESS = "127.0.0.1"
BROKER_PORT = 1883

SENSOR_TOPIC = "room/sensors"

CSV_PATH = Path(__file__).resolve().parents[1] / "fake_data.csv"

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
rows: list[dict] = []
row_index = 0


def load_csv_rows() -> list[dict]:
    loaded: list[dict] = []
    try:
        with CSV_PATH.open("r", encoding="utf-8", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                loaded.append(row)
    except OSError as exc:
        print(f"\n✗ Failed to read CSV data at {CSV_PATH}: {exc}")
        sys.exit(1)

    if not loaded:
        print(f"\n✗ CSV file has no rows: {CSV_PATH}")
        sys.exit(1)

    return loaded


def generate_sensor_payload() -> dict:
    """Build one payload from CSV environment and synthetic power data."""
    global battery, row_index

    row = rows[row_index]
    row_index = (row_index + 1) % len(rows)

    # Battery changes gradually with occasional recharge spikes.
    if random.random() < 0.04:
        battery = min(100.0, battery + random.uniform(5.0, 15.0))
    else:
        battery = max(5.0, battery - random.uniform(0.5, 2.0))

    temperature_c = float(row["Temperature_C"])
    humidity = float(row["Humidity_%"])
    lux = float(row["Luminous_Intensity_Lux"])
    occupancy = 1 if int(float(row["Occupancy"])) > 0 else 0

    # Synthetic electrical data to emulate ESP-side power telemetry.
    voltage = round(random.uniform(215.0, 225.0), 1)
    current = round(random.uniform(2.0, 8.0), 2)

    return {
        "temperature_c": round(temperature_c, 2),
        "temperature": round(temperature_c, 2),
        "humidity": round(humidity, 2),
        "lux": round(lux, 2),
        "occupancy": occupancy,
        "voltage": voltage,
        "current": current,
        "power_w": round(voltage * current, 2),
        "battery_level": round(battery, 1),
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  Smart Room — Data Simulator")
    print("=" * 60)
    print(f"  CSV     : {CSV_PATH}")
    print(f"  Broker  : {BROKER_ADDRESS}:{BROKER_PORT}")
    print(f"  Topic   : {SENSOR_TOPIC}")
    print(f"  Interval: every {PUBLISH_INTERVAL}s")
    print("=" * 60)

    global rows
    rows = load_csv_rows()
    print(f"Loaded {len(rows)} rows from CSV environmental dataset")

    try:
        client.connect(BROKER_ADDRESS, BROKER_PORT, keepalive=60)
    except OSError as e:
        print(f"\n✗ Cannot connect to broker: {e}")
        print(
            "  Make sure Mosquitto is running: "
            "sudo systemctl start mosquitto"
        )
        sys.exit(1)

    client.loop_start()
    time.sleep(0.5)  # Let connection establish

    tick = 0
    try:
        print("\nPublishing simulated data… Press Ctrl+C to stop.\n")
        while True:
            tick += 1
            sensor = generate_sensor_payload()

            client.publish(SENSOR_TOPIC, json.dumps(sensor), qos=1)

            occ_str = "OCCUPIED" if sensor["occupancy"] else "EMPTY   "
            print(
                f"  [{tick:>4}] "
                f"Temp: {sensor['temperature_c']:>5.1f}°C | "
                f"Lux: {sensor['lux']:>8.1f} | "
                f"Batt: {sensor['battery_level']:>5.1f}% | "
                f"Occ: {occ_str} | "
                f"Power: {sensor['power_w']:>7.1f} W"
            )

            time.sleep(PUBLISH_INTERVAL)

    except KeyboardInterrupt:
        print("\n\n✓ Simulation stopped.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
