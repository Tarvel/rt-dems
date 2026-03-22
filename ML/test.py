#!/usr/bin/env python3
"""
test.py — Manual HTTP Test CLI for the ML Prediction API
==========================================================

Type sensor values → POST to /predict → see model output.
Pure HTTP. No MQTT.

Usage:
    python test.py                 # Interactive (type your inputs)
    python test.py --auto N        # Auto-step through N CSV rows

Prerequisites:
    test_prediction_api.py must be running
"""

import argparse
import json
import sys
import time

import requests

# ---------------------------------------------------------------------------
# Configuration — must match test_prediction_api.py
# ---------------------------------------------------------------------------
API_BASE = "http://127.0.0.1:5000"
PREDICT_URL = f"{API_BASE}/predict"
PREDICT_NEXT_URL = f"{API_BASE}/predict_next"

# ANSI colours
G = "\033[92m"
R = "\033[91m"
Y = "\033[93m"
C = "\033[96m"
M = "\033[95m"
B = "\033[1m"
D = "\033[2m"
X = "\033[0m"


def banner(text: str):
    w = 60
    print(f"\n{C}{'=' * w}")
    print(f"  {B}{text}{X}{C}")
    print(f"{'=' * w}{X}\n")


def ok(msg): print(f"  {G}✓{X} {msg}")
def fail(msg): print(f"  {R}✗{X} {msg}")
def warn(msg): print(f"  {Y}⚠{X} {msg}")


def prompt_float(label: str, default: float) -> float:
    raw = input(f"  {M}{label}{X} [{D}{default}{X}]: ").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        warn(f"Invalid number, using default: {default}")
        return default


def prompt_int(label: str, default: int) -> int:
    raw = input(f"  {M}{label}{X} [{D}{default}{X}]: ").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        warn(f"Invalid number, using default: {default}")
        return default


# ---------------------------------------------------------------------------
# Display prediction results
# ---------------------------------------------------------------------------
def display_result(data: dict):
    sensors = data.get("live_sensors", {})
    pred = data.get("predictions", {})

    print()
    print(f"  {B}{C}─── Inputs Used ───{X}")
    if sensors:
        for k, v in sensors.items():
            if isinstance(v, float):
                print(f"    {k:>20s}: {B}{v:.2f}{X}")
            else:
                print(f"    {k:>20s}: {B}{v}{X}")

    print()
    print(f"  {B}{M}─── Model Predictions ───{X}")
    if pred:
        for k, v in pred.items():
            label = k.replace("_", " ").title()
            if isinstance(v, float):
                print(f"    {label:>24s}: {B}{v:.4f}{X}")
            else:
                print(f"    {label:>24s}: {B}{v}{X}")

    ts = data.get("timestamp", "?")
    print(f"\n  {D}Timestamp: {ts}{X}")
    print()


# ---------------------------------------------------------------------------
# Interactive mode: user types sensor values, model predicts
# ---------------------------------------------------------------------------
def interactive_mode():
    banner("Manual Prediction Mode")
    print(f"  Type sensor values (press Enter for default).\n"
          f"  Type {B}'q'{X} to quit, {B}'n'{X} to auto-step CSV.\n")

    while True:
        print(f"  {C}{'─' * 45}{X}")
        cmd = input(f"  {B}[Enter]{X} manual input | {B}n{X} CSV next | {B}q{X} quit: ").strip().lower()

        if cmd == 'q':
            print(f"\n  {G}Bye!{X}\n")
            break

        if cmd == 'n':
            try:
                resp = requests.get(PREDICT_NEXT_URL, timeout=15)
                data = resp.json()
                if "error" in data:
                    fail(data["error"])
                    continue
                ok(f"CSV predict_next → {data['predictions']['hybrid_final_kwh']} kW")
                display_result(data)
            except requests.ConnectionError:
                fail(f"Cannot reach {API_BASE} — is the API running?")
            continue

        # Manual input
        print(f"\n  {B}Enter your sensor values:{X}")
        temp = prompt_float("Temperature (°C)", 28.0)
        hum = prompt_float("Humidity (%)", 60.0)
        lux = prompt_float("Luminous Intensity (Lux)", 400.0)
        occ = prompt_int("Occupancy (0 = empty, 1 = occupied)", 1)
        energy = prompt_float("Energy (kW)", 1.5)

        body = {
            "temperature_c": temp,
            "humidity": hum,
            "lux": lux,
            "occupancy": occ,
            "energy_kw": energy,
        }

        try:
            t0 = time.time()
            resp = requests.post(PREDICT_URL, json=body, timeout=15)
            elapsed = time.time() - t0

            if resp.status_code != 200:
                fail(f"HTTP {resp.status_code}: {resp.text[:200]}")
                continue

            data = resp.json()
            ok(f"Prediction received in {elapsed:.3f}s")
            display_result(data)

        except requests.ConnectionError:
            fail(f"Cannot reach {API_BASE} — is test_prediction_api.py running?")


# ---------------------------------------------------------------------------
# Auto mode: step through N CSV rows
# ---------------------------------------------------------------------------
def auto_mode(n: int):
    banner(f"Auto Mode — {n} CSV Predictions")

    for i in range(1, n + 1):
        try:
            resp = requests.get(PREDICT_NEXT_URL, timeout=15)
            data = resp.json()
            if "error" in data:
                fail(data["error"])
                break

            kw = data["predictions"]["hybrid_final_kwh"]
            ts = data.get("timestamp", "?")
            print(f"  [{i:>4}] {ts}  →  {B}{kw:.4f} kW{X}")

        except requests.ConnectionError:
            fail(f"Cannot reach {API_BASE}")
            break

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="ML Prediction API — Manual Test CLI")
    parser.add_argument("--auto", type=int, metavar="N",
                        help="Auto-step through N CSV predictions")
    args = parser.parse_args()

    banner("Smart Grid ML — Manual Test CLI")

    # Quick connectivity check
    try:
        requests.get(f"{API_BASE}/docs", timeout=3)
        ok(f"API reachable at {API_BASE}")
    except requests.ConnectionError:
        fail(f"API unreachable at {API_BASE}")
        warn("Start it first: python test_prediction_api.py")
        sys.exit(1)

    if args.auto:
        auto_mode(args.auto)
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
