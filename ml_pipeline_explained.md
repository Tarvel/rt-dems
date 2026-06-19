# ML Prediction Pipeline — How It Actually Works

## The CSV Data (Group 2's Training Data)

The model was trained on `datarig40.csv` which has **20-minute intervals**:

```
timestamp,time_of_day,day_of_week,temperature,humidity,lux,occupancy,energy
5/8/2026 13:00,13,Friday,30.4,61.8,5.7,0,0.4
5/8/2026 13:20,13,Friday,31.6,61.6,6,0,0.4
5/8/2026 13:40,13,Friday,31.7,61.2,6.4,0,0.4
5/8/2026 14:00,14,Friday,31.8,60.8,6.7,0,1
```

Each row = one 20-minute reading of: temp, humidity, lux, occupancy, and **energy (Wh)**.

---

## The Model Architecture

Group 2 built a **hybrid model** — two separate models whose outputs are blended:

### Model 1: TE-GRU (Time-Enhanced Gated Recurrent Unit)
- A neural network that looks at **48 consecutive rows** of history (48 × 20 min = 16 hours)
- Captures temporal patterns: "energy usually rises at 8am, drops at 10pm"
- Also takes the **target hour** as an extra input (time awareness)
- Output: a single predicted energy value (Wh)

### Model 2: LightGBM (Gradient Boosted Decision Trees)
- Takes **one row** of features (the current reading + engineered features)
- Captures instant correlations: "when temp=32, humidity=60, occupancy=0 → energy tends to be X"
- Output: a single predicted energy value (Wh)

### Blending
The final prediction = `w × GRU_prediction + (1-w) × LightGBM_prediction`
where `w` is adaptively tuned using Bayesian estimation (Metropolis-Hastings).

---

## What Happens When Rule Engine Calls `/predict`

### Step 1: Build Context Window (73 rows)

```
WINDOW_SIZE = 48 (SEQ_LENGTH) + 24 (FEATURE_HISTORY) = 72
```

The code grabs 73 consecutive rows from the CSV (72 history + 1 current):

```
Row 0-71:  Historical data from CSV (lag features, rolling averages)
Row 72:    The "current" row — YOUR live sensor data gets injected here
```

### Step 2: Match CSV Position to Current Time

`_find_csv_index()` tries to find the best matching CSV row for the current timestamp:
1. Exact match within 1 hour? → use it
2. Same month + day_of_week + hour? → use it
3. Same day_of_week + hour? → use it  
4. Same hour? → use it
5. None found → use default position

This ensures the lag features (temperature patterns from the previous 16 hours) are contextually similar to the current time.

### Step 3: Inject Live Sensor Data

Your live sensor values replace the **last row** of the window:

```python
live_window.loc[idx, "Temperature_C"] = sensor.temperature_c    # Your live temp
live_window.loc[idx, "Humidity_%"]    = sensor.humidity           # Your live humidity
live_window.loc[idx, "Luminous_Intensity_Lux"] = sensor.lux     # Your live lux
live_window.loc[idx, "Occupancy"]     = sensor.occupancy         # Your live occupancy
live_window.loc[idx, "Energy_Wh"]     = np.nan                   # Unknown (we're predicting this!)
```

### Step 4: Feature Engineering

`_build_feature_frame()` creates derived features from the 73-row window:

| Feature | What it is |
|---|---|
| `temperature_lag1` | Temperature from 20 min ago |
| `temperature_lag24` | Temperature from 8 hours ago |
| `temperature_mean3` | Average temp over last 3 readings (1 hour) |
| `temperature_mean24` | Average temp over last 24 readings (8 hours) |
| `hour_sin`, `hour_cos` | Cyclical time encoding |
| `dow_sin`, `dow_cos` | Day-of-week encoding |
| `is_weekend` | Boolean weekend flag |

Same for humidity and lux.

### Step 5: Run Both Models

```python
# GRU: feed 48 rows of scaled features → predict energy
gru_raw = _invoke_gru(gru_sequence, target_hour)

# LightGBM: feed 1 row of features → predict energy
lgbm_raw = lgb_model.predict(lgb_input)[0]

# Blend them
hybrid_final_wh = w * gru_raw + (1-w) * lgbm_raw
```

### Step 6: Uncertainty Bounds

```python
lower_bound = max(0, hybrid_final_wh - 1.5 × residual_std)
upper_bound = hybrid_final_wh + 1.5 × residual_std
```

---

## The Key Question: Averaging

> Group 2 says they trained based on 20-minute intervals. Should we average sensor data before sending?

**Current behavior**: The rule engine sends a **single live snapshot** (one reading) to the model every 5 minutes. The model treats that as the "current" row.

**What Group 2 probably intended**: Each row in the training CSV is an **average** over a 20-minute window. So ideally, you'd average your sensor readings over 20 minutes and then send that average to the model.

### What this means in practice:
- Right now: temp=32.1 at the exact moment of prediction
- Ideally: temp=31.8 (average of all readings in the last 20 minutes)

The difference is small in practice because sensor values don't change drastically within 20 minutes. But for correctness, the rule engine could average the sensor buffer before calling `/predict`.

---

## Summary Flow

```
Hardware (every 2s)
  → hw_bridge publishes to room/sensors
    → Rule engine stores latest_sensor
      → Every 5 min, rule engine:
        1. Takes latest_sensor snapshot
        2. POST /predict → ML service
        3. ML finds best CSV context (73 rows)
        4. Injects your live sensors into last row
        5. Engineers lag/rolling features from CSV history
        6. GRU predicts from 48-row sequence
        7. LightGBM predicts from 1-row features
        8. Blends: hybrid = w×GRU + (1-w)×LightGBM
        9. Returns predicted_energy_wh (EDFI)
        10. Rule engine: EDFI → threshold → mode A/B/C
```
