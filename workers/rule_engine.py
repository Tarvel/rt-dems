#!/usr/bin/env python3
"""
rule_engine.py — Relay Control Rule Engine
============================================

Subscribes to sensor & ML MQTT topics, evaluates energy-management rules
on a fixed decision cycle, and publishes relay state decisions via MQTT.

An external ESP32 subscribes to room/relays/state and drives the physical
relay GPIO pins based on the relay_1, relay_2, relay_3 booleans in the
published payload.  This engine does NOT touch any local GPIO.

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
# Configuration
# ---------------------------------------------------------------------------
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_CLIENT_ID = "room-rule-engine"

TOPIC_SENSORS = "room/sensors"
TOPIC_ML = "room/ml/predictions"
TOPIC_RELAY_STATE = "room/relays/state"
TOPIC_OVERRIDE = "room/control/override"

# Database path
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "room_backend", "db.sqlite3"),
)

# Decision interval in minutes.
DECISION_INTERVAL_MINUTES = float(
    os.environ.get("DECISION_INTERVAL_MINUTES", 3)
)
DECISION_INTERVAL_SECONDS = int(DECISION_INTERVAL_MINUTES * 60)

# Battery lag tracker: sample every 30 seconds (T-now, T-1, T-2).
BATTERY_LAG_INTERVAL_SECONDS = int(
    os.environ.get("BATTERY_LAG_INTERVAL_SECONDS", 30)
)
BATTERY_LAG_READINGS = 3

# Max safe battery drop (%) over the lag window (T-2 → T-now).
# Daytime (solar window) uses a strict threshold; nighttime is wider
# to avoid false instability flags when battery naturally drains.
MAX_BATTERY_DROP_PERCENT = float(
    os.environ.get("MAX_BATTERY_DROP_PERCENT", 2)
)
MAX_BATTERY_DROP_NIGHT_PERCENT = float(
    os.environ.get("MAX_BATTERY_DROP_NIGHT_PERCENT", 8)
)

# Solar window boundaries (24-hour format).
# Daytime = SOLAR_HOUR_START..SOLAR_HOUR_END-1 (inclusive hours).
SOLAR_HOUR_START = int(os.environ.get("SOLAR_HOUR_START", 11))
SOLAR_HOUR_END   = int(os.environ.get("SOLAR_HOUR_END",   16))


def _active_battery_threshold() -> tuple[float, str]:
    """Return (threshold_pct, profile_name) based on current hour."""
    hour = datetime.now().hour
    if SOLAR_HOUR_START <= hour < SOLAR_HOUR_END:
        return MAX_BATTERY_DROP_PERCENT, "daytime"
    return MAX_BATTERY_DROP_NIGHT_PERCENT, "nighttime"


# Peak demand threshold in Wh. The core decision compares
# predicted_energy_wh against this value.
PEAK_DEMAND_WH = float(
    os.environ.get("PEAK_DEMAND_WH", 2.4)
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
# T-now  = current reading (updated every 60s)
# T-1    = reading from 60 seconds ago
# T-2    = reading from 120 seconds ago
# None means "no reading yet".
battery_t_now: float | None = None
battery_t1:    float | None = None
battery_t2:    float | None = None

# Current mode (persists between evaluations for stability lock)
current_mode: str = "C"  # start in safest mode

# ── Manual override state ──
# When True, the evaluation loop skips automatic decisions.
# The dashboard controls modes/relays directly via room/control/override.
manual_override: bool = False
manual_mode: str = "C"
manual_relays: tuple[bool, bool, bool] = (True, False, False)

# Shutdown flag
shutdown_event = threading.Event()


# ---------------------------------------------------------------------------
# Mode → relay-state mapping (pure logic, no hardware)
# ---------------------------------------------------------------------------
def apply_mode(mode: str) -> tuple[bool, bool, bool]:
    """Translate a mode letter into relay state booleans.

    No local GPIO is driven — the returned values are published
    via MQTT for the ESP32 relay controller to consume.

    Returns (relay_1, relay_2, relay_3).
    """
    if mode == "A":
        return (True, True, True)
    elif mode == "B":
        return (True, True, False)
    else:  # "C"
        return (True, False, False)


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
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT    NOT NULL DEFAULT (datetime('now')),
            mode            TEXT    NOT NULL,
            relay_1         INTEGER NOT NULL,
            relay_2         INTEGER NOT NULL,
            relay_3         INTEGER NOT NULL,
            reason          TEXT    NOT NULL DEFAULT '',
            temperature     REAL    NOT NULL DEFAULT 0.0,
            humidity        REAL    NOT NULL DEFAULT 0.0,
            lux             REAL    NOT NULL DEFAULT 0.0,
            occupancy       INTEGER NOT NULL DEFAULT 0,
            energy_kw       REAL    NOT NULL DEFAULT 0.0,
            battery_level   REAL    NOT NULL DEFAULT 0.0,
            battery_voltage REAL    NOT NULL DEFAULT 0.0
        );
        """
    )
    # Add columns to existing tables (safe — SQLite ignores if they exist)
    for col, coltype in [
        ("temperature", "REAL DEFAULT 0.0"),
        ("humidity", "REAL DEFAULT 0.0"),
        ("lux", "REAL DEFAULT 0.0"),
        ("occupancy", "INTEGER DEFAULT 0"),
        ("energy_kw", "REAL DEFAULT 0.0"),
        ("battery_level", "REAL DEFAULT 0.0"),
        ("battery_voltage", "REAL DEFAULT 0.0"),
    ]:
        try:
            conn.execute(f"ALTER TABLE energy_relaystate ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()


def log_decision(mode: str, r1: bool, r2: bool, r3: bool, reason: str) -> None:
    """Write a relay-state decision to the database, including a sensor snapshot."""
    # Grab sensor snapshot under lock
    with state_lock:
        snap = {
            "temperature": float(latest_sensor.get("temperature", 0.0)),
            "humidity": float(latest_sensor.get("humidity", 0.0)),
            "lux": float(latest_sensor.get("lux", 0.0)),
            "occupancy": int(latest_sensor.get("occupancy", 0)),
            "energy_kw": float(latest_sensor.get("energy_kw", 0.0)),
            "battery_level": float(latest_sensor.get("battery_level", 0.0)),
            "battery_voltage": float(latest_sensor.get("battery_voltage", 0.0)),
        }

    try:
        conn = get_db_connection()
        ensure_relay_table(conn)
        conn.execute(
            """
            INSERT INTO energy_relaystate
                (timestamp, mode, relay_1, relay_2, relay_3, reason,
                 temperature, humidity, lux, occupancy,
                 energy_kw, battery_level, battery_voltage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                mode,
                int(r1),
                int(r2),
                int(r3),
                reason,
                snap["temperature"],
                snap["humidity"],
                snap["lux"],
                snap["occupancy"],
                snap["energy_kw"],
                snap["battery_level"],
                snap["battery_voltage"],
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
    Step 1: predicted_energy > PEAK_DEMAND_WH
        ├─ Battery >= 80%  → lag stable? → A / B
        ├─ Battery >= 50%  → lag stable? → B / C
        └─ Battery <  50%  → C

    Step 2: predicted_energy <= PEAK_DEMAND_WH  (Class A PROHIBITED)
        ├─ Battery >= 60%  → lag stable? → B / C
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

    # Resolve predicted energy from ML payload
    predicted_energy = latest_ml.get("predicted_energy_wh")
    if predicted_energy is None:
        predicted_energy = latest_ml.get("predicted_energy_kw")
    if predicted_energy is None:
        predicted_energy = latest_ml.get("predicted_energy_range")

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
    has_full_lag = (battery_t_now is not None
                    and battery_t1 is not None
                    and battery_t2 is not None)
    # Drop = T-2 minus T-now (positive means battery fell)
    lag_drop = (battery_t2 - battery_t_now) if has_full_lag else 0.0
    lag_threshold, lag_profile = _active_battery_threshold()
    lag_stable = (not has_full_lag) or (lag_drop <= lag_threshold)

    def _lag_info() -> str:
        if not has_full_lag:
            return "lag window not full yet (treated as stable)"
        return (
            f"drop={lag_drop:.2f}% (max={lag_threshold}% {lag_profile}), "
            f"T-now={battery_t_now:.1f}% T-1={battery_t1:.1f}% T-2={battery_t2:.1f}%"
        )

    # ══════════════════════════════════════════════════════════════
    # STEP 1: predicted_energy > PEAK_DEMAND_WH
    # ══════════════════════════════════════════════════════════════
    if predicted_energy > PEAK_DEMAND_WH:
        step = (
            f"Step 1 — predicted {predicted_energy:.4f}Wh "
            f"> peak demand {PEAK_DEMAND_WH}Wh"
        )

        # 1a — Battery >= 80%
        if battery_level >= 80.0:
            if lag_stable:
                return "A", (
                    f"{step}; Battery {battery_level:.1f}% >= 80%, "
                    f"lag stable ({_lag_info()}) → Class A"
                )
            else:
                return "B", (
                    f"{step}; Battery {battery_level:.1f}% >= 80%, "
                    f"lag NOT stable ({_lag_info()}) → Class B"
                )

        # 1b — Battery >= 50%
        if battery_level >= 50.0:
            if lag_stable:
                return "B", (
                    f"{step}; Battery {battery_level:.1f}% >= 50%, "
                    f"lag stable ({_lag_info()}) → Class B"
                )
            else:
                return "C", (
                    f"{step}; Battery {battery_level:.1f}% >= 50%, "
                    f"lag NOT stable ({_lag_info()}) → Class C"
                )

        # 1bii — Battery < 50%
        return "C", (
            f"{step}; Battery {battery_level:.1f}% < 50% → Class C"
        )

    # ══════════════════════════════════════════════════════════════
    # STEP 2: predicted_energy <= PEAK_DEMAND_WH  (Class A PROHIBITED)
    # ══════════════════════════════════════════════════════════════
    step = (
        f"Step 2 — predicted {predicted_energy:.4f}Wh "
        f"<= peak demand {PEAK_DEMAND_WH}Wh (Class A prohibited)"
    )

    # 2a — Battery >= 60%
    if battery_level >= 60.0:
        if lag_stable:
            return "B", (
                f"{step}; Battery {battery_level:.1f}% >= 60%, "
                f"lag stable ({_lag_info()}) → Class B"
            )
        else:
            return "C", (
                f"{step}; Battery {battery_level:.1f}% >= 60%, "
                f"lag NOT stable ({_lag_info()}) → Class C"
            )

    # 2b — Battery < 60%
    return "C", (
        f"{step}; Battery {battery_level:.1f}% < 60% → Class C"
    )


# ---------------------------------------------------------------------------
# Decision cycle (runs every DECISION_INTERVAL_SECONDS)
# ---------------------------------------------------------------------------
def run_evaluation(client: mqtt.Client) -> None:
    """Perform one evaluation cycle: read state, decide mode, actuate, log."""
    global current_mode, manual_override

    # ── Manual override: skip automatic decision ──
    with state_lock:
        if manual_override:
            log.debug("Evaluation: Manual override active — skipping auto.")
            return

    with state_lock:
        if not latest_sensor:
            log.info("Evaluation: No sensor data yet — skipping.")
            return

        new_mode, reason = evaluate_rules()

        # Grab ML data for logging
        ml_predicted = latest_ml.get("predicted_energy_wh", "n/a")
        ml_upper = latest_ml.get("upper_bound_energy_wh", "n/a")
        ml_lower = latest_ml.get("lower_bound_energy_wh", "n/a")

    # Compute relay states (published via MQTT — ESP32 actuates)
    r1, r2, r3 = apply_mode(new_mode)

    # Update current mode
    with state_lock:
        mode_changed = new_mode != current_mode
        current_mode = new_mode

    change_str = "⚡ CLASS CHANGED" if mode_changed else "class unchanged"
    log.info(
        "━━━ Decision: Class %s (%s)  R1=%s R2=%s R3=%s",
        new_mode, change_str,
        "ON" if r1 else "OFF",
        "ON" if r2 else "OFF",
        "ON" if r3 else "OFF",
    )
    log.info(
        "    ML prediction: %.4f Wh  [lower=%.4f, upper=%.4f]  peak_demand=%.1f Wh",
        float(ml_predicted) if ml_predicted != "n/a" else 0.0,
        float(ml_lower) if ml_lower != "n/a" else 0.0,
        float(ml_upper) if ml_upper != "n/a" else 0.0,
        PEAK_DEMAND_WH,
    )
    log.info("    Reason: %s", reason)

    # Log decision to database
    log_decision(new_mode, r1, r2, r3, reason)

    # Publish relay state to MQTT for the frontend
    _publish_relay_state(client, new_mode, r1, r2, r3, reason, auto=True)


def _publish_relay_state(
    client: mqtt.Client,
    mode: str,
    r1: bool, r2: bool, r3: bool,
    reason: str,
    auto: bool = True,
) -> None:
    """Publish current relay state to MQTT (used by both auto and manual paths)."""
    relay_payload = {
        "mode": mode,
        "relay_1": r1,
        "relay_2": r2,
        "relay_3": r3,
        "auto": auto,
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
        "timestamp": latest_sensor.get("timestamp", datetime.now(timezone.utc).isoformat()),
    }
    client.publish(TOPIC_RELAY_STATE, json.dumps(relay_payload), qos=1)


def evaluation_loop(client: mqtt.Client) -> None:
    """Background thread: run_evaluation every decision interval."""
    while not shutdown_event.is_set():
        shutdown_event.wait(DECISION_INTERVAL_SECONDS)
        if not shutdown_event.is_set():
            run_evaluation(client)


def battery_lag_loop(client: mqtt.Client) -> None:
    """Background thread: shift battery readings every 60 seconds.

    Every 60s:  T-1 → T-2,  T-now → T-1,  fresh reading → T-now
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

            # ── 60-second shift ──
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
                "timestamp": latest_sensor.get("timestamp", datetime.now(timezone.utc).isoformat()),
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
        client.subscribe([
            (TOPIC_SENSORS, 1),
            (TOPIC_ML, 1),
            (TOPIC_OVERRIDE, 1),
        ])
        log.info(
            "Subscribed to %s, %s, %s",
            TOPIC_SENSORS, TOPIC_ML, TOPIC_OVERRIDE,
        )
    else:
        log.error("MQTT connection failed with code %d", rc)


def _handle_override(client, payload: dict) -> None:
    """Handle manual override commands from the dashboard.

    Expected payloads:

    Enable override + set mode:
        {"auto": false, "mode": "A"}
        {"auto": false, "mode": "B"}
        {"auto": false, "mode": "C"}

    Enable override + set individual relays:
        {"auto": false, "relay_1": true, "relay_2": false, "relay_3": true}

    Re-enable auto management:
        {"auto": true}
    """
    global manual_override, manual_mode, manual_relays, current_mode

    auto = payload.get("auto", True)

    with state_lock:
        if auto:
            # ── Re-enable auto management ──
            manual_override = False
            log.info("Manual override DISABLED — auto management resumed")
            return

        # ── Enable manual override ──
        manual_override = True

        # Mode-based override: {"auto": false, "mode": "A"}
        if "mode" in payload:
            mode = payload["mode"].upper()
            if mode not in ("A", "B", "C"):
                log.warning("Invalid manual mode: %s — ignoring", mode)
                return
            manual_mode = mode
            manual_relays = apply_mode(mode)
            current_mode = mode
            reason = f"Manual override → Mode {mode}"

        # Relay-based override: {"auto": false, "relay_1": true, ...}
        elif any(k in payload for k in ("relay_1", "relay_2", "relay_3")):
            r1 = bool(payload.get("relay_1", manual_relays[0]))
            r2 = bool(payload.get("relay_2", manual_relays[1]))
            r3 = bool(payload.get("relay_3", manual_relays[2]))
            manual_relays = (r1, r2, r3)
            manual_mode = "MANUAL"
            current_mode = "MANUAL"
            reason = f"Manual relay override → R1={r1}, R2={r2}, R3={r3}"
        else:
            reason = "Manual override enabled (no mode/relay specified)"

    r1, r2, r3 = manual_relays
    log.info("MANUAL: %s  [R1=%s R2=%s R3=%s]", reason, r1, r2, r3)

    # Log to database
    log_decision(manual_mode, r1, r2, r3, reason)

    # Publish to room/relays/state so ESP32 + dashboard update
    _publish_relay_state(client, manual_mode, r1, r2, r3, reason, auto=False)


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("Bad payload on %s: %s", msg.topic, exc)
        return

    # ── Manual override commands ──
    if msg.topic == TOPIC_OVERRIDE:
        _handle_override(client, payload)
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
        "Decision interval: %ds (%.0fm)",
        DECISION_INTERVAL_SECONDS,
        DECISION_INTERVAL_MINUTES,
    )
    log.info(
        "Battery lag tracker: %ds interval (%d readings)",
        BATTERY_LAG_INTERVAL_SECONDS,
        BATTERY_LAG_READINGS,
    )
    log.info(
        "Peak demand threshold (PEAK_DEMAND_WH): %.1f Wh",
        PEAK_DEMAND_WH,
    )
    log.info(
        "Max battery drop: %.1f%% (day %d:00-%d:00) / %.1f%% (night)",
        MAX_BATTERY_DROP_PERCENT,
        SOLAR_HOUR_START,
        SOLAR_HOUR_END,
        MAX_BATTERY_DROP_NIGHT_PERCENT,
    )
    log.info("Database: %s", os.path.abspath(DB_PATH))
    log.info("GPIO: disabled (ESP32 handles relay actuation via MQTT)")

    # Start in Mode C (safest) — no GPIO, just internal state
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

    # Clean up — publish Mode C so the ESP32 drops to safe state
    log.info("Publishing Mode C (safety shutdown) to MQTT …")
    r1, r2, r3 = apply_mode("C")
    shutdown_payload = {
        "mode": "C",
        "relay_1": r1,
        "relay_2": r2,
        "relay_3": r3,
        "reason": "Shutdown — forced Mode C for safety",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    client.publish(TOPIC_RELAY_STATE, json.dumps(shutdown_payload), qos=1)
    log_decision("C", r1, r2, r3, "Shutdown — forced Mode C for safety")
    client.loop_stop()
    client.disconnect()
    log.info("Rule Engine stopped.")


if __name__ == "__main__":
    main()