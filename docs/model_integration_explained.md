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
