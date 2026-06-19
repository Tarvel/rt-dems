# `model_new_unsure` Integration Audit & Fix Plan

**Date:** 2026-06-19
**Status:** Issues found — fixes proposed below

---

## Summary

The `model_new_unsure` TE-GRU + XGBoost + MH model is **wired correctly at the API level** — the rule engine sends the right fields to `POST /predict`, the response is parsed correctly, and the MQTT buffer feed is connected. However, **4 integration gaps** were found that cause degraded predictions and operational blind spots.

---

## Issue 1: Missing `/reset` Endpoint

### IGNORE THIS PART, WE ARE UNCONCERNED ABOUT THE SIMULATION AND THE IMPLEMENTATION OF THIS ENDPOINT.



---

## Issue 2: Energy Unit Inconsistency

**Severity:** High
**Affects:** Both hardware and simulation mode

### The problem

The model's history buffer stores 5 fields per row: `{timestamp, temperature, humidity, lux, occupancy, energy}`. The `energy` field is used to compute 10 energy-derived features (lags, rolling means, standard deviations, trends). The model was trained on data where `energy` is in **Watt-hours (Wh)**.

However, there is a unit mismatch between the CSV cold-start data and the live MQTT feed:

| Source | Energy values | Unit |
|--------|--------------|------|
| `mydatanew.csv` (cold start) | ~0.39, ~0.41, ~0.45 | **Wh** (confirmed — training data) |
| `hw_bridge.py` → `energy_kw` | 0.000 (initial), cumulative kWh from Group 1 | **kWh** |
| `data_simulator.py` → `energy_kw` | ~0.0–2.5 (from CSV column `Energy_kW`) | **kW** (instantaneous power, not energy) |

After ~60 live MQTT readings, the CSV cold-start data is completely flushed from the 60-row deque. At that point, **all energy features are computed from values in the wrong unit**.

### Concrete impact

**Simulator mode:** The buffer transitions from Wh values (~0.4) to kW values (~1.5). The energy lag features jump from ~0.4 to ~1.5 (a 3.75× inflation). Rolling statistics and trends are affected. The model was trained on ~0.4 Wh scale — feeding it kW-scale values will inflate energy predictions.

**Hardware mode:** Group 1's `energy` field is cumulative kWh. It starts at `0.000` and slowly increments. The first hour of operation will have energy values near zero — all energy lag features collapse to zero, removing the model's "memory" of consumption patterns. As cumulative energy grows (e.g., 12.345 kWh), the buffer energy values drift far beyond the training distribution.

### Fix

Add a unit normalisation step in `model_new_unsure/main.py`'s `on_mqtt_message` handler so that whatever unit arrives from MQTT is converted to the Wh scale the model was trained on:

```python
def on_mqtt_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
        # ── Unit normalisation ──────────────────────────────────────
        # The model was trained on Wh-scale energy values (~0.3–50 Wh).
        # Live MQTT data may arrive in different units:
        #   - hw_bridge: kWh (cumulative) → multiply by 1000 → Wh
        #   - simulator: kW (instantaneous power) → multiply by 1000/12 → Wh per 5-min
        #     (12 intervals per hour at 5-min granularity)
        # ────────────────────────────────────────────────────────────
        raw_energy_kw = float(payload.get("energy_kw", 0.0))

        # Detect source: hardware publishes cumulative kWh, simulator publishes kW
        source = payload.get("source", "")

        if source == "group1_hardware":
            # Group 1: cumulative kWh → Wh (multiply by 1000)
            energy_wh = raw_energy_kw * 1000.0
        else:
            # Simulator / unknown: kW instantaneous → approximate Wh per 5-min interval
            energy_wh = raw_energy_kw * (1000.0 / 12.0)

        add_to_buffer(
            temperature=float(payload.get("temperature_c", payload.get("temperature", 25.0))),
            humidity=float(payload.get("humidity", 50.0)),
            lux=float(payload.get("lux", 0.0)),
            occupancy=int(payload.get("occupancy", 0)),
            energy=energy_wh,
            timestamp=payload.get("timestamp"),
        )
    except Exception as exc:
        print(f"[WARN] MQTT buffer update failed: {exc}")
```

**Alternative (simpler but less precise):** If you control the data pipeline end-to-end, fix the unit at the source — make `hw_bridge.py` and `data_simulator.py` publish `energy_kw` in Wh instead of kW/kWh. This is cleaner but requires changes in two other files.

**Files to change:**
- `model_new_unsure/main.py` — `on_mqtt_message` handler (lines 413–431)
- OR `workers/hw_bridge.py` — `normalise()` function (line 142)
- OR `simulation/data_simulator.py` — payload construction (line 259)

---

## Issue 3: Silent MQTT Exception Handling

**Severity:** Medium
**Affects:** Hardware mode (when Group 1 data is live)

### What happens

The MQTT message handler in `model_new_unsure/main.py` (lines 413–431) silently swallows all exceptions:

```python
def on_mqtt_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
        energy_val = float(payload.get("energy_kw", 0.0))
        add_to_buffer(...)
    except Exception:
        pass  # silently ignore malformed payloads
```

If Group 1 changes their payload format, adds/removes fields, or sends corrupted JSON, the buffer silently stops updating. There is zero visibility into why predictions are degrading.

### Fix

Log the exception so operators can detect data feed problems:

```python
def on_mqtt_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8", errors="replace"))
        energy_val = float(payload.get("energy_kw", 0.0))
        add_to_buffer(...)
    except Exception as exc:
        print(f"[WARN] MQTT buffer update failed: {exc} — payload was: "
              f"{msg.payload[:200]}...")
```

**File to change:** `model_new_unsure/main.py`
**Lines:** 429–431 (the `except Exception: pass` block)

---

## Issue 4: Integration Contract Lists Wrong Endpoints

**Severity:** Low
**Affects:** Documentation accuracy only

### What's wrong

`integration_contract.md` Section 9 lists these endpoints for the active model:

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/predict` | ✅ Present |
| `GET` | `/metadata` | ✅ Present |
| `GET` | `/` | ✅ Present |
| `GET` | `/predict_next` | ❌ NOT present in model_new_unsure |
| `GET` | `/reset` | ❌ NOT present in model_new_unsure |

The contract also mentions `POST /reset` which the simulator calls, but `model_new_unsure` has neither `/reset` nor `/predict_next`.

### Fix

Update `integration_contract.md` Section 9 to reflect `model_new_unsure`'s actual endpoints:

```
| `POST` | `/predict` | Send sensor values, get prediction back |
| `GET` | `/metadata` | Returns model info, buffer status, feature list |
| `GET` | `/` | Serves status page |
```

And add a note: "`/reset` and `/predict_next` are available on `LIGHT_ML_MODEL/main.py` (CSV-pointer-based model) but not on the buffer-based `model_new_unsure/main.py`. To reset the buffer-based model, restart the service."

**File to change:** `integration_contract.md` — Section 9

---

## Fix Priority & Order

| # | Issue | Priority | Effort | Risk of not fixing |
|---|-------|----------|--------|--------------------|
| 3 | Silent MQTT exceptions | **P0** | 1 line | Data feed failures go undetected |
| 1 | Missing `/reset` | **P1** | 5 lines | Simulator warns on every boot; future model compat |
| 2 | Energy unit mismatch | **P1** | 10 lines | Predictions degrade as CSV data ages out |
| 4 | Contract docs | **P2** | Documentation | Outdated docs confuse new team members |

### Recommended approach

1. Fix Issue 3 first (trivial, high impact for observability)
2. Fix Issue 1 (unblocks clean simulator integration)
3. Fix Issue 2 (choose source-level fix or model-level fix)
4. Update contract docs (Issue 4)

---

## Verification After Fixes

After applying fixes, verify with:

```bash
# 1. /reset returns 200
curl -s -X POST http://127.0.0.1:5000/reset | python3 -m json.tool
# Expected: {"message": "Reset acknowledged ..."}

# 2. /metadata shows correct buffer state
curl -s http://127.0.0.1:5000/metadata | python3 -m json.tool | grep -E "buffer_size|latest_energy"

# 3. Prediction works with live data
curl -s -X POST http://127.0.0.1:5000/predict \
  -H "Content-Type: application/json" \
  -d '{"temperature_c":31.6,"humidity":60.9,"lux":3.2,"occupancy":4}' | python3 -m json.tool

# 4. Run the integration test suite
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
python simulation/test_rule_engine_mqtt.py
```

---

## NOTES

I think we can ignore the reset and predict_next endpoint part (I already made notes under it, thats issue 1)
please go through the history and buffer, all that 8-row lag part, does it need fixing?