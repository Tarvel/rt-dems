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

## 1b. Exactly What Data Goes to our Model to Make a Prediction

When a prediction is requested, the system combines **live HTTP parameters** and **cached history** to build the input. Here is the exact data that flows in:

### 1. The HTTP Request Payload (From Rule Engine)
Every 5 seconds, the rule engine sends a POST request to the model service on `http://localhost:5000/predict`. This request contains the **current environmental snapshot**:
```json
{
  "temperature_c": 31.6,
  "humidity": 60.9,
  "lux": 3.2,
  "occupancy": 4,
  "datetime_str": "2026-06-18T13:58:16"
}
```
*Note: The energy value for "now" is omitted because that is what we are trying to predict!*

### 2. The Cached Buffer History (From MQTT Bridge)
The model service retrieves the last **12 readings** from its live rolling buffer. Each of these 12 rows contains:
* `timestamp` (time of reading)
* `temperature` (measured live)
* `humidity` (measured live)
* `lux` (measured live)
* `occupancy` (measured live)
* `energy` (measured live cumulative energy from INA219 sensor)

### 3. Combining and Engineering the Features
The service appends the new HTTP snapshot to the 12 history rows, creating a **13-row working dataset**. It then:
1. Calculates time/weekend variables from the timestamps.
2. Combines variables (e.g., Temperature × Humidity).
3. Computes lag, trend, and rolling features over the history rows.
4. Generates a final dataset where each of the rows contains **74 columns** (engineered features).

### 4. Slicing for the Models
The final engineered dataset is split to feed the two model parts:
* **For TE-GRU:** We slice the **last 8 rows** of the 74-column dataset. This sequence of $8 \times 74$ features is passed to the Keras model.
* **For XGBoost:** We slice **only the 1 current row** (the last row) of the 74-column dataset. This single row is passed to the XGBoost model.

---

## 2. The 8-Row Window & The 12 Lag History Rows

Because the model uses a recurrent neural network (TE-GRU), it cannot make a prediction using only a single snapshot of data. It expects an input window of **8 consecutive rows** of fully engineered features.

```
Row 1 (t-7)  --> [ 74 individual features ]
Row 2 (t-6)  --> [ 74 individual features ]
Row 3 (t-5)  --> [ 74 individual features ]
Row 4 (t-4)  --> [ 74 individual features ]
Row 5 (t-3)  --> [ 74 individual features ]
Row 6 (t-2)  --> [ 74 individual features ]
Row 7 (t-1)  --> [ 74 individual features ]
Row 8 (t-now)--> [ 74 individual features ]
```

### What is the content of each of the 8 rows?
Each of the 8 rows in the sequence is a vector of **74 engineered feature columns**. These columns are calculated dynamically for each timestep in the window:

1. **Raw Sensor Telemetry (4 columns):**
   * `temperature` (DHT22 reading in °C)
   * `humidity` (DHT22 reading in %)
   * `lux` (BH1750 / LDR ambient light level)
   * `occupancy` (Ultrasonic / radar room count)

2. **Calendar & Time Indicators (6 columns):**
   * `hour` (0 to 23)
   * `minute` (0 to 59)
   * `dayofweek` (0 to 6, where 0 is Monday)
   * `month` (1 to 12)
   * `day` (1 to 31)
   * `is_weekend` (1 if Saturday or Sunday, otherwise 0)

3. **Trigonometric Encoded Time (4 columns):**
   * `hour_sin` / `hour_cos` (converts hour to a circular pattern so 23:59 is next to 00:00)
   * `month_sin` / `month_cos` (converts month to circular pattern)

4. **Interaction Cross-Features (5 columns):**
   * `temp_x_humidity` (Temperature × Humidity)
   * `temp_x_occupancy` (Temperature × Occupancy)
   * `humidity_x_occupancy` (Humidity × Occupancy)
   * `lux_x_occupancy` (Lux × Occupancy)
   * `hour_x_occupancy` (Hour × Occupancy)

5. **Historical Energy Lags & Statistics (22 columns):**
   * *The model expects these features in two aliases: `energy_*` and `real time energy_*`.*
   * **Lags:** `lag1`, `lag2`, `lag3` (the energy consumed 1, 2, and 3 steps ago)
   * **Rolling Averages:** `roll3`, `roll6`, `roll12` (the average energy over the last 3, 6, and 12 steps)
   * **Rolling Variances:** `std6`, `std12` (the standard deviation over the last 6 and 12 steps)
   * **Trends:** `trend3`, `trend6`, `trend12` (how much energy changed compared to 3, 6, or 12 steps ago)

6. **Historical Sensor Lags & Statistics (33 columns):**
   * Computed for each sensor (`temperature`, `humidity`, `lux`, and `occupancy`) over the buffer:
   * **Lags:** `lag1`, `lag2`, `lag3` (what the sensor read 1, 2, and 3 steps ago)
   * **Rolling Averages:** `roll3`, `roll6`, `roll12` (moving average of the sensor values)
   * **Rolling Variances:** `std6`, `std12` (moving standard deviation of the sensor values)
   * **Trends:** `trend3`, `trend6`, `trend12` (the difference between the previous step and N steps ago)

### Why is it exactly 74 features?
A machine learning model doesn't just see a single table row with 4 values (`temp`, `humidity`, `lux`, `occupancy`). It needs multiple views of the data to capture time patterns. 

Here is the exact breakdown of how we get 74 columns (features):
* **Raw inputs:** 4 columns
* **Calendar columns:** 6 columns
* **Trigonometric cyclical time:** 4 columns
* **Interaction cross-features:** 5 columns
* **Energy statistics (duplicated under 2 aliases):** 22 columns ($11 \text{ features} \times 2 \text{ prefixes}$)
* **Sensor history statistics:** 33 columns ($11 \text{ features} \times 3 \text{ sensors}$)
* **Total:** $4 + 6 + 4 + 5 + 22 + 33 = 74$ features.

To predict, the TE-GRU neural network takes this 74-feature vector for each of the last 8 steps in the sequence. It receives a grid of **8 rows × 74 columns = 592 total inputs** to make its single prediction.

### Are past predictions used as lag inputs?
**No. Past predictions are NOT used to calculate the lag and rolling energy features.**

* **Why?** If the model made a wrong prediction in the last step, and we fed that prediction back as the input (`energy_lag1`) for the next step, the prediction error would grow larger and larger over time (error propagation feedback loop).
* **What we do instead:** We use the **actual, live energy consumed** (measured by the physical INA219 sensor on the hardware, which we get from `room/sensors` and store in the history buffer). By using the real, measured energy for the lag calculations, the model is always anchored in physical reality and never drifts off-course.

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

#### 💡 Let's understand with a live numerical example:
Suppose the last 12 readings in our rolling buffer contain these actual energy values (in Watt-hours):

* `t-12` (1 hour ago): **10.0 Wh**
* `t-11`: **10.2 Wh**
* `t-10`: **10.5 Wh**
* `t-9`: **10.1 Wh**
* `t-8`: **10.3 Wh**
* `t-7`: **10.8 Wh**
* `t-6` (30 mins ago): **10.6 Wh**
* `t-5`: **10.4 Wh**
* `t-4`: **10.9 Wh**
* `t-3` (15 mins ago): **11.2 Wh**
* `t-2` (10 mins ago): **11.0 Wh**
* `t-1` (5 mins ago): **11.5 Wh**
* `t-now` (Current prediction step): **[We are predicting this! It is NaN during feature building]**

When `/predict` is called, the feature engineering computes these columns for `t-now`:

1. **Lags (Where was energy in the past?):**
   * `energy_lag1` = value at `t-1` = **11.5 Wh** (energy consumed in the last 5 minutes)
   * `energy_lag2` = value at `t-2` = **11.0 Wh**
   * `energy_lag3` = value at `t-3` = **11.2 Wh**

2. **Rolling Averages (What is the recent average?):**
   * `energy_roll3` = average of (`t-1`, `t-2`, `t-3`) = $(11.5 + 11.0 + 11.2) / 3$ = **11.23 Wh**
   * `energy_roll6` = average of `t-1` through `t-6` = $(11.5 + 11.0 + 11.2 + 10.9 + 10.4 + 10.6) / 6$ = **10.93 Wh**
   * `energy_roll12` = average of all 12 history steps = **10.70 Wh**

3. **Trends (Is consumption going up or down?):**
   * `energy_trend3` = `lag1 - lag3` = $11.5 - 11.2$ = **+0.3 Wh** (energy is trending up by 0.3 Wh compared to 15 minutes ago)
   * `energy_trend6` = `lag1 - lag6` = $11.5 - 10.6$ = **+0.9 Wh** (trending up by 0.9 Wh compared to 30 minutes ago)

4. **Rolling Standard Deviation (How stable is the consumption?):**
   * `energy_std6` = standard deviation of the last 6 steps (`t-1` to `t-6`) = **0.36 Wh** (shows how much the usage is fluctuating)

#### Why do we see both `energy_*` and `real time energy_*` prefixes?
The training code was written using the column name `"real time energy"` as the target, but some parts of the saved pipeline expect the prefix `"energy"`. 

To prevent errors during model execution, **every feature listed above is calculated twice and duplicated under both names**:
* `real time energy_lag1` = `energy_lag1` = **11.5 Wh**
* `real time energy_roll3` = `energy_roll3` = **11.23 Wh**
* `real time energy_trend3` = `energy_trend3` = **+0.3 Wh**

This guarantees that the model receives exactly the format it expects.

---

## 4c. Prediction Frequency vs. Control Loop Decisions

You might notice in the logs that the rule engine sends a POST request to `/predict` every **5 seconds**, even though the system is supposed to make control decisions every **5 minutes**. Here is why:

1. **Real-time Dashboard Visibility:** The Smart Room dashboard has a live-updating display. By requesting a new prediction every 5 seconds, the user interface can display a smooth, real-time prediction graph reflecting ambient changes immediately.
2. **5-Minute Decision Boundary:** The actual relay state switches (Mode A, B, or C) are only evaluated at the 5-minute mark (configured by `DECISION_INTERVAL_MINUTES=5` in `.env`). The rule engine simply uses the latest prediction value that it pulled from its continuous poll when this 5-minute boundary is hit.

---

## 4d. What Data Ensures the Model Predicts Well?

The model's accuracy is heavily dependent on specific streams of data. Without these, predictions would drift or fail:

### 1. From Group 1's Hardware (ESP32 Telemetry)
* **Actual Cumulative Energy (`energy_kw` from INA219):** This is the single most critical feature. The model relies on knowing what was consumed in the past (lags and rolling averages). If Group 1 sends a constant `0.0` or fails to send this reading, the lag features will collapse to zero, causing the model to underpredict.
* **Occupancy Count (Ultrasonic/Radar):** Human presence is the primary driver of consumption (people turn on lights, AC, water heaters). The model uses this to separate baseline standby usage from high-occupancy usage.
* **Ambient Conditions (`temperature`, `humidity`, `lux`):** High temperatures dictate high A/C load, while lux levels help the model determine time-of-day brightness relationships.

### 2. From our Backend & Rule Engine
* **Precise System Clock:** The model uses circular time transformations (`hour_sin`, `month_cos`) to learn peak energy hours. A synchronized system clock ensures time features align with historical training data.
* **5-Minute Resampling Resolution:** The training dataset was built on 5-minute intervals. The backend buffer ensures that history records are appended at this frequency so that a 12-row window represents exactly 60 minutes of real-world history.

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


