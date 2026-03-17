#!/usr/bin/env python3
"""
rule_engine.py — Relay Control Rule Engine
============================================

Subscribes to sensor & ML MQTT topics, evaluates energy-management rules
on a 5-minute cycle, controls 3 GPIO relays, and logs every decision.

Relay Modes:
  A  — Peak Demand   : All relays ON  (Priority 1, 2, 3)
  B  — Average Load  : P1 + P2 ON, P3 OFF
  C  — Baseline Load : P1 ON, P2 + P3 OFF

Run as a systemd service (see systemd/rule-engine.service).
"""

import json
import logging
import os
import signal
import sqlite3
import sys
import threading
from collections import deque
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# GPIO Setup — real RPi.GPIO or mock for development machines
# ---------------------------------------------------------------------------
try:
    import RPi.GPIO as GPIO

    ON_PI = True
except (ImportError, RuntimeError):

    class MockGPIO:
        """Lightweight GPIO mock so rule_engine.py runs on dev machines."""

        BCM = "BCM"
        OUT = "OUT"
        HIGH = 1
        LOW = 0
        _pins: dict[int, int] = {}

        @classmethod
        def setmode(cls, mode):
            logging.getLogger("rule_engine").info(
                "MockGPIO: setmode(%s)",
                mode,
            )

        @classmethod
        def setwarnings(cls, flag):
            pass

        @classmethod
        def setup(cls, pin, mode):
            cls._pins[pin] = cls.LOW
            logging.getLogger("rule_engine").info(
                "MockGPIO: setup(pin=%d, mode=%s)", pin, mode
            )

        @classmethod
        def output(cls, pin, state):
            cls._pins[pin] = state
            state_str = "HIGH (ON)" if state == cls.HIGH else "LOW (OFF)"
            logging.getLogger("rule_engine").info(
                "MockGPIO: pin %d → %s", pin, state_str
            )

        @classmethod
        def cleanup(cls):
            logging.getLogger("rule_engine").info("MockGPIO: cleanup()")

    GPIO = MockGPIO  # type: ignore[misc]
    ON_PI = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_CLIENT_ID = "room-rule-engine"

TOPIC_SENSORS = "room/sensors"
TOPIC_ML = "room/ml/predictions"
TOPIC_RELAY_STATE = "room/relays/state"

# GPIO pins (BCM numbering) — change via env vars or edit here
RELAY_PIN_1 = int(os.environ.get("RELAY_PIN_1", 17))   # Priority 1
RELAY_PIN_2 = int(os.environ.get("RELAY_PIN_2", 27))   # Priority 2
RELAY_PIN_3 = int(os.environ.get("RELAY_PIN_3", 22))   # Priority 3

# Database path
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "room_backend", "db.sqlite3"),
)

# Evaluation interval (seconds) — set to 30*60 for production.
EVAL_INTERVAL = int(os.environ.get("RULE_EVAL_INTERVAL_SECONDS", 2 * 60))

# Configurable max load thresholds in watts.
MODE_A_MAX_W = float(os.environ.get("MODE_A_MAX_W", 2400.0))
MODE_B_MAX_W = float(os.environ.get("MODE_B_MAX_W", 1400.0))
MODE_C_MAX_W = float(os.environ.get("MODE_C_MAX_W", 800.0))

# Temperature bias threshold
TEMP_HOT_THRESHOLD = 28.0

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("rule_engine")

# ---------------------------------------------------------------------------
# Shared state (protected by lock)
# ---------------------------------------------------------------------------
state_lock = threading.Lock()

# Latest sensor readings (updated on every MQTT message)
latest_sensor: dict = {}
# Latest ML prediction
latest_ml: dict = {}

# Rolling battery window: last 3 readings [T-10m, T-5m, T-now].
battery_window: deque[float] = deque(maxlen=3)

# Current mode (persists between evaluations for stability lock)
current_mode: str = "C"  # start in safest mode

# Shutdown flag
shutdown_event = threading.Event()


# ---------------------------------------------------------------------------
# GPIO helpers
# ---------------------------------------------------------------------------
def gpio_init() -> None:
    """Set up GPIO pins as outputs."""
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in (RELAY_PIN_1, RELAY_PIN_2, RELAY_PIN_3):
        GPIO.setup(pin, GPIO.OUT)
    log.info(
        "GPIO initialized: P1=pin%d, P2=pin%d, P3=pin%d",
        RELAY_PIN_1, RELAY_PIN_2, RELAY_PIN_3,
    )


def set_relays(relay_1: bool, relay_2: bool, relay_3: bool) -> None:
    """Drive the three relay GPIO pins."""
    GPIO.output(RELAY_PIN_1, GPIO.HIGH if relay_1 else GPIO.LOW)
    GPIO.output(RELAY_PIN_2, GPIO.HIGH if relay_2 else GPIO.LOW)
    GPIO.output(RELAY_PIN_3, GPIO.HIGH if relay_3 else GPIO.LOW)


def apply_mode(mode: str) -> tuple[bool, bool, bool]:
    """Translate a mode letter into relay states and actuate GPIO.

    Returns (relay_1, relay_2, relay_3) as booleans.
    """
    if mode == "A":
        states = (True, True, True)
    elif mode == "B":
        states = (True, True, False)
    else:  # "C"
        states = (True, False, False)

    set_relays(*states)
    return states


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def ensure_relay_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS energy_relaystate (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT    NOT NULL DEFAULT (datetime('now')),
            mode      TEXT    NOT NULL,
            relay_1   INTEGER NOT NULL,
            relay_2   INTEGER NOT NULL,
            relay_3   INTEGER NOT NULL,
            reason    TEXT    NOT NULL DEFAULT ''
        );
        """
    )
    conn.commit()


def log_decision(mode: str, r1: bool, r2: bool, r3: bool, reason: str) -> None:
    """Write a relay-state decision to the database."""
    try:
        conn = get_db_connection()
        ensure_relay_table(conn)
        conn.execute(
            """
            INSERT INTO energy_relaystate
                (timestamp, mode, relay_1, relay_2, relay_3, reason)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                mode,
                int(r1),
                int(r2),
                int(r3),
                reason,
            ),
        )
        conn.commit()
        conn.close()
    except sqlite3.Error as exc:
        log.error("SQLite error logging decision: %s", exc)


# ---------------------------------------------------------------------------
# Core Rule Engine
# ---------------------------------------------------------------------------
def evaluate_rules() -> tuple[str, str]:
    """Evaluate the rule hierarchy and return (mode, reason).

    Uses the shared state variables under state_lock.
    Must be called while holding state_lock.
    """
    global current_mode

    temperature = latest_sensor.get(
        "temperature", latest_sensor.get("temperature_c", 25.0)
    )
    battery_level = latest_sensor.get("battery_level", 100.0)

    predicted_power_w = latest_ml.get("predicted_power_w")
    predicted_power_kw = latest_ml.get("predicted_power_kw")
    if predicted_power_w is None:
        if predicted_power_kw is not None:
            predicted_power_w = float(predicted_power_kw) * 1000.0
        else:
            # Backward compatibility with legacy kW field.
            predicted_power_w = (
                float(latest_ml.get("predicted_energy_range", 0.0)) * 1000.0
            )

    # Phase 1: Battery Stability Lock (3-reading sliding window)
    # We need at least 3 readings to evaluate the drop
    if len(battery_window) >= 3:
        drop = battery_window[0] - battery_window[-1]  # T-10m minus T-Now
        if drop <= 2.0:
            return current_mode, (
                f"Phase 1 — Stability Lock: battery drop {drop:.1f}% "
                f"(≤2%) over 3 readings → maintaining Mode {current_mode}"
            )

    # Phase 2: Temperature bias
    if temperature > TEMP_HOT_THRESHOLD and battery_level > 40.0:
        return "B", (
            f"Phase 2 — Temperature Bias: "
            f"{temperature}°C > {TEMP_HOT_THRESHOLD}°C "
            f"and battery {battery_level}% > 40% → Mode B (fans allowed)"
        )

    # Phase 3: Wattage-based mode selection.
    if battery_level < 50.0:
        return "C", (
            f"Phase 3 — Low battery guard: {battery_level:.1f}% < 50% → Mode C"
        )

    if predicted_power_w <= MODE_C_MAX_W:
        return "C", (
            f"Phase 3 — Predicted load "
            f"{predicted_power_w:.1f}W <= MODE_C_MAX_W "
            f"{MODE_C_MAX_W:.1f}W → Mode C"
        )
    if predicted_power_w <= MODE_B_MAX_W:
        return "B", (
            f"Phase 3 — Predicted load "
            f"{predicted_power_w:.1f}W <= MODE_B_MAX_W "
            f"{MODE_B_MAX_W:.1f}W → Mode B"
        )
    if predicted_power_w <= MODE_A_MAX_W:
        return "A", (
            f"Phase 3 — Predicted load "
            f"{predicted_power_w:.1f}W <= MODE_A_MAX_W "
            f"{MODE_A_MAX_W:.1f}W → Mode A"
        )

    return "A", (
        f"Phase 3 — Predicted load "
        f"{predicted_power_w:.1f}W exceeds MODE_A_MAX_W "
        f"{MODE_A_MAX_W:.1f}W → Mode A (max-capacity fallback)"
    )


# ---------------------------------------------------------------------------
# Evaluation cycle (runs every EVAL_INTERVAL)
# ---------------------------------------------------------------------------
def run_evaluation(client: mqtt.Client) -> None:
    """Perform one evaluation cycle: read state, decide mode, actuate, log."""
    global current_mode

    with state_lock:
        if not latest_sensor:
            log.info("Evaluation: No sensor data yet — skipping.")
            return

        # Push current battery into the sliding window
        battery_level = latest_sensor.get("battery_level", 100.0)
        battery_window.append(battery_level)
        log.info(
            "Battery window (last 3): %s",
            [round(b, 1) for b in battery_window],
        )

        new_mode, reason = evaluate_rules()

    # Actuate relays
    r1, r2, r3 = apply_mode(new_mode)

    # Update current mode
    with state_lock:
        mode_changed = new_mode != current_mode
        current_mode = new_mode

    change_str = "MODE CHANGED" if mode_changed else "mode unchanged"
    log.info(
        "Evaluation result: Mode %s (%s) — %s",
        new_mode,
        change_str,
        reason,
    )

    # Log decision to database
    log_decision(new_mode, r1, r2, r3, reason)

    # Publish relay state to MQTT for the frontend
    relay_payload = {
        "mode": new_mode,
        "relay_1": r1,
        "relay_2": r2,
        "relay_3": r3,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    client.publish(TOPIC_RELAY_STATE, json.dumps(relay_payload), qos=1)


def evaluation_loop(client: mqtt.Client) -> None:
    """Background thread: run_evaluation every EVAL_INTERVAL."""
    while not shutdown_event.is_set():
        shutdown_event.wait(EVAL_INTERVAL)
        if not shutdown_event.is_set():
            run_evaluation(client)


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

    with state_lock:
        if msg.topic == TOPIC_SENSORS:
            if "temperature" not in payload and "temperature_c" in payload:
                payload["temperature"] = payload["temperature_c"]
            latest_sensor.update(payload)
            log.debug("Updated latest sensor data")
        elif msg.topic == TOPIC_ML:
            latest_ml.update(payload)
            log.debug("Updated latest ML prediction")


def on_disconnect(client, userdata, rc, properties=None):
    if rc != 0:
        log.warning(
            "Unexpected MQTT disconnect (rc=%d). Will auto-reconnect.",
            rc,
        )


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

    log.info("Starting Rule Engine (eval every %ds)", EVAL_INTERVAL)
    log.info(
        "Mode watt limits: A<=%.1fW, B<=%.1fW, C<=%.1fW",
        MODE_A_MAX_W,
        MODE_B_MAX_W,
        MODE_C_MAX_W,
    )
    log.info("Database: %s", os.path.abspath(DB_PATH))
    log.info("Running on Raspberry Pi: %s", ON_PI)

    # Initialize GPIO
    gpio_init()

    # Start in Mode C (safest)
    apply_mode("C")
    log.info("Initial state: Mode C (Baseline Load)")

    # Ensure DB table exists
    conn = get_db_connection()
    ensure_relay_table(conn)
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
        GPIO.cleanup()
        sys.exit(1)

    # Start evaluation thread
    eval_thread = threading.Thread(
        target=evaluation_loop,
        args=(client,),
        daemon=True,
    )
    eval_thread.start()

    # Blocking MQTT loop
    client.loop_start()

    # Wait for shutdown
    shutdown_event.wait()

    # Clean up
    log.info("Shutting down relays (Mode C for safety) …")
    apply_mode("C")
    log_decision(
        "C",
        True,
        False,
        False,
        "Shutdown — forced Mode C for safety",
    )
    GPIO.cleanup()
    client.loop_stop()
    client.disconnect()
    log.info("Rule Engine stopped.")


if __name__ == "__main__":
    main()
