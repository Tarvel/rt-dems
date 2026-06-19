# Prompting the `model_new_unsure` Model — Accurate Prediction Guide

**Model:** TE-GRU + XGBoost + Metropolis-Hastings Bayesian Residual Correction
**Service file:** `model_new_unsure/main.py`
**Model bundle:** `model_new_unsure/model_assets/tegru_xgb_mh_pipeline.joblib`
**API:** FastAPI on port 5000

---

## 1. The Prediction Endpoint

### `POST /predict`

All predictions go through this single endpoint. There is no MQTT-driven auto-prediction — the rule engine (or any caller) must call this HTTP endpoint explicitly.

### Request Body

```json
{
    "temperature_c": 31.6,
    "humidity": 60.9,
    "lux": 3.2,
    "occupancy": 4,
    "datetime_str": "2026-06-18T14:30:00"
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `temperature_c` | float | No | `25.0` | Room temperature in °C |
| `humidity` | float | No | `50.0` | Relative humidity in % |
| `lux` | float | No | `0.0` | Ambient light level in lux |
| `occupancy` | int | No | `1` | Number of people in the room (0 = empty) |
| `datetime_str` | string | No | current server time | ISO 8601 timestamp for time-based feature computation |

### Response (Success — 200)

```json
{
    "predicted_energy_wh": 11.074,
    "upper_bound_energy_wh": 13.1317,
    "lower_bound_energy_wh": 9.0163,
    "predicted_energy_range_wh": [9.0163, 13.1317],
    "energy_unit": "Wh",
    "tegru_raw_wh": 10.9521,
    "xgb_residual_wh": 0.1116,
    "peak_demand": 5.0,
    "buffer_size": 60,
    "timestamp": "2026-06-18T13:58:16.485123Z",
    "source": "fastapi-tegru-xgb-mh"
}
```

| Field | Meaning |
|-------|---------|
| `predicted_energy_wh` | **The final prediction** — this is the EDFI value used by the rule engine |
| `upper_bound_energy_wh` | 95% confidence upper bound |
| `lower_bound_energy_wh` | 95% confidence lower bound (floored at 0) |
| `predicted_energy_range_wh` | Bounds as a two-element array `[lower, upper]` |
| `tegru_raw_wh` | Raw TE-GRU sequence model output (before residual correction) |
| `xgb_residual_wh` | XGBoost residual correction term |
| `buffer_size` | Current number of rows in the history buffer |

### Response (503 — Not Ready)

If the model failed to load, or the history buffer has fewer than 12 rows:

```json
{
    "detail": "Insufficient history: 5 rows (need ≥12)"
}
```

---

## 2. The History Buffer — The Single Most Critical Requirement

This model **cannot** make accurate predictions from a single sensor snapshot. It requires a **rolling history buffer** of past sensor + energy readings to compute lag features, rolling statistics, and trend features. The buffer is the model's "memory."

### What the Buffer Contains

Each entry in the rolling deque is a dict with 6 fields:

```python
{
    "timestamp": Timestamp("2026-06-18 14:25:00"),
    "temperature": 31.4,
    "humidity": 60.5,
    "lux": 5.5,
    "occupancy": 4,
    "energy": 10.95          # ← THE CRITICAL FIELD — actual energy consumed (Wh)
}
```

### Buffer Size Requirements

| Threshold | Meaning |
|-----------|---------|
| **≥ 12 rows** | Absolute minimum — the service will reject requests with fewer rows (503 error) |
| **≥ 12 rows** | Needed for `rolling12`, `std12`, `trend12` energy features |
| **≥ 6 rows** | Needed for `rolling6`, `std6`, `trend6` features |
| **60 rows** | Default `BUFFER_SIZE` — the deque capacity |

Below 12 rows, the model literally **cannot run** — `POST /predict` returns HTTP 503.

### How the Buffer is Populated

There are two mechanisms:

#### A. Cold Start: CSV Seeding

On service startup, `_seed_buffer_from_csv()` loads the last 60 rows from `model_assets/mydatanew.csv` into the buffer:

- The CSV must have columns: `timestamp`, `temperature`, `humidity`, `lux`, `occupancy`, `energy`
- Only the last `BUFFER_SIZE` (60) rows are used
- If the CSV fails to load, the buffer starts **empty** and the service cannot serve predictions until live MQTT data fills it

#### B. Runtime: MQTT Live Feed

The service runs an internal MQTT client that listens to `room/sensors`. Every time a sensor payload arrives, it calls `add_to_buffer()`:

```python
def on_mqtt_message(client, userdata, msg):
    payload = json.loads(msg.payload)
    energy_val = float(payload.get("energy_kw", 0.0))
    add_to_buffer(
        temperature=payload.get("temperature_c", 25.0),
        humidity=payload.get("humidity", 50.0),
        lux=payload.get("lux", 0.0),
        occupancy=int(payload.get("occupancy", 0)),
        energy=energy_val,
        timestamp=payload.get("timestamp"),
    )
```

New live readings push old CSV rows out of the deque. Within 60 readings, the buffer is entirely live data.

### The `energy` Field — Why It Matters

The `energy` field in each buffer entry is the **actual energy consumption** (in Wh) from the INA219 sensor, carried in MQTT as `energy_kw`. This is the **feedback loop**:

```
Buffer energy values  →  lag/roll/std/trend features  →  model prediction
```

If `energy_kw` is consistently `0.0` in MQTT payloads (which happens when Group 1's hardware is not connected, or with the simulator's fake data), then **all energy lag features will be zero**, and the model will predict near-zero energy (often ~0.3 Wh due to the zero-occupancy rule). This is not a model failure — it is a data starvation problem.

---

## 3. What Happens Inside the Model — Step by Step

### Step 1: Buffer Snapshot

The current thread-safe buffer is copied. All existing rows form the historical context.

### Step 2: Build Working DataFrame

The buffer rows become a DataFrame. The current prediction row (from your POST body) is appended as the last row with `energy = NaN` (because energy is the target being predicted).

```
Rows 1–60 (from buffer)  +  Row 61 (your live input, energy=NaN)
```

### Step 3: Feature Engineering

`engineer_features()` computes **74 columns** for every row:

**4 raw sensor columns:**
`temperature`, `humidity`, `lux`, `occupancy`

**6 calendar columns:**
`hour`, `minute`, `dayofweek`, `month`, `day`, `is_weekend`

**4 cyclical time columns:**
`hour_sin`, `hour_cos`, `month_sin`, `month_cos`

**5 interaction columns:**
`temp_x_humidity`, `temp_x_occupancy`, `humidity_x_occupancy`, `lux_x_occupancy`, `hour_x_occupancy`

**22 energy lag columns** (11 statistics × 2 aliases — `energy_*` and `real time energy_*`):

| Feature | Formula | Minimum buffer needed |
|---------|---------|----------------------|
| `energy_lag1` | Energy at t-1 | 1 row |
| `energy_lag2` | Energy at t-2 | 2 rows |
| `energy_lag3` | Energy at t-3 | 3 rows |
| `energy_roll3` | Mean of t-1, t-2, t-3 | 3 rows |
| `energy_roll6` | Mean of last 6 energy values | 6 rows |
| `energy_roll12` | Mean of last 12 energy values | 12 rows |
| `energy_std6` | Std dev of last 6 energy values | 6 rows |
| `energy_std12` | Std dev of last 12 energy values | 12 rows |
| `energy_trend3` | lag1 − lag3 (short-term direction) | 3 rows |
| `energy_trend6` | lag1 − lag6 (medium-term direction) | 6 rows |
| `energy_trend12` | lag1 − lag12 (long-term direction) | 12 rows |

The `real time energy_*` aliases duplicate every value — the training pipeline used `real time energy` as the primary energy column name, so both column name conventions are populated to satisfy the model's expected `feature_columns`.

**33 sensor history columns** (11 lag/roll/std/trend for each of `temperature`, `humidity`, `lux`, `occupancy`):

For each sensor, the same 11 statistics are computed: `lag1`, `lag2`, `lag3`, `roll3`, `roll6`, `roll12`, `std6`, `std12`, `trend3`, `trend6`, `trend12`.

All NaN values are forward-filled, backward-filled, then zero-filled.

### Step 4: Force Energy Features for the Prediction Row

Because the prediction row has `energy=NaN`, the energy lag features for the last row would naturally be computed as NaN or incorrect. The model **overrides** them using the actual energy values from the buffer:

```python
last_12 = energy_values[-12:]   # last 12 actual energy readings from buffer
forced = {
    "energy_lag1": last_12[-1],                          # 11.5
    "energy_lag2": last_12[-2],                          # 11.0
    "energy_lag3": last_12[-3],                          # 11.2
    "energy_roll3": mean(last_12[-3:]),                  # 11.23
    "energy_roll6": mean(last_12[-6:]),                  # 10.93
    "energy_roll12": mean(last_12[-12:]),                # 10.70
    "energy_std6": std(last_12[-6:]),                    # 0.36
    "energy_std12": std(last_12[-12:]),                  # ...
    "energy_trend3": last_12[-1] - last_12[-3],          # +0.3
    "energy_trend6": last_12[-1] - last_12[-6],          # +0.9
    "energy_trend12": last_12[-1] - last_12[-12],        # ...
}
```

These are written into both `energy_*` and `real time energy_*` columns for the prediction row.

### Step 5: Select Feature Columns

Only the exact columns the model was trained on are kept (loaded from `feature_columns` in the joblib bundle). Any missing columns are filled with `0`.

### Step 6: Prepare Model Inputs

- **XGBoost** gets 1 row: the engineered features for the current prediction row → shape `(1, N_features)`
- **TE-GRU** gets an **8-row sequence**: the last `WINDOW=8` rows of engineered features → reshaped to `(1, 8, N_features)`. If the working DataFrame has fewer than 8 rows, the first row is repeated to pad.

### Step 7: Predict

```python
tegru_pred = tegru.predict(X_sequence)        # sequence model → base energy estimate
residual_pred = xgb_residual.predict(X_current)  # tree model → error correction

final_pred = tegru_pred + (alpha * residual_pred) + beta
final_pred = max(0.0, final_pred)              # floor at 0
```

The TE-GRU produces the base energy estimate. XGBoost predicts the residual error. The Metropolis-Hastings `alpha` (scale) and `beta` (bias) parameters, fitted during training, calibrate the correction:

```
final = TE-GRU_raw + α × XGBoost_residual + β
```

### Step 8: Zero-Occupancy Override

If `occupancy == 0` AND the CSV is loaded:

```python
lowest_zero_occ_energy = min(energy in CSV where occupancy == 0)
final_pred = lowest_zero_occ_energy
```

This bypasses the ML prediction entirely and forces the output to the lowest energy value ever observed when the room was empty. This prevents the model from predicting "ghost" energy usage in an unoccupied room.

### Step 9: Confidence Bounds

```python
lower_95 = max(0.0, final_pred - q95)
upper_95 = final_pred + q95
lower_80 = max(0.0, final_pred - q80)
upper_80 = final_pred + q80
```

`q95` and `q80` are fixed calibration parameters loaded from the joblib bundle. They represent the residual spread observed during training — not dynamically computed per prediction.

---

## 4. Complete Numerical Example

### Given: Buffer last 12 energy values (Wh)

```
t-12: 10.0   t-11: 10.2   t-10: 10.5   t-9: 10.1
t-8:  10.3   t-7:  10.8   t-6:  10.6   t-5: 10.4
t-4:  10.9   t-3:  11.2   t-2:  11.0   t-1: 11.5
```

### Given: Current sensor reading

```json
{"temperature_c": 32.0, "humidity": 62.0, "lux": 6.0, "occupancy": 4}
```

### Computed energy features for the prediction row

| Feature | Value | Source |
|---------|-------|--------|
| `energy_lag1` | 11.5 | `t-1` energy |
| `energy_lag2` | 11.0 | `t-2` energy |
| `energy_lag3` | 11.2 | `t-3` energy |
| `energy_roll3` | 11.23 | mean(11.5, 11.0, 11.2) |
| `energy_roll6` | 10.93 | mean(11.5, ..., 10.6) |
| `energy_roll12` | 10.70 | mean of all 12 |
| `energy_std6` | 0.36 | std of last 6 |
| `energy_trend3` | +0.3 | 11.5 − 11.2 |
| `energy_trend6` | +0.9 | 11.5 − 10.6 |

These are forced into the prediction row before model execution.

### Prediction

```
TE-GRU output:       10.95 Wh      (sequence model sees 8-row window)
XGBoost residual:    +0.11 Wh      (correction from flat features)
MH alpha × residual:  0.0035 Wh    (alpha ≈ 0.03 calibrates correction)
MH beta:             +0.01 Wh      (bias term)

Final = 10.95 + 0.0035 + 0.01 = 10.96 Wh
95% CI = [10.96 - 2.06, 10.96 + 2.06] = [8.90, 13.02] Wh
```

---

## 5. How to Get Accurate Predictions

### Requirement 1: The Buffer Must Be Full of Real Energy Data

The single biggest factor in prediction accuracy is the **quality of energy values in the history buffer**. If `energy_kw` in MQTT payloads is zero or stale, all energy lag features collapse to zero and the model cannot detect consumption patterns.

**Check buffer health:**
```bash
curl http://127.0.0.1:5000/metadata | jq '{buffer_size, latest_energy, csv_ready, model_ready}'
```

If `latest_energy` is `0.0` or `null`, the model is starved of real energy data.

### Requirement 2: `datetime_str` Must Be Accurate

The model uses the timestamp to compute `hour`, `minute`, `dayofweek`, `month`, `day`, `is_weekend`, and the cyclical time features. An incorrect timestamp will misalign the temporal features and degrade prediction.

The rule engine sends `datetime_str` from the sensor payload's `timestamp` field. If sensors publish without timestamps, the model falls back to server time — which is acceptable if the Pi's clock is correct.

### Requirement 3: Don't Flood the Endpoint

The rule engine rate-limits to one call every 5 seconds (`PREDICTION_RATE_LIMIT_SECONDS`). The model inference is not instantaneous — TE-GRU inference runs a Keras model, and XGBoost adds tree traversal overhead. Calling faster than ~5s is wasteful and may cause queuing.

### Requirement 4: All Sensor Fields Should Be Live

Every field you omit uses its Pydantic default:
- `temperature_c`: 25.0
- `humidity`: 50.0
- `lux`: 0.0
- `occupancy`: 1

Omitting fields means the model sees stale defaults. Always send all four live sensor values.

### Requirement 5: Occupancy Drives the Zero-Occupancy Override

When `occupancy == 0`, the model is bypassed and the output is forced to the CSV's lowest zero-occupancy energy value. If your hardware reports `occupancy=0` incorrectly (e.g., a sensor glitch), predictions will be artificially low. Verify that occupancy values match reality.

### Requirement 6: Keep the MQTT Feed Running

The MQTT bridge in `model_new_unsure/main.py` listens to `room/sensors` to keep the buffer current. If the MQTT broker is down, the buffer freezes. The service will still respond to `POST /predict` using stale buffer data, but predictions will drift from reality as the buffer ages.

---

## 6. Quick Testing

### Minimal test from the command line

```bash
curl -s -X POST http://127.0.0.1:5000/predict \
  -H "Content-Type: application/json" \
  -d '{"temperature_c": 32.0, "humidity": 62.0, "lux": 5.5, "occupancy": 4}' \
  | python3 -m json.tool
```

### Check model metadata

```bash
curl -s http://127.0.0.1:5000/metadata | python3 -m json.tool
```

Key fields to check:
- `model_ready` — must be `true`
- `buffer_size` — should be ≥ 12 (60 is full)
- `latest_energy` — should be a non-zero float (confirms energy data is flowing)
- `csv_ready` — `true` means the zero-occupancy override is armed
- `alpha`, `beta`, `q95`, `q80` — the MH calibration constants

### Testing with an empty body (uses all defaults)

```bash
curl -s -X POST http://127.0.0.1:5000/predict \
  -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool
```

This sends `temperature_c=25.0, humidity=50.0, lux=0.0, occupancy=1` with the current server time. Predictions will be low (~0.3–5 Wh) because the sensor values are mild.

---

## 7. Common Pitfalls

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| HTTP 503 "Model not loaded" | `tegru_xgb_mh_pipeline.joblib` missing or corrupt | Verify the file exists at `model_new_unsure/model_assets/tegru_xgb_mh_pipeline.joblib` |
| HTTP 503 "Insufficient history" | Buffer has < 12 rows on cold start + MQTT feed is down | Check that `mydatanew.csv` is present and MQTT broker is running |
| Predictions always ~0.3 Wh | `energy_kw` is 0.0 in MQTT, zero-occupancy override firing | Verify Group 1's INA219 is publishing real energy values; check occupancy values |
| Predictions fluctuate wildly | Buffer contains stale CSV data mixed with live data | Wait 60 readings for buffer to fully flush; check MQTT feed consistency |
| `latest_energy` is 0.0 or null | MQTT bridge not receiving `room/sensors` messages | Run `mosquitto_sub -t "room/sensors" -v` to verify data is flowing |
| Time features are wrong | `datetime_str` is missing or incorrect | Ensure the caller sends a valid ISO 8601 timestamp |
| Zero-occupancy rule not working | CSV failed to load | Check `model_assets/mydatanew.csv` exists and is readable |
