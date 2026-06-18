# TE-GRU + XGBoost + MH Model Integration

## Overview

The system uses a hybrid AI model for energy consumption prediction:

- **TE-GRU** (Temporal Encoding GRU) — a neural network that processes a **sequence of 8 timesteps** to capture temporal patterns
- **XGBoost** — a gradient boosting model that corrects the residual error of the TE-GRU
- **Metropolis-Hastings (MH)** — Bayesian calibration that combines both predictions with learned weights (`alpha`, `beta`)

**Final prediction:** `tegru_pred + (alpha × xgb_residual) + beta`

---

## What the Model Needs

The model takes **25 engineered features** as input, not just raw sensor data.

### Raw Inputs (from Group 1 sensors)

| Feature       | Source              |
|---------------|---------------------|
| temperature   | DHT22 sensor        |
| humidity      | DHT22 sensor        |
| lux           | BH1750 / LDR        |
| occupancy     | Ultrasonic / Radar   |

### Engineered Features (computed at prediction time)

| Feature            | Description                              |
|--------------------|------------------------------------------|
| hour               | Hour of day (0–23)                       |
| dayofweek          | Day of week (0=Mon, 6=Sun)               |
| month              | Month number (1–12)                      |
| day                | Day of month                             |
| is_weekend         | 1 if Saturday/Sunday, else 0             |
| hour_sin, hour_cos | Cyclical encoding of hour                |
| month_sin, month_cos | Cyclical encoding of month             |
| temp_x_humidity    | Temperature × Humidity interaction       |
| temp_x_occupancy   | Temperature × Occupancy interaction      |
| humidity_x_occupancy | Humidity × Occupancy interaction       |
| lux_x_occupancy    | Lux × Occupancy interaction              |
| hour_x_occupancy   | Hour × Occupancy interaction             |

Plus lag/rolling/trend features for energy and sensors (computed from history buffer).

---

## The 8-Row Window

The TE-GRU is a **sequence model** — it doesn't just see a single snapshot, it needs a sliding window of **8 consecutive timesteps**.

```
Timestep:    t-7   t-6   t-5   t-4   t-3   t-2   t-1   t-now
              │     │     │     │     │     │     │     │
              └─────┴─────┴─────┴─────┴─────┴─────┴─────┘
                        8 rows × 25 features
                         ↓
                     TE-GRU model
                         ↓
                    Base prediction
```

Each of these 8 rows is a fully-engineered feature vector (25 values). To build them, we need historical sensor + energy data.

---

## Where the History Comes From

### Cold Start (first boot)

When the system starts for the first time, it has no live history. The CSV file (`mydatanew.csv`, 42,240 rows of training data) provides the initial context:

```
Buffer seeded from CSV: 60 rows
```

The last 60 rows from the CSV are loaded into a rolling in-memory buffer.

### Live Data (after boot)

As Group 1 sends sensor data every ~2 seconds via MQTT, the ML service listens on `room/sensors` and adds each reading to the buffer:

```
[Group 1 ESP32] → room/hardware/nano → [hw_bridge] → room/sensors → [ML Service buffer]
```

Each reading includes:
- temperature, humidity, lux, occupancy (from sensors)
- energy (from INA219 power monitor)

Over time, live data pushes out the CSV-seeded rows. After ~2 minutes (60 readings × 2s), the entire buffer contains only real data.

### Energy Lag Features

The model was trained with energy lag features:
- `energy_lag1` = energy value 1 reading ago
- `energy_lag2` = energy value 2 readings ago
- `energy_lag3` = energy value 3 readings ago
- `energy_roll3/6/12` = rolling average over last 3/6/12 readings
- `energy_std6/12` = rolling standard deviation
- `energy_trend3/6/12` = trend (current − N steps ago)

These are computed from the **actual energy values in the buffer** — either CSV historical energy (cold start) or live energy from Group 1's INA219 sensor.

---

## Prediction Flow

```
Rule Engine (every ~5s)
     │
     ├── POST /predict { temperature_c, humidity, lux, occupancy }
     │
     ▼
ML Service (model_new_unsure/main.py)
     │
     ├── 1. Snapshot the rolling buffer (last 60 readings)
     ├── 2. Append current sensor values as a new row (energy = NaN)
     ├── 3. Run engineer_features() → compute all 25+ features
     ├── 4. Force energy lag features from actual buffer values
     ├── 5. Extract X_sequence (last 8 rows) for TE-GRU
     ├── 6. Extract X_current (last 1 row) for XGBoost
     ├── 7. tegru_pred = TE-GRU(X_sequence)
     ├── 8. residual_pred = XGBoost(X_current)
     ├── 9. final = tegru_pred + alpha × residual_pred + beta
     └── 10. Return { predicted_energy_wh, lower_bound, upper_bound }
```

---

## Confidence Bounds

The model provides prediction intervals:
- **95% interval:** `prediction ± Q95` (Q95 = 2.0577 Wh)
- **80% interval:** `prediction ± Q80` (Q80 = 0.8464 Wh)

Example: If EDFI = 11.07 Wh → range is [9.01 – 13.13] Wh (95%).

---

## Files

| File | Purpose |
|------|---------|
| `model_new_unsure/main.py` | FastAPI service (prediction endpoint) |
| `model_new_unsure/model_assets/tegru_xgb_mh_pipeline.joblib` | Trained model bundle |
| `model_new_unsure/model_assets/mydatanew.csv` | CSV for cold-start buffer seeding |
| `model_new_unsure/applastvalues.py` | Original Streamlit app (reference only) |

---

## Dependencies

```
tensorflow        # TE-GRU Keras model
xgboost           # Residual correction model
catboost           # Required by the joblib bundle
scikit-learn      # Scaler inside SequenceRegressorPipeline
joblib            # Model serialisation
fastapi + uvicorn # HTTP service
pandas + numpy    # Feature engineering
paho-mqtt         # Buffer population from live sensors
```

---

## Configuration (.env)

```env
MODEL_ASSET_DIR=model_new_unsure/model_assets
ML_SERVICE_SCRIPT=model_new_unsure/main.py
```

To switch back to the LightGBM model, change these to:
```env
MODEL_ASSET_DIR=LIGHT_ML_MODEL/model_assets
ML_SERVICE_SCRIPT=LIGHT_ML_MODEL/main.py
```
