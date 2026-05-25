#!/usr/bin/env python3
"""
mqtt_logger.py — MQTT → SQLite Logger
======================================

Subscribes to:
    room/sensors          — live telemetry (logged to terminal only)
    room/ml/predictions   — ML predictions (buffered and flushed to DB)

Every FLUSH_INTERVAL seconds, buffered ML predictions are averaged and
written to the ``energy_mlprediction`` table.

Sensor data is NOT averaged/stored — it flows through room/sensors in
real-time for the dashboard and is captured as snapshots in
energy_relaystate whenever the rule engine makes a mode decision.
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

# ── .env support ──
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Topics to subscribe
TOPIC_SENSORS = "room/sensors"
TOPIC_ML = "room/ml/predictions"

# Database path (relative to this script or absolute)
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "room_backend", "db.sqlite3"),
)

# Flush interval in seconds (5 minutes)
FLUSH_INTERVAL = 5 * 60

# MQTT
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_CLIENT_ID = "room-mqtt-logger"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("mqtt_logger")

# ---------------------------------------------------------------------------
# Thread-safe buffer (ML only)
# ---------------------------------------------------------------------------
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
    """Create tables if they don't exist yet (mirrors Django models).

    If the table has the old schema (predicted_energy_range + peak_demand),
    drop it and recreate with the new schema.
    """
    # Check if old schema exists and migrate
    try:
        cur = conn.execute("PRAGMA table_info(energy_mlprediction)")
        cols = {row[1] for row in cur.fetchall()}
        if cols and "predicted_energy_wh" not in cols:
            # Old schema — drop and recreate
            conn.execute("DROP TABLE energy_mlprediction")
            log.info("Dropped old energy_mlprediction table (schema migration)")
    except sqlite3.OperationalError:
        pass

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS energy_mlprediction (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp               TEXT    NOT NULL DEFAULT (datetime('now')),
            predicted_energy_wh     REAL    NOT NULL DEFAULT 0.0,
            upper_bound_wh          REAL    NOT NULL DEFAULT 0.0,
            lower_bound_wh          REAL    NOT NULL DEFAULT 0.0
        );
        """
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Averaging logic (ML only)
# ---------------------------------------------------------------------------
def compute_ml_average(predictions: list[dict]) -> dict | None:
    """Return the average of buffered ML predictions."""
    if not predictions:
        return None

    n = len(predictions)
    avg = {
        "predicted_energy_wh": round(
            float(sum(p.get("predicted_energy_wh", 0) for p in predictions) / n), 4
        ),
        "upper_bound_wh": round(
            float(sum(p.get("upper_bound_wh", 0) for p in predictions) / n), 4
        ),
        "lower_bound_wh": round(
            float(sum(p.get("lower_bound_wh", 0) for p in predictions) / n), 4
        ),
    }
    return avg


# ---------------------------------------------------------------------------
# Flush (runs every 5 minutes)
# ---------------------------------------------------------------------------
def flush_to_db(client: mqtt.Client) -> None:
    """Drain the ML buffer, compute average, write to SQLite."""
    global ml_buffer

    with buffer_lock:
        ml_snapshot = ml_buffer.copy()
        ml_buffer.clear()

    ml_avg = compute_ml_average(ml_snapshot)

    if ml_avg is None:
        log.info("Flush: No ML data in buffer — skipping.")
        return

    try:
        conn = get_db_connection()
        ensure_tables(conn)

        now = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """
            INSERT INTO energy_mlprediction
                (timestamp, predicted_energy_wh, upper_bound_wh, lower_bound_wh)
            VALUES (?, ?, ?, ?)
            """,
            (now, ml_avg["predicted_energy_wh"], ml_avg["upper_bound_wh"], ml_avg["lower_bound_wh"]),
        )
        log.info("Flush: Wrote ML average → %s", ml_avg)

        conn.commit()
        conn.close()

    except sqlite3.Error as exc:
        log.error("SQLite error during flush: %s", exc)
        return


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

    if msg.topic == TOPIC_SENSORS:
        # Live sensor data — just acknowledge receipt (no DB storage)
        log.debug("Sensor reading received (pass-through, not stored)")

    elif msg.topic == TOPIC_ML:
        # Normalise ML keys for internal storage
        norm = {
            "predicted_energy_wh": float(payload.get(
                "predicted_energy_wh",
                payload.get("predicted_energy_range", payload.get("predicted_energy_kw", 0))
            )),
            "upper_bound_wh": float(payload.get("upper_bound_energy_wh", 0)),
            "lower_bound_wh": float(payload.get("lower_bound_energy_wh", 0)),
        }

        if norm["predicted_energy_wh"] == 0:
            log.warning("ML payload missing predicted energy value")
            return
        with buffer_lock:
            ml_buffer.append(norm)
        log.debug("Buffered ML prediction (%d in buffer)", len(ml_buffer))


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

    log.info("Starting MQTT Logger (ML flush every %ds)", FLUSH_INTERVAL)
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