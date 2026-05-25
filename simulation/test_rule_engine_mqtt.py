#!/usr/bin/env python3
"""
test_rule_engine_mqtt.py — Rule Engine MQTT Integration Test
=============================================================

Mocks the MQTT broker locally and asserts that rule_engine.py
publishes the correct JSON relay-state payloads for every mode
transition.  No hardware or real broker required.

Usage:
    python simulation/test_rule_engine_mqtt.py

Tests cover (notes.py decision tree):
  1. PEAK LOAD (EDFI >= 80):
     - battery_stable(80%) → Smart A
     - battery_stable(60%) → Smart B
     - else                → Smart C
  2. MODERATE LOAD (50 <= EDFI < 80):
     - battery_stable(60%) → Smart B
     - else                → Smart C
  3. BASELINE LOAD (30 <= EDFI < 50) → Smart C
  4. VERY LOW LOAD (EDFI < 30) → All OFF
  5. No ML prediction → maintain current mode
  6. Payload structure check
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
os.environ.setdefault("PEAK_THRESHOLD", "80")
os.environ.setdefault("MODERATE_THRESHOLD", "50")
os.environ.setdefault("BASELINE_THRESHOLD", "30")

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
    ml_peak = {"predicted_energy_wh": 85.0}         # >= 80 Wh → peak
    ml_moderate = {"predicted_energy_wh": 60.0}      # 50-79 → moderate
    ml_baseline = {"predicted_energy_wh": 40.0}      # 30-49 → baseline
    ml_very_low = {"predicted_energy_wh": 10.0}      # < 30  → very low

    # ------------------------------------------------------------------
    section("1. PEAK LOAD — battery_stable(80%) → Smart A")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 90.0}
    payload = inject_and_evaluate(
        sensor, ml_peak,
        t_now=90.0, t1=88.0, t2=85.0,  # all >= 80%
    )
    assert_eq("mode is A", payload.get("mode"), "A")
    assert_eq("relay_1 is True", payload.get("relay_1"), True)
    assert_eq("relay_2 is True", payload.get("relay_2"), True)
    assert_eq("relay_3 is True", payload.get("relay_3"), True)
    assert_eq("reason contains 'Smart A'", "Smart A" in payload.get("reason", ""), True)

    # ------------------------------------------------------------------
    section("2. PEAK LOAD — battery_stable(80%) fails, battery_stable(60%) → Smart B")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 75.0}
    payload = inject_and_evaluate(
        sensor, ml_peak,
        t_now=75.0, t1=70.0, t2=65.0,  # all >= 60% but NOT all >= 80%
    )
    assert_eq("mode is B", payload.get("mode"), "B")
    assert_eq("relay_1 is True", payload.get("relay_1"), True)
    assert_eq("relay_2 is True", payload.get("relay_2"), True)
    assert_eq("relay_3 is False", payload.get("relay_3"), False)

    # ------------------------------------------------------------------
    section("3. PEAK LOAD — battery_stable(60%) fails → Smart C")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 55.0}
    payload = inject_and_evaluate(
        sensor, ml_peak,
        t_now=55.0, t1=50.0, t2=45.0,  # T-2 = 45% < 60%
    )
    assert_eq("mode is C", payload.get("mode"), "C")
    assert_eq("relay_1 is True", payload.get("relay_1"), True)
    assert_eq("relay_2 is False", payload.get("relay_2"), False)
    assert_eq("relay_3 is False", payload.get("relay_3"), False)

    # ------------------------------------------------------------------
    section("4. MODERATE LOAD — battery_stable(60%) → Smart B")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 70.0}
    payload = inject_and_evaluate(
        sensor, ml_moderate,
        t_now=70.0, t1=68.0, t2=65.0,  # all >= 60%
    )
    assert_eq("mode is B", payload.get("mode"), "B")
    assert_eq("reason contains 'MODERATE LOAD'", "MODERATE LOAD" in payload.get("reason", ""), True)

    # ------------------------------------------------------------------
    section("5. MODERATE LOAD — battery_stable(60%) fails → Smart C")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 55.0}
    payload = inject_and_evaluate(
        sensor, ml_moderate,
        t_now=55.0, t1=50.0, t2=45.0,  # T-2 = 45% < 60%
    )
    assert_eq("mode is C", payload.get("mode"), "C")

    # ------------------------------------------------------------------
    section("6. BASELINE LOAD → Always Smart C")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 90.0}
    payload = inject_and_evaluate(
        sensor, ml_baseline,
        t_now=90.0, t1=90.0, t2=90.0,
    )
    assert_eq("mode is C", payload.get("mode"), "C")
    assert_eq("reason contains 'BASELINE LOAD'", "BASELINE LOAD" in payload.get("reason", ""), True)

    # ------------------------------------------------------------------
    section("7. VERY LOW LOAD → All relays OFF")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 90.0}
    payload = inject_and_evaluate(
        sensor, ml_very_low,
        t_now=90.0, t1=90.0, t2=90.0,
    )
    assert_eq("mode is OFF", payload.get("mode"), "OFF")
    assert_eq("relay_1 is False", payload.get("relay_1"), False)
    assert_eq("relay_2 is False", payload.get("relay_2"), False)
    assert_eq("relay_3 is False", payload.get("relay_3"), False)
    assert_eq("reason contains 'VERY LOW'", "VERY LOW" in payload.get("reason", ""), True)

    # ------------------------------------------------------------------
    section("8. No ML prediction → maintains current mode")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 85.0}
    payload = inject_and_evaluate(sensor, {})  # empty ML
    assert_eq("reason contains 'no ML prediction'", "no ML prediction" in payload.get("reason", ""), True)

    # ------------------------------------------------------------------
    section("9. Partial lag window (None values) → treated as stable")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 90.0}
    payload = inject_and_evaluate(
        sensor, ml_peak,
        t_now=90.0, t1=None, t2=None,  # partial lag
    )
    assert_eq("mode is A (partial lag treated as stable)", payload.get("mode"), "A")

    # ------------------------------------------------------------------
    section("10. Payload structure — all required keys present")
    # ------------------------------------------------------------------
    sensor = {**base_sensor, "battery_level": 85.0}
    payload = inject_and_evaluate(
        sensor, ml_peak,
        t_now=85.0, t1=85.0, t2=85.0,
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
