# TE-GRU + XGBoost + MH Model Integration

This document explains in plain English how the machine learning model is integrated into the Smart Room system, how the feature history buffer works, and what each sensor value contributes to the final energy predictions.

---

## 1. Overview of the Model

The prediction system is a hybrid pipeline combining three parts:
1. **TE-GRU (Temporal Encoding Gated Recurrent Unit):** A neural network that looks back at a sequence of **8 timesteps** (readings) to capture patterns in energy consumption over time.
2. **XGBoost:** A tree-based model that looks at the current single timestep to predict and correct the residual error of the TE-GRU's forecast.
3. **Metropolis-Hastings (MH):** A Bayesian blending layer that weights the two models together and calculates safety bounds (prediction intervals).

**Final Predicted Energy (EDFI) =** `TE-GRU base prediction` + `(alpha × XGBoost correction)` + `beta`

---

## 2. The 8-Row Window & The 12 Lag History Rows

Because the model uses a recurrent neural network (TE-GRU), it cannot make a prediction using only a single snapshot of data. It expects an input window of **8 consecutive rows** of fully engineered features.

To build these 8 rows, the model needs to calculate rolling statistics and differences over the last **12 intervals** of data. These are called **lag history features**:

* **Lag 1, 2, and 3:** The values from 1, 2, and 3 steps ago.
* **Roll 3, 6, and 12:** The average value over the last 3, 6, and 12 steps.
* **Std 6 and 12:** The standard deviation (variation) over the last 6 and 12 steps.
* **Trend 3, 6, and 12:** The change in values between the previous step and the step 3, 6, or 12 intervals ago.

### How this works under the hood:
The FastAPI service maintains an in-memory queue (the `history_buffer`) with a maximum capacity of 60 records.
1. When a prediction is requested, the service copies the history buffer.
2. It appends the current sensor readings (the new row) to the end of the history.
3. It runs the feature engineering script to calculate the lags, rolling averages, and trends over the 12-interval history.
4. It extracts the last 8 fully engineered rows as a sequence to feed into the TE-GRU model.

---

## 3. Where the History Data Comes From

### Cold Start (First Boot)
When you first start the system, the history buffer is empty. To prevent the model from failing or outputting garbage, the service loads the last 60 records from `mydatanew.csv` (the training dataset) into the buffer at boot.

### Live Ingestion (During Run)
The FastAPI service runs an MQTT bridge client that subscribes to the `room/sensors` topic. Every time a new telemetry payload is published:
* The service extracts: `temperature`, `humidity`, `lux`, `occupancy`, and the cumulative `energy_kw` (provided by Group 1's hardware).
* These live values are appended to the in-memory buffer.
* The oldest reading is evicted. Within 2 minutes of continuous operation, the CSV data decays out, and the buffer is composed entirely of live hardware readings.

---

## 4. Role of Live Sensors vs. Live Energy

The features in the model are split into two categories:

### A. Live Environmental Sensors (DHT22, BH1750, Presence)
* **What they are:** Temperature, humidity, lux (light level), and occupancy.
* **What they do:** These represent the **drivers** of energy consumption. If the temperature is high (hot day) or occupancy is high (many people in the room), energy consumption increases. Lux levels tell the model if it is daytime or nighttime.
* **How they enter the model:** The rule engine reads these from the sensors and sends them via HTTP POST to `/predict`.

### B. Live Energy (INA219 Power Monitor)
* **What it is:** The cumulative energy reading (`energy_kw` normalized from Group 1's hardware).
* **What it does:** This is the feedback loop. The model needs to know how much energy was *actually* consumed in the previous steps to forecast what will be consumed next.
* **How they enter the model:** The FastAPI service listens to `room/sensors` on MQTT. It extracts the real energy value and stores it in the history buffer. When you call `/predict`, the model uses these buffered energy values to calculate the **energy lag, trend, and rolling features** (like `energy_lag1`, `energy_roll3`, etc.).

---

## 5. What the Predicted Energy Does

The rule engine gets the prediction (`predicted_energy_wh`) from the model and uses it to manage the smart room's power grid:

1. **Anomaly and Range Checks:** The Metropolis-Hastings layer returns an upper and lower safety bound. If the upper bound exceeds critical demand thresholds, the system can trigger alarms or prevent additional loads from turning on.
2. **Dynamic Mode Selection:** The predicted value (EDFI) is evaluated alongside the battery state of charge (SoC) to select the room's operational mode:
   * **Mode A (All Loads ON):** Only active when predicted demand is high and the battery is full ($\ge 80\%$).
   * **Mode B (Average Load - HVAC + Freezer ON):** Used during moderate demand, or during peak demand when the battery is at medium charge ($\ge 60\%$).
   * **Mode C (Baseline Load - Freezer Only ON):** Enforced when predicted demand is very low, or when the battery is depleted ($< 60\%$), keeping only critical appliances running.

---

## 6. How it was Integrated (Step-by-Step)

1. **Streamlit to FastAPI:** Converted the Streamlit code into a fast, headless FastAPI endpoint (`model_new_unsure/main.py`) running on Port 5000.
2. **Virtual Environment Setup:** Installed the deep learning dependency (`tensorflow`), the tree model dependencies (`xgboost`, `catboost`), and standard library tools (`scikit-learn`, `joblib`) on the Raspberry Pi.
3. **Column Mapping Alignment:** Fixed a critical bug where the model expected `"real time energy"` but the database used `"energy"`. We added an aliasing step in `engineer_features()` so that both names are populated.
4. **Rule Engine Response Adaptor:** The old model wrapped its output in a `predictions` key. The new model returned flat keys. We updated `rule_engine.py` to support both formats.
5. **CSV Download Upgrade:** Updated Django's `CSVDownloadView` to support date filters (`start=today`, `end=today`) and output every sensor reading aligned by minute.


