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