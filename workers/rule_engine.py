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
import urllib.request
import urllib.error

from datetime import datetime, timezone

import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_CLIENT_ID = "room-rule-engine"

TOPIC_SENSORS = "room/sensors"
TOPIC_ML = "room/ml/predictions"  # Published TO (for logger/frontend)
TOPIC_RELAY_STATE = "room/relays/state"
TOPIC_OVERRIDE = "room/control/override"

# ML Service HTTP endpoint (single-timer: rule engine calls ML directly)
ML_SERVICE_URL = os.environ.get(
    "ML_SERVICE_URL", "http://localhost:5000/predict"
)

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

# ── EDFI Load Thresholds (Wh) ──
# The predicted energy (EDFI) is compared against these to classify load.
#   EDFI >= PEAK       → Peak Load
#   MODERATE <= EDFI   → Moderate Load
#   BASELINE <= EDFI   → Baseline Load
#   EDFI < BASELINE    → Very Low Load
PEAK_THRESHOLD = float(os.environ.get("PEAK_THRESHOLD", 80))
MODERATE_THRESHOLD = float(os.environ.get("MODERATE_THRESHOLD", 50))
BASELINE_THRESHOLD = float(os.environ.get("BASELINE_THRESHOLD", 30))

# Sensor averaging window (seconds).
# Group 2 trained the new model on 5-minute averaged readings.
SENSOR_AVG_WINDOW_SECONDS = int(os.environ.get("SENSOR_AVG_WINDOW_SECONDS", 300))

# How often to run a prediction when sensor data arrives (seconds).
# Group 2 wants "real-time" predictions, but calling the ML service on
# every single sensor message (~2 Hz) would overload it.  This throttle
# ensures we predict at most once per PREDICTION_RATE_LIMIT_SECONDS.
PREDICTION_RATE_LIMIT_SECONDS = float(
    os.environ.get("PREDICTION_RATE_LIMIT_SECONDS", 5)
)


# ---------------------------------------------------------------------------
# Battery stability check
# ---------------------------------------------------------------------------
def battery_stable(levels: list[float | None], threshold: float) -> bool:
    """Return True if ALL battery lag readings are >= threshold.

    If any reading is None (lag window not full), that slot is
    treated as meeting the threshold so early decisions aren't blocked.
    """
    for level in levels:
        if level is not None and level < threshold:
            return False
    return True

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

# 20-minute rolling sensor buffer for averaging.
# Each entry is a dict with a "_ts" key (epoch float) + sensor values.
import time as _time_mod
sensor_buffer: list[dict] = []

# Timestamp of last continuous prediction (for rate limiting).
_last_prediction_epoch: float = 0.0

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
    elif mode == "C":
        return (True, False, False)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
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
        log.warning("SQLite error logging decision (attempt 1): %s — retrying", exc)
        import time as _time
        _time.sleep(0.5)
        try:
            conn = get_db_connection()
            ensure_relay_table(conn)
            conn.execute(
                "INSERT INTO energy_relaystate "
                "(mode, relay_1, relay_2, relay_3, reason, "
                " temperature, humidity, lux, occupancy, energy_kw, "
                " battery_level, battery_voltage) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    mode, int(r1), int(r2), int(r3), reason,
                    snap["temperature"], snap["humidity"],
                    snap["lux"], snap["occupancy"], snap["energy_kw"],
                    snap["battery_level"], snap["battery_voltage"],
                ),
            )
            conn.commit()
            conn.close()
        except sqlite3.Error as exc2:
            log.error("SQLite error logging decision (attempt 2): %s", exc2)


# ---------------------------------------------------------------------------
# Core Rule Engine
# ---------------------------------------------------------------------------
def evaluate_rules() -> tuple[str, str]:
    """Evaluate the decision pipeline and return (mode, reason).

    EDFI = predicted energy (Energy Demand Forecast Interval).
    Battery stability = ALL 3 lag readings (T-now, T-1, T-2) >= threshold.

    Decision tree
    ─────────────
    PEAK LOAD:     EDFI >= 80
        ├─ battery_stable(80%) → Smart A
        ├─ battery_stable(60%) → Smart B
        └─ else               → Smart C

    MODERATE LOAD: 50 <= EDFI < 80
        ├─ battery_stable(60%) → Smart B
        └─ else               → Smart C

    BASELINE LOAD: 30 <= EDFI < 50
        └─ Always Smart C

    VERY LOW LOAD: EDFI < 30
        └─ Smart C
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

    # Resolve predicted energy (EDFI) from ML payload
    predicted_energy = latest_ml.get("predicted_energy_wh")
    if predicted_energy is None:
        predicted_energy = latest_ml.get("predicted_energy_kw")
    if predicted_energy is None:
        predicted_energy = latest_ml.get("predicted_energy_range")


    if predicted_energy is None:
        return current_mode, (
            "Decision skipped: no ML prediction available "
            "→ maintaining current mode"
        )

    edfi = float(predicted_energy)

    # ── Battery lag levels for stability check ───────────────────
    bat_levels = [battery_t_now, battery_t1, battery_t2]

    def _bat_info() -> str:
        vals = [
            f"T-now={battery_t_now:.1f}%" if battery_t_now is not None else "T-now=--",
            f"T-1={battery_t1:.1f}%" if battery_t1 is not None else "T-1=--",
            f"T-2={battery_t2:.1f}%" if battery_t2 is not None else "T-2=--",
        ]
        return ", ".join(vals)

    # ══════════════════════════════════════════════════════════════
    # PEAK LOAD: EDFI >= PEAK_THRESHOLD
    # ══════════════════════════════════════════════════════════════
    if edfi >= PEAK_THRESHOLD:
        load = f"PEAK LOAD (EDFI {edfi:.2f} >= {PEAK_THRESHOLD})"

        if battery_stable(bat_levels, 80):
            return "A", (
                f"{load}; battery_stable(80%) = True ({_bat_info()}) → Smart A"
            )
        elif battery_stable(bat_levels, 60):
            return "B", (
                f"{load}; battery_stable(60%) = True ({_bat_info()}) → Smart B"
            )
        else:
            return "C", (
                f"{load}; battery_stable(60%) = False ({_bat_info()}) → Smart C"
            )

    # ══════════════════════════════════════════════════════════════
    # MODERATE LOAD: MODERATE_THRESHOLD <= EDFI < PEAK_THRESHOLD
    # ══════════════════════════════════════════════════════════════
    if edfi >= MODERATE_THRESHOLD:
        load = f"MODERATE LOAD (EDFI {edfi:.2f}, {MODERATE_THRESHOLD} <= x < {PEAK_THRESHOLD})"

        if battery_stable(bat_levels, 60):
            return "B", (
                f"{load}; battery_stable(60%) = True ({_bat_info()}) → Smart B"
            )
        else:
            return "C", (
                f"{load}; battery_stable(60%) = False ({_bat_info()}) → Smart C"
            )

    # ══════════════════════════════════════════════════════════════
    # BASELINE LOAD: BASELINE_THRESHOLD <= EDFI < MODERATE_THRESHOLD
    # ══════════════════════════════════════════════════════════════
    if edfi >= BASELINE_THRESHOLD:
        return "C", (
            f"BASELINE LOAD (EDFI {edfi:.2f}, {BASELINE_THRESHOLD} <= x < {MODERATE_THRESHOLD}) → Smart C"
        )

    # ══════════════════════════════════════════════════════════════
    # VERY LOW LOAD: EDFI < BASELINE_THRESHOLD
    # ══════════════════════════════════════════════════════════════
    return "C", (
        f"VERY LOW LOAD (EDFI {edfi:.2f} < {BASELINE_THRESHOLD}) → Smart C"
    )


# ---------------------------------------------------------------------------
# ML prediction fetcher (HTTP call to FastAPI ML service)
# ---------------------------------------------------------------------------
def _fetch_prediction(sensor: dict) -> dict | None:
    """Call the ML service and return the prediction payload.

    Returns a dict with predicted_energy_wh, upper/lower bounds,
    or None if the service is unreachable.
    """
    ts = sensor.get("timestamp")
    temp = float(sensor.get("temperature", sensor.get("temperature_c", 25.0)))
    hum = float(sensor.get("humidity", 50.0))
    lux = float(sensor.get("lux", 0.0))
    occ = int(sensor.get("occupancy", 0))

    log.info(
        "→ Model input (live): temp=%.1f°C  hum=%.1f  lux=%.1f  occ=%d",
        temp, hum, lux, occ,
    )

    body = json.dumps({
        "temperature_c": temp,
        "humidity": hum,
        "lux": lux,
        "occupancy": occ,
        "datetime_str": ts,
    }).encode("utf-8")

    req = urllib.request.Request(
        ML_SERVICE_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        pred = result.get("predictions", {})
        ml_payload = {
            "predicted_energy_wh": pred.get("hybrid_final_wh", 0.0),
            "upper_bound_energy_wh": pred.get("safety_upper_bound_wh", 0.0),
            "lower_bound_energy_wh": pred.get("safety_lower_bound_wh", 0.0),
            "energy_unit": "Wh",
            "avg_sensors": {
                "temperature_c": temp,
                "humidity": hum,
                "lux": lux,
                "occupancy": occ,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "rule-engine-http-call",
        }
        log.info(
            "← Model output: EDFI=%.4f Wh  [%.4f – %.4f]",
            ml_payload["predicted_energy_wh"],
            ml_payload["lower_bound_energy_wh"],
            ml_payload["upper_bound_energy_wh"],
        )
        return ml_payload

    except urllib.error.URLError as exc:
        log.warning("ML service unreachable (%s) — decision will use cached data", exc.reason)
        return None
    except Exception as exc:
        log.warning("ML prediction fetch error: %s", exc)
        return None


def _compute_sensor_average() -> dict | None:
    """Average sensor readings from the last SENSOR_AVG_WINDOW_SECONDS.

    Returns a dict with averaged temperature, humidity, lux, occupancy,
    and the latest battery_level (battery is NOT averaged — we want the
    current SoC, not a smoothed version).
    Returns None if the buffer is empty.
    """
    cutoff = _time_mod.time() - SENSOR_AVG_WINDOW_SECONDS
    # Only keep readings within the window
    recent = [s for s in sensor_buffer if s.get("_ts", 0) >= cutoff]
    if not recent:
        return None

    n = len(recent)
    avg = {
        "temperature": round(sum(s.get("temperature", s.get("temperature_c", 0)) for s in recent) / n, 2),
        "humidity": round(sum(s.get("humidity", 0) for s in recent) / n, 2),
        "lux": round(sum(s.get("lux", 0) for s in recent) / n, 2),
        "occupancy": round(sum(s.get("occupancy", 0) for s in recent) / n),
        # Battery: use latest value, not average
        "battery_level": recent[-1].get("battery_level", 0),
        "timestamp": recent[-1].get("timestamp", datetime.now(timezone.utc).isoformat()),
    }
    log.debug(
        "Sensor average (%d readings, %ds window): temp=%.1f°C, hum=%.1f, lux=%.1f, occ=%d",
        n, SENSOR_AVG_WINDOW_SECONDS,
        avg["temperature"], avg["humidity"], avg["lux"], avg["occupancy"],
    )
    return avg


def run_evaluation(client: mqtt.Client) -> None:
    """Perform one evaluation cycle: fetch prediction, decide mode, actuate, log."""
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

    # Use the latest continuous prediction if available, otherwise fetch fresh.
    with state_lock:
        # Use real-time reading for logging in the decision box
        decision_sensor = dict(latest_sensor)
        if not latest_ml:
            sensor_snapshot = decision_sensor

    if not latest_ml:
        ml_result = _fetch_prediction(sensor_snapshot)
        if ml_result:
            with state_lock:
                latest_ml.clear()
                latest_ml.update(ml_result)

    # Publish prediction to MQTT at decision time (dashboard only sees this)
    with state_lock:
        if latest_ml:
            client.publish(TOPIC_ML, json.dumps(dict(latest_ml)), qos=1)

    # ── Step 2: Run decision logic ──
    with state_lock:
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

    change_str = "⚡ CLASS CHANGED" if mode_changed else "unchanged"

    log.info("")
    log.info("┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓")
    log.info("┃  DECISION: Class %s (%s)  →  R1=%s  R2=%s  R3=%s",
        new_mode, change_str,
        "ON" if r1 else "OFF",
        "ON" if r2 else "OFF",
        "ON" if r3 else "OFF",
    )
    log.info("┃  Sensors: temp=%.1f°C  hum=%.1f  lux=%.1f  occ=%d",
        float(decision_sensor.get("temperature", decision_sensor.get("temperature_c", 0))),
        float(decision_sensor.get("humidity", 0)),
        float(decision_sensor.get("lux", 0)),
        int(decision_sensor.get("occupancy", 0)),
    )
    log.info("┃  EDFI: %.4f Wh  [%.4f – %.4f]  bat=%.0f%%",
        float(ml_predicted) if ml_predicted != "n/a" else 0.0,
        float(ml_lower) if ml_lower != "n/a" else 0.0,
        float(ml_upper) if ml_upper != "n/a" else 0.0,
        float(latest_sensor.get("battery_level", 0)),
    )
    log.info("┃  Reason: %s", reason)
    log.info("┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛")
    log.info("")

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


def _run_continuous_prediction(client: mqtt.Client) -> None:
    """Run a rate-limited prediction triggered by new sensor data.

    Called from on_message when new sensor data arrives.  Sends the
    latest real-time sensor reading (not averaged) to the ML service,
    as the model was trained on individual timestep readings with CSV
    providing the energy lag memory.  The result is cached in latest_ml
    so the decision timer always has a fresh prediction ready.
    NOT published to MQTT — only the decision-time prediction is
    published (see run_evaluation).
    """
    global _last_prediction_epoch

    now = _time_mod.time()
    if now - _last_prediction_epoch < PREDICTION_RATE_LIMIT_SECONDS:
        return  # throttled
    _last_prediction_epoch = now

    with state_lock:
        if manual_override:
            return
        if not latest_sensor:
            return
        # Send the latest real-time reading (not averaged)
        sensor_snapshot = {
            "temperature": float(latest_sensor.get("temperature", latest_sensor.get("temperature_c", 25.0))),
            "humidity": float(latest_sensor.get("humidity", 50.0)),
            "lux": float(latest_sensor.get("lux", 0.0)),
            "occupancy": int(latest_sensor.get("occupancy", 0)),
            "battery_level": latest_sensor.get("battery_level", 0),
            "timestamp": latest_sensor.get("timestamp", datetime.now(timezone.utc).isoformat()),
        }

    ml_result = _fetch_prediction(sensor_snapshot)
    if ml_result:
        with state_lock:
            latest_ml.clear()
            latest_ml.update(ml_result)
        log.debug(
            "Continuous prediction cached: EDFI=%.4f Wh",
            ml_result.get("predicted_energy_wh", 0),
        )


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
            (TOPIC_OVERRIDE, 1),
        ])
        log.info(
            "Subscribed to %s, %s",
            TOPIC_SENSORS, TOPIC_OVERRIDE,
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

            # Buffer reading for 5-minute rolling average
            payload["_ts"] = _time_mod.time()
            sensor_buffer.append(payload)
            # Trim old entries beyond the averaging window
            cutoff = _time_mod.time() - SENSOR_AVG_WINDOW_SECONDS - 60
            while sensor_buffer and sensor_buffer[0].get("_ts", 0) < cutoff:
                sensor_buffer.pop(0)

            log.debug("Updated latest sensor data (buffer: %d readings)", len(sensor_buffer))

    # Run continuous prediction outside the lock (Group 2 real-time stage)
    if msg.topic == TOPIC_SENSORS:
        _run_continuous_prediction(client)


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
        "EDFI thresholds: Peak=%.0f, Moderate=%.0f, Baseline=%.0f Wh",
        PEAK_THRESHOLD,
        MODERATE_THRESHOLD,
        BASELINE_THRESHOLD,
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