#!/usr/bin/env python3
"""
mqtt_logger.py — Background MQTT-to-SQLite Logger
===================================================

Subscribes to sensor and ML MQTT topics, buffers readings in memory,
and writes 5-minute averages to the SQLite database.

Run as a systemd service (see systemd/mqtt-logger.service).

Data flow:
  Hardware (MQTT) ──► buffer ──► 5-min average ──► SQLite
                                       └──► republish to room/data/averaged
"""

import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_CLIENT_ID = "room-mqtt-logger"

# Topics to subscribe
TOPIC_SENSORS = "room/sensors"
TOPIC_ML = "room/ml/predictions"

# Topic to republish averaged data
TOPIC_AVERAGED = "room/data/averaged"

# Database path (relative to this script or absolute)
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "room_backend", "db.sqlite3"),
)

# Flush interval in seconds
FLUSH_INTERVAL = int(os.environ.get("FLUSH_INTERVAL", 5))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mqtt_logger")

# ---------------------------------------------------------------------------
# Thread-safe buffers
# ---------------------------------------------------------------------------
sensor_buffer: list[dict] = []
ml_buffer: list[dict] = []
buffer_lock = threading.Lock()

# Graceful shutdown flag
shutdown_event = threading.Event()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    """Open a WAL-mode SQLite connection with a generous busy timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def ensure_tables(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist yet (mirrors Django models)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS energy_sensorlog (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL DEFAULT (datetime('now')),
            temperature REAL    NOT NULL,
            humidity    REAL    NOT NULL,
            occupancy   INTEGER NOT NULL,
            voltage     REAL    NOT NULL,
            current     REAL    NOT NULL,
            battery_level REAL  NOT NULL
        );

        CREATE TABLE IF NOT EXISTS energy_mlprediction (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp               TEXT    NOT NULL DEFAULT (datetime('now')),
            predicted_energy_range  REAL    NOT NULL,
            peak_demand             REAL    NOT NULL
        );
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Averaging logic
# ---------------------------------------------------------------------------
def compute_sensor_average(readings: list[dict]) -> dict | None:
    """Return the arithmetic average of buffered sensor readings."""
    if not readings:
        return None

    n = len(readings)
    avg = {
        "temperature": round(sum(r["temperature"] for r in readings) / n, 2),
        "humidity": round(sum(r["humidity"] for r in readings) / n, 2),
        "occupancy": 1 if sum(r["occupancy"] for r in readings) / n >= 0.5 else 0,
        "voltage": round(sum(r["voltage"] for r in readings) / n, 2),
        "current": round(sum(r["current"] for r in readings) / n, 2),
        "battery_level": round(sum(r["battery_level"] for r in readings) / n, 2),
    }
    return avg


def compute_ml_average(predictions: list[dict]) -> dict | None:
    """Return the average of buffered ML predictions."""
    if not predictions:
        return None

    n = len(predictions)
    avg = {
        "predicted_energy_range": round(
            sum(p["predicted_energy_range"] for p in predictions) / n, 2
        ),
        "peak_demand": round(sum(p["peak_demand"] for p in predictions) / n, 2),
    }
    return avg


# ---------------------------------------------------------------------------
# Flush (runs every 5 minutes)
# ---------------------------------------------------------------------------
def flush_to_db(client: mqtt.Client) -> None:
    """Drain the buffers, compute averages, write to SQLite, and republish."""
    global sensor_buffer, ml_buffer

    with buffer_lock:
        sensors_snapshot = sensor_buffer.copy()
        ml_snapshot = ml_buffer.copy()
        sensor_buffer.clear()
        ml_buffer.clear()

    sensor_avg = compute_sensor_average(sensors_snapshot)
    ml_avg = compute_ml_average(ml_snapshot)

    if sensor_avg is None and ml_avg is None:
        log.info("Flush: No data in buffers — skipping.")
        return

    try:
        conn = get_db_connection()
        ensure_tables(conn)

        now = datetime.now(timezone.utc).isoformat()

        if sensor_avg:
            conn.execute(
                """
                INSERT INTO energy_sensorlog
                    (timestamp, temperature, humidity, occupancy,
                     voltage, current, battery_level)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    sensor_avg["temperature"],
                    sensor_avg["humidity"],
                    sensor_avg["occupancy"],
                    sensor_avg["voltage"],
                    sensor_avg["current"],
                    sensor_avg["battery_level"],
                ),
            )
            log.info("Flush: Wrote sensor average → %s", sensor_avg)

        if ml_avg:
            conn.execute(
                """
                INSERT INTO energy_mlprediction
                    (timestamp, predicted_energy_range, peak_demand)
                VALUES (?, ?, ?)
                """,
                (now, ml_avg["predicted_energy_range"], ml_avg["peak_demand"]),
            )
            log.info("Flush: Wrote ML average → %s", ml_avg)

        conn.commit()
        conn.close()

    except sqlite3.Error as exc:
        log.error("SQLite error during flush: %s", exc)
        return

    # Republish averaged data for the frontend
    if sensor_avg:
        payload = {**sensor_avg, "timestamp": now}
        if ml_avg:
            payload.update(ml_avg)
        client.publish(TOPIC_AVERAGED, json.dumps(payload), qos=1)
        log.info("Republished averaged data to %s", TOPIC_AVERAGED)


def flush_loop(client: mqtt.Client) -> None:
    """Background thread that calls flush_to_db every FLUSH_INTERVAL."""
    while not shutdown_event.is_set():
        shutdown_event.wait(FLUSH_INTERVAL)
        if not shutdown_event.is_set():
            flush_to_db(client)


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        log.info("Connected to MQTT broker at %s:%d", MQTT_BROKER, MQTT_PORT)
        client.subscribe([(TOPIC_SENSORS, 1), (TOPIC_ML, 1)])
        log.info("Subscribed to %s, %s", TOPIC_SENSORS, TOPIC_ML)
    else:
        log.error("MQTT connection failed with code %d", rc)


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("Bad payload on %s: %s", msg.topic, exc)
        return

    with buffer_lock:
        if msg.topic == TOPIC_SENSORS:
            if "temperature" not in payload and "temperature_c" in payload:
                payload["temperature"] = payload["temperature_c"]
            required = {
                "temperature", "humidity", "occupancy",
                "voltage", "current", "battery_level",
            }
            if not required.issubset(payload.keys()):
                log.warning("Sensor payload missing keys: %s", required - payload.keys())
                return
            sensor_buffer.append(payload)
            log.debug("Buffered sensor reading (%d in buffer)", len(sensor_buffer))

        elif msg.topic == TOPIC_ML:
            required = {"predicted_energy_range", "peak_demand"}
            if not required.issubset(payload.keys()):
                log.warning("ML payload missing keys: %s", required - payload.keys())
                return
            ml_buffer.append(payload)
            log.debug("Buffered ML prediction (%d in buffer)", len(ml_buffer))


def on_disconnect(client, userdata, rc, properties=None):
    if rc != 0:
        log.warning("Unexpected MQTT disconnect (rc=%d). Will auto-reconnect.", rc)


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

    log.info("Starting MQTT Logger (flush every %ds)", FLUSH_INTERVAL)
    log.info("Database: %s", os.path.abspath(DB_PATH))

    # Ensure DB & tables exist on startup
    conn = get_db_connection()
    ensure_tables(conn)
    conn.close()

    # MQTT client
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

    # Start the 5-minute flush thread
    flush_thread = threading.Thread(target=flush_loop, args=(client,), daemon=True)
    flush_thread.start()

    # Blocking network loop — handles reconnects automatically
    client.loop_start()

    # Wait for shutdown signal
    shutdown_event.wait()

    # Clean up
    log.info("Performing final flush …")
    flush_to_db(client)
    client.loop_stop()
    client.disconnect()
    log.info("MQTT Logger stopped.")


if __name__ == "__main__":
    main()
