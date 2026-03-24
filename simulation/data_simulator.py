#!/usr/bin/env python3
"""
data_simulator.py — Prediction-Paced Sequential CSV Playback
===============================================================

Reads abs_smart_grid_dataset_20k.csv row-by-row and publishes each row
as a single time step.

PACING: The simulator does NOT advance on a fixed timer. It publishes a
row, then WAITS for the ML service to publish a prediction on
room/ml/predictions before moving to the next row. The speed of
the ML model determines the speed of the simulation.

HARD RESET ON BOOT:
  • Always starts from Row 1 (the first data row after headers).
  • Calls the ML API's /reset endpoint to synchronise the model's
    internal CSV pointer, so predictions line up with Row 1.
  • No state is cached between restarts.

Usage:
    python simulation/data_simulator.py

Press Ctrl+C to stop.
"""

import csv
import json
import os
import sys
import time
import threading
from pathlib import Path

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
BROKER_ADDRESS = "127.0.0.1"
BROKER_PORT = 1883

SENSOR_TOPIC = "room/sensors"
PREDICTION_TOPIC = "room/ml/predictions"

# ML API base URL — used to reset the model's CSV index on boot.
ML_API_BASE = os.environ.get("ML_API_BASE", "http://127.0.0.1:5000")

# Read from the 20k dataset in the project root folder.
CSV_PATH = Path(__file__).resolve().parents[1] / "abs_smart_grid_dataset_20k.csv"

# Maximum seconds to wait for a prediction before timing out.
PREDICTION_TIMEOUT = int(os.environ.get("PREDICTION_TIMEOUT", 30))

# Minimum seconds between rows — keeps output readable even when
# the model responds instantly. Set to 0 to go as fast as possible.
MIN_ROW_DELAY = float(os.environ.get("MIN_ROW_DELAY", 3))

# ---------------------------------------------------------------------------
# Synchronisation: wait for prediction before advancing
# ---------------------------------------------------------------------------
prediction_event = threading.Event()
last_prediction = {}


# ---------------------------------------------------------------------------
# MQTT setup
# ---------------------------------------------------------------------------

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("✓ Simulator connected to MQTT broker")
        # Subscribe to predictions so we know when to advance
        client.subscribe(PREDICTION_TOPIC, qos=1)
        print(f"✓ Subscribed to {PREDICTION_TOPIC} (waiting for predictions)")
    else:
        print(f"✗ Connection failed (rc={rc})")


def on_message(client, userdata, msg):
    """Fires when a prediction arrives — unblocks the main loop."""
    global last_prediction
    try:
        last_prediction = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        last_prediction = {}
    prediction_event.set()


client = mqtt.Client(
    client_id="room-data-simulator",
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
)
client.on_connect = on_connect
client.on_message = on_message

# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------
rows: list[dict] = []


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


# ---------------------------------------------------------------------------
# Hard-reset the ML API's internal CSV pointer
# ---------------------------------------------------------------------------
def reset_ml_api_index():
    """POST to /reset so the ML API's row pointer syncs with Row 1."""
    try:
        import requests
        resp = requests.post(f"{ML_API_BASE}/reset", timeout=5)
        if resp.status_code == 200:
            print(f"✓ ML API index reset (synced to Row 1)")
        else:
            print(f"⚠ ML API responded with {resp.status_code} — index may be out of sync")
    except Exception as exc:
        print(f"⚠ Could not reach ML API at {ML_API_BASE}/reset: {exc}")
        print("  (ML predictions may start from a stale position)")


# ---------------------------------------------------------------------------
# Main loop — prediction-paced sequential playback
# ---------------------------------------------------------------------------
def main():
    print("=" * 60)
    print("  Smart Room — Prediction-Paced CSV Playback")
    print("=" * 60)
    print(f"  CSV     : {CSV_PATH}")
    print(f"  Broker  : {BROKER_ADDRESS}:{BROKER_PORT}")
    print(f"  Publish : {SENSOR_TOPIC}")
    print(f"  Wait on : {PREDICTION_TOPIC}")
    print(f"  Timeout : {PREDICTION_TIMEOUT}s per row")
    print("=" * 60)

    # ── HARD RESET: Fresh CSV load, always from Row 1 ──
    global rows
    rows = load_csv_rows()
    step = 0  # Local counter, NOT cached anywhere
    print(f"Loaded {len(rows)} rows from CSV dataset")

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
    time.sleep(0.5)

    # ── HARD RESET: Reset the ML API's CSV pointer to Row 1 ──
    reset_ml_api_index()

    # Battery: simple linear drain, no randomness.
    # Starts at 85%, drops 0.1% per row, floors at 20%.
    battery = 85.0

    try:
        print("\nPlaying back CSV data (prediction-paced)… Press Ctrl+C to stop.\n")

        # ── Start from Row 1, always ──
        for row in rows:
            step += 1

            # Read values directly from CSV — no interpolation, no randomness.
            timestamp = row.get("Timestamp", "")
            temperature_c = float(row["Temperature_C"])
            humidity = float(row["Humidity_%"])
            occupancy = int(float(row["Occupancy"]))
            energy_kw = float(row["Energy_kW"])

            # Handle possible column name variations
            lux_col = "Luminous_Intensity_Lux" if "Luminous_Intensity_Lux" in row else "Luminous_Intensity"
            lux = float(row[lux_col])

            # Battery: deterministic linear drain
            battery = max(20.0, battery - 0.1)

            payload = {
                "timestamp": timestamp,
                # Environmental data → ML model input
                "temperature_c": round(temperature_c, 2),
                "temperature": round(temperature_c, 2),
                "humidity": round(humidity, 2),
                "lux": round(lux, 2),
                "occupancy": occupancy,
                # Actual energy from CSV → dashboard "Actual Load"
                "energy_kw": round(energy_kw, 4),
                # Battery → rule engine
                "battery_level": round(battery, 1),
            }

            # Clear the event BEFORE publishing so we wait for the NEW prediction
            prediction_event.clear()

            client.publish(SENSOR_TOPIC, json.dumps(payload), qos=1)

            occ_str = "OCC" if occupancy else "---"
            print(
                f"  [{step:>5}/{len(rows)}] "
                f"{timestamp} | "
                f"Temp: {temperature_c:>5.1f}°C | "
                f"Lux: {lux:>8.1f} | "
                f"Occ: {occ_str} | "
                f"Actual: {energy_kw:>6.4f} kW | "
                f"Batt: {battery:>5.1f}%",
                end="",
                flush=True,
            )

            # ── WAIT for prediction before advancing ──
            row_start = time.time()
            got_prediction = prediction_event.wait(timeout=PREDICTION_TIMEOUT)

            if got_prediction:
                pred_val = last_prediction.get("predicted_energy_kw", "?")
                if isinstance(pred_val, (int, float)):
                    print(f" → Predicted: {pred_val:>7.4f} kW ✓")
                else:
                    print(f" → Predicted: {pred_val} ✓")
            else:
                print(f" → ⚠ No prediction within {PREDICTION_TIMEOUT}s (continuing)")

            # ── Enforce minimum delay so output is readable ──
            elapsed = time.time() - row_start
            remaining = MIN_ROW_DELAY - elapsed
            if remaining > 0:
                time.sleep(remaining)

        print("\n✓ Reached end of CSV dataset.")

    except KeyboardInterrupt:
        print("\n\n✓ Simulation stopped.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
