# TE-GRU + XGBoost + MH Energy Prediction: Lag Features & Sequence Window Explained

This document explains how the **hybrid AI pipeline** (combining Gated Recurrent Units, XGBoost Residual Correction, and Metropolis-Hastings Bayesian Calibration) process temporal sequence data, lag features, and 5-minute energy intervals.

---

## 1. Core Architecture & Data Flow

The hybrid prediction pipeline consists of three stages:
1. **TE-GRU Sequence Model (Base)**: Learns complex temporal trends from the last **8 sequence rows** (40 minutes of history).
2. **XGBoost Regressor (Residual)**: Predicts the error (residual) of the GRU model using the most recent tabular row.
3. **Metropolis-Hastings (MH) Calibration**: Applies a Bayesian correction to adjust the final prediction and generate 95% and 80% confidence bounds.

```
                  ┌──────────────────────────────┐
                  │ 8-Row Sequence Window (40m)  │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                     ┌──────────────────────┐
                     │ TE-GRU Sequence model│ ──(Raw prediction)──┐
                     └──────────────────────┘                    │
                                                                 ▼
 ┌──────────────────┐                                   ┌────────────────┐
 │ Current Row (T)  │ ──(Feature engineering)──▶ XGBoost │ Residual corr. │ ──▶ Final calibrated EDFI Wh
 └──────────────────┘                                   └────────────────┘
```

---

## 2. 5-Minute Energy Accumulation & Scaling

The AI models were trained on data sampled at **5-minute intervals**. However, live hardware data from the bridge (`hw_bridge.py`) is published every ~2 seconds. Pushing high-frequency readings directly into the model's history buffer would break the lag features (e.g., shrinking `lag1` from "5 minutes ago" to "2 seconds ago").

To solve this, the pipeline performs **time-cadence normalization**:
1. **Hardware Bridge (`hw_bridge.py`)**: Integrates power over a rolling 60-second window to calculate the **1-minute energy (Wh)**.
2. **ML Service (`model_new_unsure/main.py`)**: Subscribes to these 1-minute values and accumulates them.
3. **Scaling Step**: Every 5 minutes (300 seconds), it averages the accumulated 1-minute energy values and multiplies by `5.0` to calculate the **total Wh consumed in the 5-minute block**.

$$\text{5-Minute Energy (Wh)} = \left( \frac{\sum E_{1min}}{N} \right) \times \left( \frac{300}{60} \right)$$

This scaled 5-minute value is appended to the rolling history buffer to drive lag calculations.

---

## 3. What is a "Lag Feature"?

A **lag** is a feature representation of what happened in the recent past. Because the model is predicting the energy of the *current* 5-minute block ($T$), the actual energy consumed during this block is not yet known. The model must rely on historical patterns:
* **`energy_lag1`**: The energy consumed in the 5-minute block that just completed ($T - 5$ minutes).
* **`energy_lag2`**: The energy consumed two blocks ago ($T - 10$ minutes).
* **`energy_lag3`**: The energy consumed three blocks ago ($T - 15$ minutes).

Additionally, the pipeline computes rolling stats over these lags:
* **`energy_roll3` / `energy_roll6` / `energy_roll12`**: Moving averages of the last 3, 6, and 12 blocks.
* **`energy_std6` / `energy_std12`**: Standard deviation (volatility) of energy consumption.
* **`energy_trend3` / `energy_trend6` / `energy_trend12`**: Short and long-term directions of energy change.

---

## 4. The 8-Row Sequence Window (For TE-GRU)

While traditional machine learning models (like XGBoost) only look at a single, flat row of features for the current timestamp, the **Gated Recurrent Unit (GRU)** model processes a history of the last **8 consecutive 5-minute rows** (representing the last 40 minutes). This allows it to learn temporal dependencies, acceleration, and trends.

Let's look at how the 8-row window behaves under two scenarios:

### Scenario A: Stable/Flat Energy at 10 Wh
When energy consumption is flat and stable at 10 Wh, each step in the 8-row sequence window contains consistent lag features:

| Step in Window | Timestamp | Actual Energy (Hidden) | `energy_lag1` (Fed to Model) | `energy_lag2` | `energy_lag3` |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Row 1** | $T-35$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 2** | $T-30$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 3** | $T-25$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 4** | $T-20$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 5** | $T-15$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 6** | $T-10$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 7** | $T-5$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 8 (Current)** | $T$ (Now) | *? (Predicting)* | **10 Wh** | 10 Wh | 10 Wh |

* **GRU input sequence**: `[10, 10, 10, 10, 10, 10, 10, 10]`.
* **Behavior**: The model detects zero trend and steady state, predicting **~10 Wh** for the current block.

---

### Scenario B: Sudden Energy Spike to 45 Wh
Suppose a heavy appliance is turned on, and the energy consumed during the interval $T-5$ to $T$ spikes to **45 Wh**. When the time rolls forward to the next prediction step ($T + 5$ min), this spike populates `energy_lag1` for the current prediction row.

The sequence window fed to the GRU shifts to:

| Step in Window | Timestamp | Actual Energy (Hidden) | `energy_lag1` (Fed to Model) | `energy_lag2` | `energy_lag3` |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Row 1** | $T-30$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 2** | $T-25$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 3** | $T-20$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 4** | $T-15$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 5** | $T-10$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 6** | $T-5$ min | 10 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 7** | $T$ | 45 Wh | **10 Wh** | 10 Wh | 10 Wh |
| **Row 8 (Current)** | $T+5$ min (Now) | *? (Predicting)* | **45 Wh** *(Spike)* | **10 Wh** | 10 Wh |
* **GRU input sequence**: `[10, 10, 10, 10, 10, 10, 10, 45]`.
* **Behavior**: The recurrent cells in the GRU model process the steps in order. They register the sudden jump from 10 to 45 at the very last step, recognizing the upward spike. The model responds by adjusting its prediction output sharply upward (approaching **~45 Wh**).

---

## 5. Live Behavior: Why Predictions Can "Lag" or "Drop" During Sudden Spikes

When testing on live hardware, you may notice that after a sudden load increase (e.g., from 8 Wh to 48 Wh), the prediction does not immediately reach 48 Wh, and might even drop back to baseline levels (~11-12 Wh) on subsequent rows. This is caused by two factors:

### A. GRU Sequential Inertia (Temporal Memory)

The GRU model makes its predictions based on the **entire history of the last 8 steps** (40 minutes).

* When a spike first occurs, the 8-row sequence window has **1 high step** (the current lag1 at 48 Wh) and **7 low steps** (the previous blocks at 10 Wh).
* Because the model sees a long history of low energy and only a very brief increase, its sequential memory dampens the response. The XGBoost residual corrector pushes the prediction up immediately (e.g., to ~24 Wh), but the base GRU remains conservative.
* It takes a full **8 steps (40 minutes)** of sustained high load to completely purge the low-load history from the GRU's sequence window. Only then will the prediction stabilize at the full ~48 Wh level.

### B. Database-Backed History Buffer (The Solution)

To prevent predictions from dropping back to baseline during Pi reboots, clock syncs, or service restarts, the ML service's history buffer has been migrated from an in-memory (RAM) structure to a **database-backed history buffer**:

* **How it works**: On every `/predict` request, the ML service queries the SQLite database (`room_backend/db.sqlite3`) for the last 60 records of the `energy_relaystate` table.
* **Cadence Normalization**: It reads the 1-minute `energy_kw` values logged by the rule engine and scales them by `5.0` to represent the 5-minute Wh consumption training cadence.
* **Reboot Resilience**: If the Pi reboots or the ML service restarts, the buffer is no longer wiped. It immediately hydrates itself from the actual history stored in the database.
* **Fall-back Seeding**: If the database contains fewer than 60 entries (e.g. on first setup), the oldest records are automatically padded from `mydatanew.csv` as a fallback.

> [!TIP]
> With the database-backed buffer active, the ML service is completely stateless and resilient to clock jumps. You only need to wait for the sequential GRU window (40 minutes of sustained load) to fully reflect step-change spikes, but you no longer need to worry about service restarts resetting the lags.

---

The EDFI is low (around `0.0` to `0.20 Wh`) because the room's actual power draw has decreased further, and the model is responding:

### 1. Actual Load Decreased
* The room's power draw dropped from **`~117W`** (at `14:15`) to **`~98W`** (at `14:37`).
* This caused the 1-minute energy to drop from **`1.90 Wh`** to **`1.60 Wh`** (which scales to a 5-minute history of **`8.0 Wh`** instead of `9.5 Wh`).

### 2. TE-GRU Responded
Because the historical load trend is lower, the raw TE-GRU prediction responded by dropping from **`~7.20 Wh`** down to **`~4.90 Wh` - `5.70 Wh`**.

### 3. The Math
With the negative occupancy-based residual correction still active at **`~-5.00 Wh`**, the lower TE-GRU base yields:

$$\text{EDFI} = 4.90 + (1.0918 \times -5.03) - 0.0855 = -0.67\text{ Wh} \rightarrow \text{Clipped to } \mathbf{0.0000\text{ Wh}}$$

$$\text{EDFI} = 5.71 + (1.0918 \times -4.97) - 0.0855 = 0.20\text{ Wh} \rightarrow \mathbf{0.2009\text{ Wh}}$$

---

# Smart Room ML Model Integration & Data Flow Guide

This document provides a formal technical overview of the machine learning model's integration, detailing the 8-row sequence window, the lag features, and the data sources.

---

## 1. The 8-Row Sequence Window

The core Gated Recurrent Unit (GRU) model expects an input sequence consisting of **8 timesteps** (representing $8 \times 5\text{-minute steps} = 40\text{ minutes}$ of continuous history). 

For each timestep in this 8-row sequence, the model consumes a feature vector containing:
1. **Raw Sensor Readings:** Temperature (°C), humidity (%), ambient light (lux), and occupancy count.
2. **Normalized Energy:** Integrated energy consumption (Watt-hours) scaled to the 5-minute interval.
3. **Time Encodings:** Chronological features including hour, minute, day of the week, month, day, weekend flag, and their respective sine/cosine trigonometric transformations.
4. **Interaction Cross-Features:** Sensor interaction metrics (e.g., `temp_x_humidity`).
5. **Lag and Rolling Statistics:** Features computed from past values relative to each timestep (e.g., sensor values and energy from 1, 2, and 3 steps prior).

---

## 2. Lag Features & Rolling Windows

To enable the model to capture temporal patterns, the dataset is expanded to include lagged and rolling variables for each timestep:

* **Sensor Lags:** For each sensor (temperature, humidity, lux, and occupancy), the model utilizes:
  * Individual lagged values from 1, 2, and 3 timesteps prior (e.g., `temperature_lag1`, `temperature_lag2`, `temperature_lag3`).
  * Rolling averages and standard deviations computed over 3, 6, and 12 historical timesteps.
  * Trend indicators representing the difference between the most recent timestep and historical timesteps.
* **Energy Lags:** The model similarly tracks past energy consumption states:
  * Lags for energy from 1, 2, and 3 timesteps prior (`energy_lag1`, `energy_lag2`, `energy_lag3`).
  * Rolling averages over 3, 6, and 12 timesteps (`energy_roll3`, `energy_roll6`, `energy_roll12`).
  * Volatility measures (rolling standard deviations `energy_std6`, `energy_std12`) and trend gradients over 3, 6, and 12 steps.

---

## 3. Data Sources & Integration Flow

The data used to hydrate the 8-row sequence and construct these lags is sourced from two main channels:

```
                  ┌────────────────────────────────────────┐
                  │          Django SQLite DB              │
                  │   (Table: energy_relaystate)           │
                  └──────────────────┬──────────────────────┘
                                     │ (Last 60 records)
                                     ▼
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│   Rule Engine    ├─────►│    ML Service    ├─────►│    ML Model      │
│  (Live Request)  │      │   (main.py API)  │      │  (8-Row Matrix)  │
└──────────────────┘      └──────────────────┘      └──────────────────┘
```

1. **Historical Data (Steps 1 to 7):**
   * **Source:** The SQLite database (`room_backend/db.sqlite3`), specifically the `energy_relaystate` table.
   * **Mechanism:** Upon receiving a prediction request, the ML service queries the latest 60 records from `energy_relaystate`. These records are sorted chronologically.
   * **Energy Scaling:** Because the database stores 1-minute integrated energy (`energy_kw` in Wh), the ML service multiplies these values by $5.0$ to scale them to the 5-minute training block equivalent before constructing the lag vectors.

2. **Current Step Data (Step 8):**
   * **Source:** The rule engine (`rule_engine.py`) via the live POST `/predict` request body.
   * **Mechanism:** The rule engine sends the current real-time readings (current temperature, humidity, lux, occupancy, and current 1-minute energy).
   * **Lags Overriding:** For the current timestep (Row 8), the actual energy column value is set to `np.nan` (as it is the target value being predicted). The lag features for this step (e.g. `energy_lag1`, which refers to the energy at Step 7) are forced using the scaled values from the database history.

---

## 4. Model Predictions under Low-Load States

When occupancy decreases to 2 and base load is low, the calibrated prediction drops significantly due to the model architecture:

* **Base prediction (TE-GRU):** Yields the basic load trend (e.g. $7.2\text{ Wh}$ under 117W, $4.9\text{ Wh}$ under 98W).
* **Residual correction (XGBoost):** Adjusts for contextual factors. A lower occupancy (2 vs baseline of 3) introduces a significant negative residual adjustment (approximately $-5.0\text{ Wh}$).
* **Calibration formula:**
  $$\hat{y} = \hat{y}_{\text{GRU}} + 1.0918 \times \hat{r}_{\text{XGB}} - 0.0855$$
  Under a low load of 98W and occupancy of 2, the subtractive adjustment offsets the base prediction, resulting in a calibrated output of approximately $0.0\text{ Wh}$.

---

## 5. Why the Prediction Remains at 0.0000 Wh for Extended Periods

Under certain conditions, the energy prediction (EDFI) on the dashboard stays flat at `0.0000 Wh`. This is expected behavior and happens due to two primary reasons:

1. **Negative Value Clipping:**
   * The calibration formula combines the raw TE-GRU prediction and the XGBoost residual correction. Under lower occupancy (e.g., 2 instead of 3) and low room load (e.g., < 100W), the math yields a negative output:
     $$\hat{y} = \hat{y}_{\text{GRU}} + 1.0918 \times \hat{r}_{\text{XGB}} - 0.0855$$
     $$\text{Example: } 4.90 + (1.0918 \times -5.03) - 0.0855 = -0.67\text{ Wh}$$
   * The prediction pipeline applies a safety clip to prevent physically impossible negative energy values:
     $$\text{final\_pred} = \max(0.0, \text{final\_pred})$$
   * As long as the calculated prediction remains negative, the output will register as exactly `0.0000 Wh`.

2. **Temporal Window Hysteresis (40-Minute Lag):**
   * The TE-GRU model uses a sequence window of 8 steps (40 minutes) of history. If the room has been running a low load, the sequence is filled with low energy inputs (e.g., 1.6 Wh / 8.0 Wh scaled).
   * Even if a high load is suddenly turned on, the model will not immediately jump to a high prediction. It takes several timesteps for the high load to populate the 8-row buffer and displace the older low-load readings. During this transition period, the predicted output may remain at `0.0000 Wh` before starting to rise.