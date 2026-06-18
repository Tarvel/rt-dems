#!/usr/bin/env python3
"""
hw_bridge.py — Group 1 Hardware → room/sensors Bridge
========================================================

Subscribes to Group 1's MQTT topic (combined environmental + battery
data from NANO), normalises it into the ``room/sensors`` contract,
and republishes.

This means every existing subscriber (mqtt_logger, rule_engine,
ML service, dashboard) receives hardware data in exactly the same
format as the data simulator — zero changes needed downstream.

Group 1 topic (configurable via env):
    room/hardware/nano   — temperature, humidity, voltage, current,
                           power, energy, lux, ultrasonic_occupancy,
                           radar_motion, battery_voltage, soc

Data flow:
    Group 1 ESP32 ──MQTT──▶ room/hardware/nano
                                    │
                                    ▼
                             hw_bridge.py
                               (normalise)
                                    │
                                    ▼
                             room/sensors
                                    │
                  ┌────────────────┬────────────┬────────────┐
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

# Group 1 source topic (what their ESP32 publishes to)
TOPIC_HW_NANO = os.environ.get("TOPIC_HW_NANO", "room/hardware/nano")

# Our canonical sensor topic (where we republish the normalised payload)
TOPIC_SENSORS = "room/sensors"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("hw_bridge")

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------
state_lock = threading.Lock()
shutdown_event = threading.Event()

# Stats
stats = {"rx": 0, "published": 0}


# ---------------------------------------------------------------------------
# Normalisation: Group 1 → room/sensors
# ---------------------------------------------------------------------------
def normalise(raw: dict) -> dict:
    """Normalise Group 1's combined payload into our ``room/sensors`` contract.

    Field mapping:
        Group 1 (combined)        → room/sensors
        ──────────────────────     ───────────────────
        temperature              → temperature, temperature_c
        humidity                 → humidity
        voltage                  → voltage
        current                  → current
        power                    → power_w  (renamed)
        energy                   → energy_kw  (renamed)
        lux                      → lux
        ultrasonic_occupancy     → occupancy  (renamed)
        radar_motion             → (passed through)
        soc                      → battery_level  (renamed)
        battery_voltage          → (passed through)
    """
    # Temperature
    temp = raw.get("temperature", 0.0)

    # Occupancy — prefer ultrasonic; fall back to radar
    occ_raw = raw.get("ultrasonic_occupancy")
    if occ_raw is None:
        occ_raw = raw.get("radar_motion", 0)
    occupancy = int(occ_raw)

    # Battery — from 'soc' field (now in the same payload)
    battery_level = float(raw.get("soc", 0.0))

    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),

        # Core fields every subscriber expects
        "temperature_c": round(float(temp), 2),
        "temperature": round(float(temp), 2),
        "humidity": round(float(raw.get("humidity", 0.0)), 2),
        "lux": round(float(raw.get("lux", 0.0)), 2),
        "occupancy": occupancy,
        "battery_level": round(battery_level, 1),

        # Electrical measurements (optional in contract, but we have them)
        "voltage": round(float(raw.get("voltage", 0.0)), 2),
        "current": round(float(raw.get("current", 0.0)), 2),

        # Energy — Group 1 calls it "energy" (kWh cumulative);
        # our contract uses "energy_kw".
        "energy_kw": round(float(raw.get("energy", 0.0)), 4),

        # Pass-through fields (not required by contract, but useful)
        "power_w": round(float(raw.get("power", 0.0)), 2),
        "radar_motion": int(raw.get("radar_motion", 0)),
        "battery_voltage": round(float(raw.get("battery_voltage", 0.0)), 2),

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
        client.subscribe(TOPIC_HW_NANO, qos=1)
        log.info("Subscribed to %s", TOPIC_HW_NANO)
    else:
        log.error("MQTT connection failed with code %d", rc)


def on_message(client, userdata, msg):
    try:
        # Try strict UTF-8 first (normal path)
        raw = msg.payload.decode("utf-8")
    except UnicodeDecodeError:
        # Group 1's Arduino sometimes sends garbled serial bytes —
        # fall back to latin-1 (accepts any byte) and let JSON
        # parsing decide if the content is salvageable.
        raw = msg.payload.decode("latin-1")
        log.debug("Non-UTF-8 bytes on %s — decoded via latin-1 fallback", msg.topic)

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Bad payload on %s: %s", msg.topic, exc)
        return

    # ── Combined NANO + battery data ──
    if msg.topic == TOPIC_HW_NANO:
        stats["rx"] += 1

        # Normalise and republish
        normalised = normalise(payload)
        out = json.dumps(normalised)
        client.publish(TOPIC_SENSORS, out, qos=1)
        stats["published"] += 1

        log.info(
            "Bridge: hardware → room/sensors  "
            "temp=%.1f°C  hum=%.1f%%  V=%.1f  A=%.2f  "
            "P=%.1fW  E=%.3fkWh  lux=%.1f  "
            "occ=%d  radar=%d  bat=%.0f%%  batV=%.1f  "
            "[rx=%d pub=%d]",
            normalised["temperature"],
            normalised["humidity"],
            normalised["voltage"],
            normalised["current"],
            normalised["power_w"],
            normalised["energy_kw"],
            normalised["lux"],
            normalised["occupancy"],
            normalised["radar_motion"],
            normalised["battery_level"],
            normalised["battery_voltage"],
            stats["rx"],
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
    log.info("  Input topic : %s", TOPIC_HW_NANO)
    log.info("  Output topic: %s", TOPIC_SENSORS)

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
        "Hardware Bridge stopped. Stats: rx=%d, published=%d",
        stats["rx"],
        stats["published"],
    )


if __name__ == "__main__":
    main()
