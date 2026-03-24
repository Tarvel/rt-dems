#!/usr/bin/env python3
"""
test.py — CSV Playback Test CLI for the ML Prediction API
==========================================================

Reads abs_smart_grid_dataset_20k.csv row by row, sends environmental
data to the ML model via HTTP, and prints actual vs predicted side
by side for easy accuracy comparison.

Usage:
    python test.py              # Step through rows one at a time
    python test.py --auto N     # Auto-run N rows
    python test.py --auto 0     # Auto-run ALL rows

Prerequisites:
    test_prediction_api.py must be running
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_BASE = "http://127.0.0.1:5000"
PREDICT_URL = f"{API_BASE}/predict"

CSV_PATH = Path(__file__).resolve().parents[1] / "abs_smart_grid_dataset_20k.csv"

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


# ---------------------------------------------------------------------------
# Load CSV rows
# ---------------------------------------------------------------------------
def load_csv() -> list[dict]:
    try:
        with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError as e:
        fail(f"Cannot read CSV: {e}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Send one row to the ML model and display actual vs predicted
# ---------------------------------------------------------------------------
def test_row(row: dict, row_num: int, total: int) -> dict | None:
    """Send environmental data from one CSV row to /predict.

    Returns the API response dict, or None on failure.
    """
    # Extract environmental inputs (no energy)
    lux_col = "Luminous_Intensity_Lux" if "Luminous_Intensity_Lux" in row else "Luminous_Intensity"
    body = {
        "temperature_c": float(row["Temperature_C"]),
        "humidity": float(row["Humidity_%"]),
        "lux": float(row[lux_col]),
        "occupancy": int(float(row["Occupancy"])),
    }

    # Actual energy from CSV (ground truth)
    actual_kw = float(row["Energy_kW"])
    timestamp = row.get("Timestamp", "?")

    try:
        t0 = time.time()
        resp = requests.post(PREDICT_URL, json=body, timeout=15)
        elapsed = time.time() - t0

        if resp.status_code != 200:
            fail(f"Row {row_num}: HTTP {resp.status_code}")
            return None

        data = resp.json()
        predicted_kw = data.get("predictions", {}).get("hybrid_final_kwh", None)

        if predicted_kw is not None:
            diff = predicted_kw - actual_kw
            pct = (abs(diff) / actual_kw * 100) if actual_kw != 0 else 0

            print(
                f"  [{row_num:>5}/{total}] {timestamp}  "
                f"Actual: {B}{actual_kw:>7.4f}{X} kW | "
                f"Predicted: {B}{M}{predicted_kw:>7.4f}{X} kW | "
                f"Diff: {(G if abs(diff) < 0.5 else Y if abs(diff) < 1 else R)}"
                f"{diff:>+7.4f}{X} kW "
                f"({pct:>5.1f}%) "
                f"{D}[{elapsed:.2f}s]{X}"
            )
        else:
            warn(f"Row {row_num}: No prediction returned")

        return data

    except requests.ConnectionError:
        fail(f"Cannot reach {API_BASE} — is test_prediction_api.py running?")
        return None


# ---------------------------------------------------------------------------
# Interactive mode — step through CSV one row at a time
# ---------------------------------------------------------------------------
def interactive_mode(rows: list[dict]):
    banner("Interactive CSV Playback")
    print(f"  CSV: {CSV_PATH.name} ({len(rows)} rows)")
    print(f"  Press {B}Enter{X} for next row, {B}s N{X} to skip to row N, {B}q{X} to quit.\n")

    idx = 0
    total = len(rows)

    while idx < total:
        cmd = input(f"  {D}[Row {idx + 1}]{X} Enter/s N/q: ").strip().lower()

        if cmd == "q":
            print(f"\n  {G}Done!{X}\n")
            break

        if cmd.startswith("s "):
            try:
                target = int(cmd.split()[1]) - 1
                if 0 <= target < total:
                    idx = target
                    ok(f"Skipped to row {idx + 1}")
                else:
                    warn(f"Row must be between 1 and {total}")
                continue
            except (ValueError, IndexError):
                warn("Usage: s <row_number>")
                continue

        test_row(rows[idx], idx + 1, total)
        idx += 1

    if idx >= total:
        print(f"\n  {G}✓ Reached end of CSV.{X}\n")


# ---------------------------------------------------------------------------
# Auto mode — run through N rows automatically
# ---------------------------------------------------------------------------
def auto_mode(rows: list[dict], n: int):
    count = len(rows) if n == 0 else min(n, len(rows))
    banner(f"Auto Mode — {count} CSV Rows")

    print(
        f"  {'Row':>10}  {'Timestamp':<20}  "
        f"{'Actual':>10}  {'Predicted':>10}  "
        f"{'Diff':>10}  {'Error%':>7}"
    )
    print(f"  {'─' * 80}")

    for i in range(count):
        result = test_row(rows[i], i + 1, count)
        if result is None:
            break

    print(f"\n  {G}✓ Finished {count} rows.{X}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="CSV Playback Test — Actual vs Predicted comparison"
    )
    parser.add_argument(
        "--auto", type=int, metavar="N", default=None,
        help="Auto-run N rows (0 = all rows)"
    )
    args = parser.parse_args()

    banner("Smart Grid ML — CSV Playback Test")

    # Quick connectivity check
    try:
        requests.get(f"{API_BASE}/docs", timeout=3)
        ok(f"API reachable at {API_BASE}")
    except requests.ConnectionError:
        fail(f"API unreachable at {API_BASE}")
        warn("Start it first: python test_prediction_api.py")
        sys.exit(1)

    rows = load_csv()
    ok(f"Loaded {len(rows)} rows from {CSV_PATH.name}")
    print()

    if args.auto is not None:
        auto_mode(rows, args.auto)
    else:
        interactive_mode(rows)


if __name__ == "__main__":
    main()
