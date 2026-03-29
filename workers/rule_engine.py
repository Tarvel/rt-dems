#!/usr/bin/env python3
"""
rule_engine.py — Relay Control Rule Engine
============================================

Subscribes to sensor & ML MQTT topics, evaluates energy-management rules
on a fixed decision cycle, controls 3 GPIO relays, and logs every decision.

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

from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# GPIO Setup — gpiozero (Pi 5 compatible) or mock for dev machines
# ---------------------------------------------------------------------------
# gpiozero auto-selects the right backend:
#   • Pi 5  → lgpio      (the only lib that works with the RP1 chip)
#   • Pi 4  → RPi.GPIO   (if installed) or lgpio
#   • Dev   → MockFactory (no hardware)
# This replaces the old RPi.GPIO code which is NOT compatible with Pi 5.
from gpiozero import LED, Device

try:
    # If we're on a real Pi, the default pin factory will work.
    # Try creating a throwaway LED to see if real GPIO is available.
    _probe = LED(0, initial_value=False)
    _probe.close()
    ON_PI = True
except Exception:
    # Not on a Pi — fall back to virtual pins for development.
    from gpiozero.pins.mock import MockFactory
    Device.pin_factory = MockFactory()
    ON_PI = False

_log_boot = logging.getLogger("rule_engine")
_log_boot.info("gpiozero pin factory: %s (ON_PI=%s)", Device.pin_factory, ON_PI)

# Relay LED objects are created later (in gpio_init) after pin numbers are read
# from env vars. We store them here so set_relays / cleanup can access them.
relay_leds: dict[int, LED] = {}

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

# Decision interval in minutes.
# ┌─────────────────────────────────────────────────────────────────┐
# │  TESTING DEFAULT: 1 minutes.  Change to 5 for production.       │
# │  export DECISION_INTERVAL_MINUTES=5                             │
# └─────────────────────────────────────────────────────────────────┘
DECISION_INTERVAL_MINUTES = int(
    os.environ.get("DECISION_INTERVAL_MINUTES", 1)
)
DECISION_INTERVAL_SECONDS = DECISION_INTERVAL_MINUTES * 60

# Battery lag tracker: sample every 30 seconds (T-now, T-30s, T-60s).
BATTERY_LAG_INTERVAL_SECONDS = int(
    os.environ.get("BATTERY_LAG_INTERVAL_SECONDS", 30)
)
BATTERY_LAG_READINGS = 3

# Max safe battery drop (%) over the lag window.
# ┌─────────────────────────────────────────────────────────────────┐
# │  TESTING DEFAULT: 2%.  Change to 10 for production.           │
# │  export MAX_BATTERY_DROP_PERCENT=10                           │
# └─────────────────────────────────────────────────────────────────┘
MAX_BATTERY_DROP_PERCENT = float(
    os.environ.get("MAX_BATTERY_DROP_PERCENT", 2)
)


def _format_duration(seconds: int) -> str:
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes}m"
    return f"{seconds}s"


# Peak demand threshold in kW. Condition 1 compares predicted energy
# against this value.
# ┌─────────────────────────────────────────────────────────────────┐
# │  This is the MODE_A ceiling (peak demand load).               │
# │  export MODE_A_MAX_KWH=2.4                                   │
# └─────────────────────────────────────────────────────────────────┘
MODE_A_MAX_KWH = float(
    os.environ.get("MODE_A_MAX_KWH", 2.4)
)

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

# Rolling battery lag: three explicit time slots.
# T-now  = current reading (updated every 30s)
# T-1    = reading from 30 seconds ago
# T-2    = reading from 60 seconds ago
# None means "no reading yet".
battery_t_now: float | None = None
battery_t1:    float | None = None
battery_t2:    float | None = None

# Current mode (persists between evaluations for stability lock)
current_mode: str = "C"  # start in safest mode

# Shutdown flag
shutdown_event = threading.Event()


# ---------------------------------------------------------------------------
# GPIO helpers
# ---------------------------------------------------------------------------
def gpio_init() -> None:
    """Create gpiozero LED objects for each relay pin."""
    for pin in (RELAY_PIN_1, RELAY_PIN_2, RELAY_PIN_3):
        relay_leds[pin] = LED(pin, initial_value=False)
    log.info(
        "GPIO initialized (gpiozero): P1=pin%d, P2=pin%d, P3=pin%d",
        RELAY_PIN_1, RELAY_PIN_2, RELAY_PIN_3,
    )


def set_relays(relay_1: bool, relay_2: bool, relay_3: bool) -> None:
    """Drive the three relay GPIO pins via gpiozero LEDs."""
    for pin, state in (
        (RELAY_PIN_1, relay_1),
        (RELAY_PIN_2, relay_2),
        (RELAY_PIN_3, relay_3),
    ):
        led = relay_leds[pin]
        if state:
            led.on()
        else:
            led.off()
        log.debug("Pin %d → %s", pin, "ON" if state else "OFF")


def gpio_cleanup() -> None:
    """Close all gpiozero LED objects (releases GPIO pins)."""
    for pin, led in relay_leds.items():
        led.off()
        led.close()
    relay_leds.clear()
    log.info("GPIO cleaned up (all pins released)")


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
    """Evaluate the decision pipeline and return (mode, reason).

    Decision tree
    ─────────────
    Step 1: predicted_energy >= MODE_A_MAX_KWH  (energy is sufficient)
        ├─ Battery >= 80%  → lag stable? → A / B
        ├─ Battery >= 50%  → lag stable? → B / C
        └─ Battery <  50%  → C

    Step 2: predicted_energy <  MODE_A_MAX_KWH  (energy is tight)
        ├─ Battery >= 80%  → lag floor ok? → A / B
        ├─ Battery >= 60%  → lag floor ok? → B / C
        └─ Battery <  60%  → C
    """
    global current_mode

    # ── Gather sensor + ML values ────────────────────────────────
    temperature = latest_sensor.get(
        "temperature", latest_sensor.get("temperature_c", 25.0)
    )
    humidity = latest_sensor.get("humidity")
    lux = latest_sensor.get("lux")
    occupancy = latest_sensor.get("occupancy")
    battery_level = latest_sensor.get("battery_level", 100.0)

    # Resolve predicted energy from whichever key the ML payload has.
    predicted_energy = latest_ml.get("predicted_energy_kw")
    if predicted_energy is None:
        predicted_energy = latest_ml.get("predicted_energy_range")
    if predicted_energy is None:
        predicted_energy = latest_ml.get("predicted_energy_kwh")

    log.info(
        "Env snapshot: temp=%.1f°C, humidity=%s, lux=%s, "
        "occupancy=%s, battery=%.1f%%",
        float(temperature),
        "n/a" if humidity is None else f"{humidity}",
        "n/a" if lux is None else f"{lux}",
        "n/a" if occupancy is None else f"{occupancy}",
        float(battery_level),
    )

    if predicted_energy is None:
        return current_mode, (
            "Decision skipped: no ML prediction available "
            "→ maintaining current mode"
        )

    predicted_energy = float(predicted_energy)

    # ── Lag helpers ──────────────────────────────────────────────
    # "Full lag" means we have readings in all three slots.
    has_full_lag = (battery_t_now is not None
                    and battery_t1 is not None
                    and battery_t2 is not None)
    # Drop = T-2 minus T-now (positive means battery fell)
    lag_drop = (battery_t2 - battery_t_now) if has_full_lag else 0.0

    def _lag_info() -> str:
        """Short string describing the lag window state."""
        if not has_full_lag:
            return "lag window not full yet (treated as stable)"
        return (
            f"lag drop={lag_drop:.2f}%, "
            f"T-now={battery_t_now:.1f}% T-1={battery_t1:.1f}% T-2={battery_t2:.1f}%"
        )

    # ── STEP 1: predicted_energy >= peak demand (energy sufficient) ──
    if predicted_energy >= MODE_A_MAX_KWH:
        step = (
            f"Step 1 — Predicted {predicted_energy:.4f}kW "
            f">= peak demand {MODE_A_MAX_KWH}kW"
        )
        lag_stable = (not has_full_lag) or (lag_drop <= MAX_BATTERY_DROP_PERCENT)

        # 1 — Battery >= 80%
        if battery_level >= 80.0:
            if lag_stable:
                return "A", (
                    f"{step}; Battery {battery_level:.1f}% >= 80%, "
                    f"lag stable ({_lag_info()}) → Mode A"
                )
            else:
                return "B", (
                    f"{step}; Battery {battery_level:.1f}% >= 80%, "
                    f"lag NOT stable ({_lag_info()}) → Mode B"
                )

        # 1 — Battery >= 50%
        if battery_level >= 50.0:
            if lag_stable:
                return "B", (
                    f"{step}; Battery {battery_level:.1f}% >= 50%, "
                    f"lag stable ({_lag_info()}) → Mode B"
                )
            else:
                return "C", (
                    f"{step}; Battery {battery_level:.1f}% >= 50%, "
                    f"lag NOT stable ({_lag_info()}) → Mode C"
                )

        # 1 — Battery < 50%
        return "C", (
            f"{step}; Battery {battery_level:.1f}% < 50% → Mode C"
        )

    # ── STEP 2: predicted_energy < peak demand (energy is tight) ─────
    step = (
        f"Step 2 — Predicted {predicted_energy:.4f}kW "
        f"< peak demand {MODE_A_MAX_KWH}kW"
    )
    lag_stable = (not has_full_lag) or (lag_drop <= MAX_BATTERY_DROP_PERCENT)

    # 2a — Battery >= 80%
    if battery_level >= 80.0:
        if lag_stable:
            return "A", (
                f"{step}; Battery {battery_level:.1f}% >= 80%, "
                f"lag stable ({_lag_info()}) → Mode A"
            )
        else:
            return "B", (
                f"{step}; Battery {battery_level:.1f}% >= 80%, "
                f"lag NOT stable ({_lag_info()}) → Mode B"
            )

    # 2b — Battery >= 60%
    if battery_level >= 60.0:
        if lag_stable:
            return "B", (
                f"{step}; Battery {battery_level:.1f}% >= 60%, "
                f"lag stable ({_lag_info()}) → Mode B"
            )
        else:
            return "C", (
                f"{step}; Battery {battery_level:.1f}% >= 60%, "
                f"lag NOT stable ({_lag_info()}) → Mode C"
            )

    # 2bii — Battery < 60%
    return "C", (
        f"{step}; Battery {battery_level:.1f}% < 60% → Mode C"
    )


# ---------------------------------------------------------------------------
# Decision cycle (runs every DECISION_INTERVAL_SECONDS)
# ---------------------------------------------------------------------------
def run_evaluation(client: mqtt.Client) -> None:
    """Perform one evaluation cycle: read state, decide mode, actuate, log."""
    global current_mode

    with state_lock:
        if not latest_sensor:
            log.info("Evaluation: No sensor data yet — skipping.")
            return

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
        "battery_t_now": round(battery_t_now, 1) if battery_t_now is not None else None,
        "battery_t1":    round(battery_t1, 1)    if battery_t1 is not None else None,
        "battery_t2":    round(battery_t2, 1)    if battery_t2 is not None else None,
        "battery_lag_drop": (
            round(battery_t2 - battery_t_now, 2)
            if battery_t_now is not None and battery_t2 is not None
            else None
        ),
        "battery_lag_interval_seconds": BATTERY_LAG_INTERVAL_SECONDS,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    client.publish(TOPIC_RELAY_STATE, json.dumps(relay_payload), qos=1)


def evaluation_loop(client: mqtt.Client) -> None:
    """Background thread: run_evaluation every decision interval."""
    while not shutdown_event.is_set():
        shutdown_event.wait(DECISION_INTERVAL_SECONDS)
        if not shutdown_event.is_set():
            run_evaluation(client)


def battery_lag_loop(client: mqtt.Client) -> None:
    """Background thread: shift battery readings every 30 seconds.

    Every 30s:  T-1 → T-2,  T-now → T-1,  fresh reading → T-now
    """
    global battery_t_now, battery_t1, battery_t2

    while not shutdown_event.is_set():
        shutdown_event.wait(BATTERY_LAG_INTERVAL_SECONDS)
        if shutdown_event.is_set():
            break
        with state_lock:
            if not latest_sensor:
                continue
            fresh = latest_sensor.get("battery_level")
            if fresh is None:
                continue
            fresh = float(fresh)

            # ── 30-second shift ──
            battery_t2    = battery_t1       # old T-1 becomes T-2
            battery_t1    = battery_t_now    # old T-now becomes T-1
            battery_t_now = fresh            # fresh reading is T-now

            log.info(
                "Battery lag shift → T-now: %.1f%%  T-1: %s  T-2: %s",
                battery_t_now,
                f"{battery_t1:.1f}%" if battery_t1 is not None else "--",
                f"{battery_t2:.1f}%" if battery_t2 is not None else "--",
            )

            # Publish updated lag to dashboard immediately
            lag_payload = {
                "type": "battery_lag_update",
                "battery_t_now": round(float(battery_t_now), 1) if battery_t_now is not None else None,
                "battery_t1":    round(float(battery_t1), 1) if battery_t1 is not None else None,
                "battery_t2":    round(float(battery_t2), 1) if battery_t2 is not None else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            # We use the same topic the dashboard already listens to for state,
            # but the dashboard must be updated to handle this "type" of payload.
            client.publish(TOPIC_RELAY_STATE, json.dumps(lag_payload), qos=1)


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
            log.debug(
                "ML prediction received: keys=%s",
                sorted(payload.keys()),
            )


def on_disconnect(client, userdata, *args, **kwargs):
    # paho-mqtt v2 may pass extra positional args (flags, rc, properties).
    rc = args[0] if args else 0
    if rc != 0:
        log.warning(
            "Unexpected MQTT disconnect (rc=%s). Will auto-reconnect.",
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

    log.info("Starting Rule Engine")
    log.info(
        "Decision interval: %ds (%s)",
        DECISION_INTERVAL_SECONDS,
        _format_duration(DECISION_INTERVAL_SECONDS),
    )
    log.info(
        "Battery lag tracker: %ds interval (%d readings)",
        BATTERY_LAG_INTERVAL_SECONDS,
        BATTERY_LAG_READINGS,
    )
    log.info(
        "Peak demand threshold (MODE_A_MAX_KWH): %.1f kW",
        MODE_A_MAX_KWH,
    )
    log.info(
        "Max battery drop threshold: %.1f%%",
        MAX_BATTERY_DROP_PERCENT,
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
        gpio_cleanup()
        sys.exit(1)

    # Start evaluation thread
    eval_thread = threading.Thread(
        target=evaluation_loop,
        args=(client,),
        daemon=True,
    )
    eval_thread.start()

    # Start battery lag tracker thread
    lag_thread = threading.Thread(
        target=battery_lag_loop,
        args=(client,),
        daemon=True,
    )
    lag_thread.start()

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
    gpio_cleanup()
    client.loop_stop()
    client.disconnect()
    log.info("Rule Engine stopped.")


if __name__ == "__main__":
    main()
