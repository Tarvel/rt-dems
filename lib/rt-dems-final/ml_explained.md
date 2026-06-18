# ML Prediction Pipeline — How It Works

## Overview

The system uses a **hybrid ML model** to predict energy consumption (Wh). Two models run in parallel and their outputs are blended for the final prediction. A historical **context window** from a CSV dataset provides the model with memory of past patterns.

---

## 1. The Training Data (`datanew40.csv`)

The model was trained on **5-minute interval** data:

```
timestamp,time_of_day,day_of_week,temperature,humidity,lux,occupancy,energy
5/8/2026 13:00,13,Friday,29.8,61.7,5.6,0,0.4
5/8/2026 13:05,13,Friday,29.8,61.8,5.7,0,0.4
5/8/2026 13:10,13,Friday,30.8,62.0,5.8,0,0.4
...
```

Each row is a **5-minute averaged** reading of temperature, humidity, light, occupancy, and the corresponding energy consumption in Wh.

The CSV has **40,001 rows** covering roughly **139 days** of continuous 5-minute data.

---

## 2. The Hybrid Model Architecture

### Model 1: TE-GRU (Time-Enhanced Gated Recurrent Unit)
- **Type**: Neural network (recurrent)
- **Input**: 48 consecutive rows of sensor features = **4 hours of history** (48 × 5 min)
- **Also receives**: the target hour as a separate input (time awareness)
- **What it learns**: Temporal patterns — "energy rises at 8am, drops at 10pm", "weekday patterns differ from weekends"
- **Output**: a single predicted energy value (Wh)
- **Format**: TFLite (`tegru_model.tflite`)

### Model 2: LightGBM (Gradient Boosted Decision Trees)
- **Type**: Tree-based model
- **Input**: 1 row of features (the current reading + engineered lag features)
- **What it learns**: Instant correlations — "when temp=32°C, humidity=60%, occupancy=0 → energy ≈ 0.4 Wh"
- **Output**: a single predicted energy value (Wh)
- **Format**: Joblib (`lgb_model.joblib`)

### Blending
```
final_prediction = w × GRU_prediction + (1 - w) × LightGBM_prediction
```
Where `w` is adaptively tuned using Bayesian estimation (Metropolis-Hastings sampler), or a pre-trained fixed weight from `bayes_weight_gru.joblib`.

### Optional Residual Stack (XGBoost + Uncertainty)
If `xgb_residual_model.joblib` and `uncertainty_model.joblib` are present:
- XGBoost learns to **correct** the blend's residual error
- Uncertainty model estimates **how confident** the prediction is
- `optimal_z.joblib` provides the confidence multiplier for bounds

---

## 3. The 72-Row Context Window — Why It Exists

### The Problem
Both models need **historical context** to make predictions:
- TE-GRU needs 48 rows of past data to see temporal patterns
- LightGBM needs lag features like "temperature 1 hour ago" and "average temperature over last 2 hours"

In production, we don't have 4+ hours of stored model-ready data — we only have the **current live sensor reading**. So the system uses the CSV as a substitute for historical context.

### How It Works

```
WINDOW_SIZE = SEQ_LENGTH + FEATURE_HISTORY
           = 48          + 24
           = 72
```

The system grabs **73 consecutive rows** from the CSV:

```
┌─────────────────────────────────────────────────────────────────────┐
│  CSV Row [i-72] ──────────────── CSV Row [i-1]  │  CSV Row [i]     │
│  ◄──── 72 rows of historical context ──────────►  │  YOUR LIVE DATA  │
│                                                    │  (injected here) │
│  Used for:                                         │                  │
│  - GRU sees 48 rows of sequence                    │                  │
│  - Lag features (temp_lag1, temp_lag24, etc.)       │                  │
│  - Rolling averages (temp_mean3, temp_mean24)       │                  │
└─────────────────────────────────────────────────────────────────────┘
```

### Why This Makes Predictions More Accurate

Without context, the model only knows "right now it's 32°C and 60% humidity." That's not enough — energy patterns depend heavily on **trends**:

- **Is temperature rising or falling?** (lag features)
- **What time of day is it?** (hour encoding)
- **Is this a weekday or weekend?** (dow encoding)
- **What did the last 4 hours look like?** (GRU sequence)

The CSV provides realistic context that matches these patterns. For example, if it's currently 3pm on a Wednesday, the system finds a similar 3pm-Wednesday window in the CSV. The 72 rows before that point give the model a realistic "memory" of what the previous 6 hours looked like, even though we don't have our own 6 hours of stored data.

### Time Matching Logic (`_find_csv_index()`)

When a prediction request arrives, the service finds the best CSV position:

1. **Exact match** (within 1 hour of timestamp) → best case
2. **Same month + same day-of-week + same hour** → very good
3. **Same day-of-week + same hour** → good (e.g., any Wednesday at 3pm)
4. **Same hour** → acceptable fallback (e.g., any day at 3pm)
5. **Default** → starts from row 72

This ensures the historical context reflects similar time patterns to the current moment.

---

## 4. Feature Engineering

From the 73-row window, the following features are computed:

| Feature | Meaning | Used by |
|---|---|---|
| `temperature` | Current temp (°C) | Both |
| `humidity` | Current humidity (%) | Both |
| `lux` | Current light (lx) | Both |
| `occupancy` | Current occupancy count | Both |
| `hour` | Hour of day (0–23) | Both |
| `day_of_week` | Day (0=Mon, 6=Sun) | LightGBM |
| `time_of_day` | Same as hour | LightGBM |
| `hour_sin`, `hour_cos` | Cyclical hour encoding | Both |
| `dow_sin`, `dow_cos` | Cyclical day encoding | Both |
| `is_weekend` | 1 if Sat/Sun, else 0 | LightGBM |
| `temperature_lag1` | Temp from 1 row ago (5 min) | LightGBM |
| `temperature_lag24` | Temp from 24 rows ago (2 hours) | LightGBM |
| `temperature_mean3` | Avg temp over last 3 rows (15 min) | LightGBM |
| `temperature_mean24` | Avg temp over last 24 rows (2 hours) | LightGBM |

Same lag/rolling features for `humidity` and `lux`.

---

## 5. How Live Data Gets Sent to the Model

### Step 1: Rule Engine Collects Sensor Data
Hardware sensors publish readings every ~2 seconds to MQTT (`room/sensors`). The rule engine buffers all readings in a rolling 5-minute window.

### Step 2: 5-Minute Averaging
Every 5 seconds (rate-limited), the rule engine:
- Takes all buffered readings from the last 300 seconds
- Averages: temp, humidity, lux, occupancy
- This matches the CSV training format where each row = 5 minutes of averaged data

### Step 3: POST /predict
The averaged values are sent to the ML service:
```json
{
  "temperature_c": 31.4,
  "humidity": 60.9,
  "lux": 5.5,
  "occupancy": 4,
  "datetime_str": "2026-05-29T15:00:00+01:00"
}
```

### Step 4: ML Service Builds Context
1. Uses `datetime_str` to find matching CSV position via `_find_csv_index()`
2. Extracts 73 rows around that position
3. **Injects your live averages into the last row**, replacing the CSV values:

```python
live_window.loc[last_row, "Temperature_C"] = 31.4       # Your averaged temp
live_window.loc[last_row, "Humidity_%"]     = 60.9       # Your averaged humidity
live_window.loc[last_row, "Luminous_Intensity_Lux"] = 5.5
live_window.loc[last_row, "Occupancy"]      = 4
live_window.loc[last_row, "Energy_Wh"]      = NaN       # This is what we're predicting
```

### Step 5: Prediction
Both models run on the engineered features:
- GRU reads the last 48 rows as a sequence
- LightGBM reads the last 1 row of features
- Results are blended, residual-corrected, and uncertainty bounds added

### Step 6: Response
```json
{
  "predictions": {
    "hybrid_final_wh": 6.2161,
    "safety_lower_bound_wh": 5.2837,
    "safety_upper_bound_wh": 7.1485,
    "base_gru_wh": 6.1,
    "lgbm_wh": 6.4,
    "hybrid_weight_gru": 0.92
  }
}
```

---

## 6. Is the 5-Minute Average Accurate?

**Yes — it directly matches the training data format.**

The CSV (`datanew40.csv`) was created from 5-minute averaged sensor readings. Each row represents a 5-minute window:

```
5/8/2026 13:00 → avg from 13:00 to 13:05
5/8/2026 13:05 → avg from 13:05 to 13:10
```

When the rule engine averages 300 seconds of live sensor data and sends it to the model, it's producing a value in the **exact same format** the model was trained on. This is critical because:

- Sending raw single-second readings would be **noisy** — the model never saw that format
- Averaging smooths out sensor noise (temporary spikes, measurement jitter)
- The model's learned patterns (lag features, rolling means) assume 5-minute granularity

---

## 7. End-to-End Flow

```
Hardware sensors (every ~2 seconds)
  │
  ▼
hw_bridge → MQTT (room/sensors)
  │
  ▼
Rule Engine:
  ├── Buffers sensor readings in rolling 5-min window
  ├── Every ~5 seconds: averages buffer → POST /predict (cached internally)
  └── Every 5 minutes (decision timer):
        │
        ▼
      ML Service receives: { temp, hum, lux, occ, datetime }
        │
        ├── 1. Find best CSV position for the current time
        ├── 2. Extract 73 rows (72 history + 1 current)
        ├── 3. Inject live averaged sensors into last row
        ├── 4. Engineer features (lags, rolling means, time encoding)
        ├── 5. GRU: 48-row sequence → prediction
        ├── 6. LightGBM: 1-row features → prediction
        ├── 7. Blend: w×GRU + (1-w)×LightGBM
        ├── 8. Optional: XGBoost residual correction
        ├── 9. Uncertainty bounds: [lower, upper]
        └── 10. Return predicted_energy_wh (EDFI)
              │
              ▼
      Rule Engine: EDFI → threshold → Mode A/B/C → MQTT → ESP32 relays
```

---

## 8. Current Limitation

The 72-row context window is sourced from the **static CSV file**, not from live data we've collected ourselves. This means:

- The lag features (temp_lag1, humidity_lag24, etc.) come from the CSV, not from what actually happened in our room
- The GRU's 48-row sequence is from CSV history, not our real 4-hour history

**Future improvement**: As the system accumulates enough real sensor data in the database, the context window could eventually be built from our own historical data instead of the CSV. This would make predictions more specific to our room's actual patterns.
