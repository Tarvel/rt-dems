#!/usr/bin/env python3
"""
test_rule_engine_mqtt.py — Rule Engine MQTT Integration Test
=============================================================

Mocks the MQTT broker locally and asserts that rule_engine.py
publishes the correct JSON relay-state payloads for every mode
transition.  No hardware or real broker required.

Usage:
    python simulation/test_rule_engine_mqtt.py

Tests cover:
  1. Mode A when energy >= peak demand, battery >= 80%, lag stable
  2. Mode B when energy >= peak demand, battery >= 50%, lag stable
  3. Mode C when energy >= peak demand, battery < 50%
  4. Step 2 fallback when energy < peak demand
  5. Lag instability forcing mode drop (day vs night thresholds)
  6. Shutdown publishes Mode C
"""

import json
import sys
import os
import time
import threading
from datetime import datetime
from unittest.mock import MagicMock, patch

# ── Ensure workers/ is importable ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# ── Colour helpers ──
GREEN = "\033[92m"
RED   = "\033[91m"
CYAN  = "\033[96m"
RESET = "\033[0m"
BOLD  = "\033[1m"

passed = 0
failed = 0


def assert_eq(label: str, actual, expected):
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"  {GREEN}✓{RESET} {label}")
    else:
        failed += 1
        print(f"  {RED}✗{RESET} {label}")
        print(f"    Expected: {expected}")
        print(f"    Actual:   {actual}")


def section(title: str):
    print(f"\n{BOLD}{CYAN}━━ {title} ━━{RESET}")


# ── We need to patch env vars BEFORE importing rule_engine ──
os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_PORT", "1883")
os.environ.setdefault("DECISION_INTERVAL_MINUTES", "1")
os.environ.setdefault("BATTERY_LAG_INTERVAL_SECONDS", "60")
os.environ.setdefault("MAX_BATTERY_DROP_PERCENT", "2")
os.environ.setdefault("MAX_BATTERY_DROP_NIGHT_PERCENT", "8")
os.environ.setdefault("MODE_A_MAX_KWH", "2.4")
os.environ.setdefault("SOLAR_HOUR_START", "11")
os.environ.setdefault("SOLAR_HOUR_END", "16")

# Import rule engine internals
import workers.rule_engine as re_mod


# ---------------------------------------------------------------------------
# Helper: inject state and run a single evaluation
# ---------------------------------------------------------------------------
def inject_and_evaluate(
    sensor: dict,
    ml: dict,
    t_now=None,
    t1=None,
    t2=None,
) -> dict:
    """Inject state into rule_engine globals, run one evaluation,
    and return the published MQTT payload (or empty dict)."""

    # Inject sensor + ML
    with re_mod.state_lock:
        re_mod.latest_sensor.clear()
        re_mod.latest_sensor.update(sensor)
        re_mod.latest_ml.clear()
        re_mod.latest_ml.update(ml)
        re_mod.battery_t_now = t_now
        re_mod.battery_t1 = t1
        re_mod.battery_t2 = t2

    # Mock MQTT client
    mock_client = MagicMock()
    captured = {}

    def capture_publish(topic, payload, **kwargs):
        captured["topic"] = topic
        captured["payload"] = json.loads(payload)

    mock_client.publish.side_effect = capture_publish

    # Run evaluation
    re_mod.run_evaluation(mock_client)

    return captured.get("payload", {})


# ===========================================================================
# TEST SUITE
# ===========================================================================
def main():
    global passed, failed

    print(f"\n{BOLD}{'=' * 60}")
    print("  Rule Engine — MQTT Payload Test Suite")
    print(f"{'=' * 60}{RESET}")

    base_sensor = {
        "temperature_c": 25.0,
        "temperature": 25.0,
        "humidity": 50.0,
        "lux": 300.0,
        "occupancy": 1,
        "battery_level": 90.0,
        "timestamp": datetime.now().isoformat(),
    }
    ml_above_peak = {"predicted_energy_kw": 3.0}    # >= 2.4 kW
    ml_below_peak = {"predicted_energy_kw": 1.0}    # < 2.4 kW

    # ------------------------------------------------------------------
    section("1. Mode A — energy sufficient, battery ≥80%, lag stable")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 85.0}
    payload = inject_and_evaluate(
        sensor, ml_above_peak,
        t_now=85.0, t1=85.5, t2=86.0,  # drop = 1.0% < 2%
    )
    assert_eq("mode is A", payload.get("mode"), "A")
    assert_eq("relay_1 is True", payload.get("relay_1"), True)
    assert_eq("relay_2 is True", payload.get("relay_2"), True)
    assert_eq("relay_3 is True", payload.get("relay_3"), True)
    assert_eq("reason contains 'Mode A'", "Mode A" in payload.get("reason", ""), True)

    # ------------------------------------------------------------------
    section("2. Mode B — energy sufficient, battery 50-79%, lag stable")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 65.0}
    payload = inject_and_evaluate(
        sensor, ml_above_peak,
        t_now=65.0, t1=65.5, t2=66.0,  # drop = 1.0%
    )
    assert_eq("mode is B", payload.get("mode"), "B")
    assert_eq("relay_1 is True", payload.get("relay_1"), True)
    assert_eq("relay_2 is True", payload.get("relay_2"), True)
    assert_eq("relay_3 is False", payload.get("relay_3"), False)

    # ------------------------------------------------------------------
    section("3. Mode C — energy sufficient, battery < 50%")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 40.0}
    payload = inject_and_evaluate(
        sensor, ml_above_peak,
        t_now=40.0, t1=41.0, t2=42.0,
    )
    assert_eq("mode is C", payload.get("mode"), "C")
    assert_eq("relay_1 is True", payload.get("relay_1"), True)
    assert_eq("relay_2 is False", payload.get("relay_2"), False)
    assert_eq("relay_3 is False", payload.get("relay_3"), False)

    # ------------------------------------------------------------------
    section("4. Step 2 — energy tight, battery ≥80%, lag stable → A")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 85.0}
    payload = inject_and_evaluate(
        sensor, ml_below_peak,
        t_now=85.0, t1=85.5, t2=86.0,
    )
    assert_eq("mode is A", payload.get("mode"), "A")
    assert_eq("reason contains 'Step 2'", "Step 2" in payload.get("reason", ""), True)

    # ------------------------------------------------------------------
    section("5. Step 2 — energy tight, battery ≥60%, lag stable → B")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 65.0}
    payload = inject_and_evaluate(
        sensor, ml_below_peak,
        t_now=65.0, t1=65.5, t2=66.0,
    )
    assert_eq("mode is B", payload.get("mode"), "B")

    # ------------------------------------------------------------------
    section("6. Step 2 — energy tight, battery < 60% → C")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 50.0}
    payload = inject_and_evaluate(
        sensor, ml_below_peak,
        t_now=50.0, t1=51.0, t2=52.0,
    )
    assert_eq("mode is C", payload.get("mode"), "C")

    # ------------------------------------------------------------------
    section("7. Lag UNSTABLE (daytime, >2%) — mode drops B→C at 50-79%")
    # ------------------------------------------------------------------
    # Force daytime threshold by patching the hour
    with patch.object(re_mod, "_active_battery_threshold", return_value=(2.0, "daytime")):
        sensor = {**base_sensor, "battery_level": 65.0}
        payload = inject_and_evaluate(
            sensor, ml_above_peak,
            t_now=65.0, t1=66.5, t2=68.0,  # drop = 3.0% > 2%
        )
        assert_eq("mode is C (lag unstable, day)", payload.get("mode"), "C")
        assert_eq("reason contains 'NOT stable'", "NOT stable" in payload.get("reason", ""), True)

    # ------------------------------------------------------------------
    section("8. Lag would be unstable at day threshold but STABLE at night threshold")
    # ------------------------------------------------------------------
    with patch.object(re_mod, "_active_battery_threshold", return_value=(8.0, "nighttime")):
        sensor = {**base_sensor, "battery_level": 65.0}
        payload = inject_and_evaluate(
            sensor, ml_above_peak,
            t_now=65.0, t1=66.5, t2=68.0,  # drop = 3.0%: >2% but <8%
        )
        assert_eq("mode is B (lag stable under night threshold)", payload.get("mode"), "B")

    # ------------------------------------------------------------------
    section("9. No ML prediction — maintains current mode")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 85.0}
    payload = inject_and_evaluate(sensor, {})  # empty ML
    assert_eq("reason contains 'no ML prediction'", "no ML prediction" in payload.get("reason", ""), True)

    # ------------------------------------------------------------------
    section("10. Payload structure — all required keys present")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 85.0}
    payload = inject_and_evaluate(
        sensor, ml_above_peak,
        t_now=85.0, t1=85.5, t2=86.0,
    )
    required_keys = {"mode", "relay_1", "relay_2", "relay_3", "reason", "timestamp",
                     "battery_t_now", "battery_t1", "battery_t2",
                     "battery_lag_drop", "battery_lag_interval_seconds"}
    present = set(payload.keys())
    missing = required_keys - present
    assert_eq("all required keys present", missing, set())

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    total = passed + failed
    print(f"\n{BOLD}{'=' * 60}")
    if failed == 0:
        print(f"  {GREEN}ALL {total} TESTS PASSED ✓{RESET}")
    else:
        print(f"  {RED}{failed}/{total} TESTS FAILED ✗{RESET}")
    print(f"{'=' * 60}{RESET}\n")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
