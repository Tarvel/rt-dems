#!/usr/bin/env python3
"""
hw_bridge.py — Group 1 Hardware → room/sensors Bridge
========================================================

Subscribes to Group 1's two MQTT topics (NANO environmental sensors
and UNO battery data), merges them into a single normalised payload
that matches the ``room/sensors`` contract, and republishes.

This means every existing subscriber (mqtt_logger, rule_engine,
ML service, dashboard) receives hardware data in exactly the same
format as the data simulator — zero changes needed downstream.

Group 1 topics (configurable via env):
    room/hardware/nano   — temperature, humidity, voltage, current,
                           power, energy, lux, ultrasonic_occupancy,
                           radar_motion
    room/hardware/uno    — battery_voltage, soc

Data flow:
    Group 1 ESP32 ──MQTT──▶ room/hardware/nano ──┐
                           room/hardware/uno  ──┤
                                                 ▼
                                         hw_bridge.py
                                           (merge + normalise)
                                                 │
                                                 ▼
                                          room/sensors
                                                 │
                    ┌────────────────┬────────────┼────────────┐
                    ▼                ▼            ▼            ▼
              mqtt_logger     rule_engine    ML service    dashboard

Run as a systemd service or in a terminal alongside the other workers.

Usage:
    python workers/hw_bridge.py
"""

import json
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# ── .env support ──
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_CLIENT_ID = "room-hw-bridge"

# Group 1 source topics (what their ESP32 publishes to)
TOPIC_HW_NANO = os.environ.get("TOPIC_HW_NANO", "room/hardware/nano")
TOPIC_HW_UNO = os.environ.get("TOPIC_HW_UNO", "room/hardware/uno")

# Our canonical sensor topic (where we republish the normalised payload)
TOPIC_SENSORS = "room/sensors"

# How long to wait for a matching UNO battery reading before publishing
# the NANO payload with the last-known battery value.
BATTERY_MERGE_WINDOW_S = float(
    os.environ.get("BATTERY_MERGE_WINDOW_S", 5.0)
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("hw_bridge")

# ---------------------------------------------------------------------------
# Shared state (latest battery reading from UNO, protected by lock)
# ---------------------------------------------------------------------------
state_lock = threading.Lock()
latest_battery: dict = {}        # raw UNO payload
latest_nano: dict = {}           # raw NANO payload (most recent)

shutdown_event = threading.Event()

# Stats
stats = {"nano_rx": 0, "uno_rx": 0, "published": 0}


# ---------------------------------------------------------------------------
# Normalisation: Group 1 → room/sensors
# ---------------------------------------------------------------------------
def normalise(nano: dict, battery: dict) -> dict:
    """Merge NANO + UNO data into our ``room/sensors`` contract.

    Field mapping:
        Group 1 NANO             → room/sensors
        ──────────────────────     ───────────────────
        temperature              → temperature, temperature_c
        humidity                 → humidity
        voltage                  → voltage
        current                  → current
        power                    → (passed through, not required)
        energy                   → energy_kw  (renamed)
        lux                      → lux
        ultrasonic_occupancy     → occupancy  (renamed)
        radar_motion             → (passed through)

        Group 1 UNO
        ──────────────────────
        soc                      → battery_level  (renamed)
        battery_voltage          → (passed through)
    """
    # Temperature
    temp = nano.get("temperature", 0.0)

    # Occupancy — prefer ultrasonic; fall back to radar
    occ_raw = nano.get("ultrasonic_occupancy")
    if occ_raw is None:
        occ_raw = nano.get("radar_motion", 0)
    occupancy = int(occ_raw)

    # Battery — from UNO 'soc' field
    battery_level = float(battery.get("soc", 0.0))

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),

        # Core fields every subscriber expects
        "temperature_c": round(float(temp), 2),
        "temperature": round(float(temp), 2),
        "humidity": round(float(nano.get("humidity", 0.0)), 2),
        "lux": round(float(nano.get("lux", 0.0)), 2),
        "occupancy": occupancy,
        "battery_level": round(battery_level, 1),

        # Electrical measurements (optional in contract, but we have them)
        "voltage": round(float(nano.get("voltage", 0.0)), 2),
        "current": round(float(nano.get("current", 0.0)), 2),

        # Energy — Group 1 calls it "energy" (kWh cumulative);
        # our contract uses "energy_kw".
        "energy_kw": round(float(nano.get("energy", 0.0)), 4),

        # Pass-through fields (not required by contract, but useful)
        "power_w": round(float(nano.get("power", 0.0)), 2),
        "radar_motion": int(nano.get("radar_motion", 0)),
        "battery_voltage": round(float(battery.get("battery_voltage", 0.0)), 2),

        # Source tag so downstream can distinguish hardware from simulator
        "source": "group1_hardware",
    }
    return payload


# ---------------------------------------------------------------------------
# MQTT Callbacks
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info("Connected to MQTT broker at %s:%d", MQTT_BROKER, MQTT_PORT)
        client.subscribe([
            (TOPIC_HW_NANO, 1),
            (TOPIC_HW_UNO, 1),
        ])
        log.info("Subscribed to %s, %s", TOPIC_HW_NANO, TOPIC_HW_UNO)
    else:
        log.error("MQTT connection failed with code %d", rc)


def on_message(client, userdata, msg):
    global latest_battery, latest_nano

    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("Bad payload on %s: %s", msg.topic, exc)
        return

    # ── UNO battery data ──
    if msg.topic == TOPIC_HW_UNO:
        with state_lock:
            latest_battery = payload
        stats["uno_rx"] += 1
        log.debug(
            "UNO battery update: soc=%.0f%%, voltage=%.1fV",
            payload.get("soc", 0),
            payload.get("battery_voltage", 0),
        )
        return

    # ── NANO environmental data ──
    if msg.topic == TOPIC_HW_NANO:
        stats["nano_rx"] += 1

        with state_lock:
            latest_nano = payload
            bat = latest_battery.copy()

        # Normalise and republish
        merged = normalise(payload, bat)
        out = json.dumps(merged)
        client.publish(TOPIC_SENSORS, out, qos=1)
        stats["published"] += 1

        log.info(
            "Bridge: NANO → room/sensors  "
            "(temp=%.1f°C, occ=%d, battery=%.0f%%, energy=%.3fkW)  "
            "[nano_rx=%d, uno_rx=%d, pub=%d]",
            merged["temperature"],
            merged["occupancy"],
            merged["battery_level"],
            merged["energy_kw"],
            stats["nano_rx"],
            stats["uno_rx"],
            stats["published"],
        )


def on_disconnect(client, userdata, *args, **kwargs):
    rc = args[0] if args else 0
    if rc != 0:
        log.warning("Unexpected MQTT disconnect (rc=%s). Will auto-reconnect.", rc)


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
def handle_signal(signum, frame):
    log.info("Received signal %d — shutting down …", signum)
    shutdown_event.set()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("Starting Hardware Bridge")
    log.info("  NANO topic: %s", TOPIC_HW_NANO)
    log.info("  UNO  topic: %s", TOPIC_HW_UNO)
    log.info("  Output    : %s", TOPIC_SENSORS)

    client = mqtt.Client(
        client_id=MQTT_CLIENT_ID,
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    try:
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    except OSError as exc:
        log.critical("Cannot connect to MQTT broker: %s", exc)
        sys.exit(1)

    client.loop_start()

    log.info("Hardware Bridge running. Press Ctrl+C to stop.")
    shutdown_event.wait()

    client.loop_stop()
    client.disconnect()
    log.info(
        "Hardware Bridge stopped. Stats: nano_rx=%d, uno_rx=%d, published=%d",
        stats["nano_rx"],
        stats["uno_rx"],
        stats["published"],
    )


if __name__ == "__main__":
    main()
