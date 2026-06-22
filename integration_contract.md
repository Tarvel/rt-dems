# Integration Contract - MQTT Topics and JSON Payloads

This contract defines active MQTT topics, ownership, and payload schemas.

Broker: `<PI_IP>:1883`
WebSocket listener: `<PI_IP>:9001`
QoS: `1`

## 1. Topic Map

| Topic | Publisher | Subscribers | Description |
|---|---|---|---|
| `room/sensors` | hw_bridge or simulator | logger, rule engine, dashboard | Normalised telemetry stream |
| `room/hardware/nano` | Group 1 ESP32 | hw_bridge | Combined environmental + battery data |
| `room/ml/predictions` | rule engine | logger, dashboard | Model predictions (published by rule engine after HTTP call to ML) |
| `room/relays/state` | rule engine | **ESP32 relay controller**, dashboard | Current mode and relay states |
| `room/control/override` | dashboard | rule engine | Manual mode/relay override commands |

## 2. Group 1 Hardware Payload Contract

Group 1's ESP32 publishes a single combined payload (environmental sensors + battery) to `room/hardware/nano`. The `hw_bridge.py` worker normalises this into the `room/sensors` schema (section 3) so downstream subscribers need no changes.

### 2a. Combined Payload (`room/hardware/nano`)

```json
{
  "temperature": 31.6,
  "humidity": 63.2,
  "voltage": 0.0,
  "current": 0.00,
  "power": 0.0,
  "energy": 0.000,
  "lux": 5.75,
  "ultrasonic_occupancy": 0,
  "radar_motion": 1,
  "battery_voltage": 26.2,
  "soc": 100
}
```

| Field | Type | Unit | Notes |
|---|---|---|---|
| `temperature` | float | °C | Room temperature |
| `humidity` | float | % | Relative humidity |
| `voltage` | float | V | Mains voltage |
| `current` | float | A | Load current |
| `power` | float | W | Instantaneous power |
| `energy` | float | kWh | Cumulative energy (mapped to `energy_kw` in bridge) |
| `lux` | float | lx | Ambient light level |
| `ultrasonic_occupancy` | int | 0/1 | Ultrasonic presence (mapped to `occupancy`) |
| `radar_motion` | int | 0/1 | Radar-based motion detection (passed through) |
| `battery_voltage` | float | V | Battery terminal voltage |
| `soc` | float | % | State of Charge (mapped to `battery_level`) |

### 2b. Field mapping (hw_bridge normalisation)

| Group 1 field | → `room/sensors` field | Transform |
|---|---|---|
| `temperature` | `temperature`, `temperature_c` | Direct copy |
| `humidity` | `humidity` | Direct copy |
| `voltage` | `voltage` | Direct copy |
| `current` | `current` | Direct copy |
| `energy` | `energy_kw` | Key rename |
| `lux` | `lux` | Direct copy |
| `ultrasonic_occupancy` | `occupancy` | Key rename + int cast |
| `radar_motion` | `radar_motion` | Pass-through |
| `soc` | `battery_level` | Key rename |
| `battery_voltage` | `battery_voltage` | Pass-through |

## 3. Sensor Payload Contract (`room/sensors`)

### Required fields

```json
{
  "temperature_c": 32.5,
  "temperature": 32.5,
  "humidity": 60.0,
  "lux": 450.0,
  "occupancy": 1,
  "voltage": 220.0,
  "current": 6.2,
  "energy_kwh": 0.0227,
  "battery_level": 78.0
}
```

### Notes

1. `temperature` is kept for legacy compatibility.
2. `temperature_c` is the preferred name.
3. `energy_kwh` is energy for the sample interval.
4. `voltage` and `current` are optional (the backend logger defaults them to `0.0` if missing).
5. When `source` is `"group1_hardware"`, the data originated from the hw_bridge (real sensors). When absent, it is from the simulator.

## 4. ML Payload Contract (`room/ml/predictions`)

Published by the **rule engine** after calling `POST /predict` on the ML service (`workers/ml_service.py`). The ML service is HTTP-only — it does not subscribe to MQTT topics.

```json
{
  "predicted_energy_wh": 45.2301,
  "upper_bound_energy_wh": 52.8745,
  "lower_bound_energy_wh": 37.5857,
  "energy_unit": "Wh",
  "avg_sensors": {
    "temperature_c": 31.4,
    "humidity": 60.9,
    "lux": 5.5,
    "occupancy": 4
  },
  "timestamp": "2026-05-24T17:00:00+00:00",
  "source": "rule-engine-http-call"
}
```

### Field descriptions

| Field | Unit | Description |
|---|---|---|
| `predicted_energy_wh` | Wh | Hybrid prediction (TE-GRU + LightGBM + XGBoost residual correction) |
| `upper_bound_energy_wh` | Wh | Upper confidence bound (z × uncertainty) |
| `lower_bound_energy_wh` | Wh | Lower confidence bound |
| `energy_unit` | string | Always `"Wh"` |
| `avg_sensors` | object | The 5-minute averaged sensor values used as model input |
| `avg_sensors.temperature_c` | °C | Average temperature over 5-min window |
| `avg_sensors.humidity` | % | Average humidity |
| `avg_sensors.lux` | lx | Average light level |
| `avg_sensors.occupancy` | int | Average occupancy count |
| `timestamp` | ISO 8601 | When the prediction was made |
| `source` | string | Always `"rule-engine-http-call"` |

### Prediction flow

The rule engine averages 5 minutes of sensor readings (matching the ML model's training interval), then sends the averaged values to the ML service via HTTP. The ML service returns a prediction, which the rule engine uses for the EDFI decision and then publishes to MQTT.

## 5. Relay State Payload (`room/relays/state`)

This topic publishes three different types of payloads.

**A. Full Rule Evaluation Payload (every decision interval)**
```json
{
  "mode": "B",
  "relay_1": true,
  "relay_2": true,
  "relay_3": false,
  "auto": true,
  "battery_t_now": 77.3,
  "battery_t1": 77.9,
  "battery_t2": 78.4,
  "battery_lag_drop": 1.1,
  "battery_lag_interval_seconds": 30,
  "reason": "Condition 3 - Battery drop within threshold -> switch to Mode B",
  "timestamp": "2026-03-17T12:00:00+00:00"
}
```

| Field | Type | Notes |
|---|---|---|
| `mode` | string | `"A"`, `"B"`, `"C"`, or `"MANUAL"` (when individual relays are overridden) |
| `relay_1` | bool | Priority 1 relay state |
| `relay_2` | bool | Priority 2 relay state |
| `relay_3` | bool | Priority 3 relay state |
| `auto` | bool | `true` = AI auto-managed, `false` = manual override active |
| `reason` | string | Human-readable explanation of why this mode was chosen |

**B. Lightweight Battery Lag Update (strictly every 30 seconds)**
```json
{
  "type": "battery_lag_update",
  "battery_t_now": 77.3,
  "battery_t1": 77.9,
  "battery_t2": 78.4,
  "timestamp": "2026-03-17T12:00:15+00:00"
}
```

**C. Manual Override Payload (published when dashboard sends override)**
```json
{
  "mode": "A",
  "relay_1": true,
  "relay_2": true,
  "relay_3": true,
  "auto": false,
  "reason": "Manual override → Mode A",
  "timestamp": "2026-05-24T17:00:00+00:00"
}
```

When `"auto": false`, the dashboard should show the "Manual Override Active" indicator. When `"auto": true`, the system is AI-managed.

## 6b. Manual Override Commands (`room/control/override`)

Published by the **dashboard** to the rule engine. The rule engine subscribes to this topic and responds immediately.

### Enable override — set a mode

Disables auto-management and switches to the specified mode. Relay states are determined by the standard mode mapping (A = all on, B = R1+R2 on, C = R1 only).

```json
{"auto": false, "mode": "A"}
```
```json
{"auto": false, "mode": "B"}
```
```json
{"auto": false, "mode": "C"}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `auto` | bool | ✅ | Must be `false` to enable override |
| `mode` | string | ✅ | `"A"`, `"B"`, or `"C"` |

### Enable override — set individual relays

Disables auto-management and sets each relay independently (ignoring mode presets). The mode field in `room/relays/state` will show `"MANUAL"`.

```json
{"auto": false, "relay_1": true, "relay_2": false, "relay_3": true}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `auto` | bool | ✅ | Must be `false` |
| `relay_1` | bool | optional | Water heater. Defaults to current state if omitted. |
| `relay_2` | bool | optional | HVAC / A.C. Defaults to current state if omitted. |
| `relay_3` | bool | optional | Freezer. Defaults to current state if omitted. |

### Re-enable auto management

Turns off manual override and lets the AI rule engine resume automatic decisions.

```json
{"auto": true}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `auto` | bool | ✅ | Must be `true` to re-enable auto |

### Testing from terminal

```bash
# Switch to Mode A manually
mosquitto_pub -h localhost -t "room/control/override" -m '{"auto": false, "mode": "A"}'

# Toggle individual relays
mosquitto_pub -h localhost -t "room/control/override" -m '{"auto": false, "relay_1": true, "relay_2": false, "relay_3": true}'

# Turn auto back on
mosquitto_pub -h localhost -t "room/control/override" -m '{"auto": true}'
```

## 7. Rule Threshold Contract

The rule engine uses EDFI (Energy Demand Forecast Interval) thresholds to classify predicted load, combined with battery stability checks.

### EDFI Load Thresholds (Wh)

The predicted energy from the ML model is compared against these thresholds:

| Load Class | Condition | Battery ≥ 80% | Battery ≥ 60% | Battery < 60% |
|---|---|---|---|---|
| **Peak** | EDFI ≥ `PEAK_THRESHOLD` | Smart A | Smart B | Smart C |
| **Moderate** | EDFI ≥ `MODERATE_THRESHOLD` | Smart B | Smart B | Smart C |
| **Baseline** | EDFI ≥ `BASELINE_THRESHOLD` | Smart C | Smart C | Smart C |
| **Very Low** | EDFI < `BASELINE_THRESHOLD` | Smart C | Smart C | Smart C |

Configurable via `.env`:
- `PEAK_THRESHOLD` (default 60)
- `MODERATE_THRESHOLD` (default 20)
- `BASELINE_THRESHOLD` (default 5)

### Battery Stability

Battery stability requires ALL 3 lag readings (T-now, T-1, T-2) to meet the threshold:
- `BATTERY_LAG_INTERVAL_SECONDS` (default 30) — how often battery is sampled
- 3 consecutive readings must all be ≥ the required level for the mode to apply

### Sensor Averaging

Before calling the ML model, the rule engine averages sensor readings over `SENSOR_AVG_WINDOW_SECONDS` (default 300 = 5 minutes), matching the ML model's training data interval.

### Hardware actuation

The rule engine does **not** drive GPIO pins directly. It publishes the `relay_1`, `relay_2`, `relay_3` booleans to `room/relays/state`. An external **ESP32 microcontroller** subscribes to this topic and actuates the physical relay modules based on these values.

## 8. REST API Contract

Base URL: `http://<PI_IP>:8000/api/v1/`

1. `GET /sensors/`
2. `GET /sensors/latest/`
3. `GET /predictions/`
4. `GET /predictions/latest/`
5. `GET /relays/`
6. `GET /relays/current/`
7. `GET /download/csv/` — Download historical data as CSV
8. `GET /analytics/` — Retrieve historical sensor, energy, prediction, and state data in JSON format for charts

### CSV Download (`GET /download/csv/`)

Returns a CSV file with sensor readings, predictions, and relay decisions aligned by minute.

**Query parameters:**

| Param | Example | Description |
|---|---|---|
| `start` | `?start=today` or `?start=2026-06-18T00:00` | Start of date range (ISO 8601, or "today" for midnight today) |
| `end` | `?end=today` or `?end=2026-06-18T12:00` | End of date range (ISO 8601, or "today" for current time) |
| `days` | `?days=7` | Alternative: last N days from now (ignored if start/end given) |
| *(none)* | | Defaults to last 24 hours |

*If only `start` is provided, `end` defaults to now. If only `end` is provided, `start` defaults to 7 days before.*

**CSV columns:**

```
timestamp, temperature, humidity, lux, occupancy, real time energy, real time energy (5-min), predicted_energy_8lags, predicted_energy_lower_8lags, predicted_energy_upper_8lags, Decision UB 1 (Wh), Decision UB 2 (Wh), Decision UB 3 (Wh), Decision UB Avg (Wh), Battery Voltage, Battery Percentage, Battery Lag Checks, System Mode A,B,C
```

## 9. ML HTTP Endpoints

Base URL: `http://<PI_IP>:5000`

The ML service is HTTP-only and configured via `.env`. To swap models, change `MODEL_ASSET_DIR` and `ML_SERVICE_SCRIPT`, then restart. The rule engine calls `POST /predict` every ~5 seconds (continuous) and uses the cached result for 5-minute decisions.

**Active model (current):** `model_new_unsure/main.py` (TE-GRU + XGBoost + MH) running the 8-row sequence window (`tegru_xgb_mh_pipeline.joblib` asset).
**Alternative models:** 
- `tegru_xgb_mh_pipeline-1.joblib` (12-row sequence window version of the TE-GRU model)
- `LIGHT_ML_MODEL/main.py` (LightGBM + XGBoost + MH Blend)
- `workers/ml_service.py` (TE-GRU + LightGBM)

| Method | Path | Description |
|---|---|---|
| `POST` | `/predict` | Send sensor values, get prediction back (used by rule engine + dashboard) |
| `GET` | `/metadata` | Returns model info, CSV, features, readiness status |
| `GET` | `/` | Serves status page |

### POST /predict Response Payload (Flat Structure)

Unlike older models that wrapped predictions under a `"predictions"` key, the TE-GRU service returns keys directly at the root:

```json
{
  "predicted_energy_wh": 11.0740,
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

### POST /predict request body

```json
{
  "temperature_c": 28.0,
  "humidity": 60.0,
  "lux": 400.0,
  "occupancy": 1,
  "datetime_str": "2026-03-26T14:30"
}
```

`datetime_str` is optional; if omitted, the ML service uses current server time.

All fields have defaults, so you can send an empty `{}` to test with default values.

## 10. Dashboard Realtime Contract

The dashboard (`dashboard/index.html`) is MQTT-driven for realtime values.

### 10.1 Topics consumed by dashboard

1. `room/sensors` (primary realtime telemetry)
2. `room/ml/predictions` (predicted load)
3. `room/relays/state` (current mode and relay states)

### 10.2 Battery lag display behavior

1. The dashboard battery-lag display reads `battery_t_now`, `battery_t1`, and `battery_t2` from `room/relays/state`.
2. To ensure real-time responsiveness, the backend pushes a lightweight `type: "battery_lag_update"` message to this topic strictly every 30 seconds, entirely independent of the `DECISION_INTERVAL_MINUTES` cadence.
3. The dashboard ignores the missing `mode` field during these updates and safely updates the lag visualization.

## 11. REST API Endpoints (Django)

Base URL: `http://<PI_IP>:8000/api/v1/`

All endpoints are **GET-only**, paginated, and return JSON.

### 11a. Sensor Logs

| Endpoint | Description |
|---|---|
| `GET /api/v1/sensors/` | Paginated list of 5-minute averaged sensor readings (newest first) |
| `GET /api/v1/sensors/latest/` | Single most recent sensor average |

**Query params** for `/sensors/`: `?page=N`, `?ordering=timestamp` or `?ordering=-temperature`

**Response fields:**
```json
{
  "id": 42,
  "timestamp": "2026-05-24T19:00:00Z",
  "temperature": 31.27,
  "humidity": 59.35,
  "occupancy": 0,
  "voltage": 0.0,
  "current": 0.0,
  "battery_level": 48.1,
  "lux": 4.31,
  "energy_kw": 0.0,
  "power_w": 0.0,
  "radar_motion": 1,
  "battery_voltage": 22.97
}
```

### 11b. ML Predictions

| Endpoint | Description |
|---|---|
| `GET /api/v1/predictions/` | Paginated list of ML predictions (newest first) |
| `GET /api/v1/predictions/latest/` | Single most recent ML prediction |

**Response fields:**
```json
{
  "id": 15,
  "timestamp": "2026-05-24T19:00:00Z",
  "predicted_energy_wh": 7.1235,
  "upper_bound_wh": 8.4501,
  "lower_bound_wh": 5.7969
}
```

### 11c. Relay State (Mode Switch Audit Trail)

| Endpoint | Description |
|---|---|
| `GET /api/v1/relays/` | Paginated audit trail of all mode decisions (newest first) |
| `GET /api/v1/relays/current/` | Single most recent relay state (current mode) |

**Response fields:**
```json
{
  "id": 88,
  "timestamp": "2026-05-24T19:05:00Z",
  "mode": "B",
  "mode_display": "Mode B — Average Load (P1+P2 ON)",
  "relay_1": true,
  "relay_2": true,
  "relay_3": false,
  "reason": "Step 1 PASSED → Step 2: battery stable (drop 0.50% ≤ 2.00%) → Mode B",
  "temperature": 31.7,
  "humidity": 60.9,
  "lux": 4.6,
  "occupancy": 0,
  "energy_kw": 0.0,
  "battery_level": 50.0,
  "battery_voltage": 23.6
}
```

> **Note:** Each relay decision now includes a full sensor snapshot — the exact room conditions at the moment of mode switch. This enables post-hoc analysis of how energy modes correlate with environmental state.

### 11d. JSON Analytics Endpoint

| Endpoint | Description |
|---|---|
| `GET /api/v1/analytics/` | List of historical sensor readings, relay decisions, and ML predictions aligned by timestamp. |

**Query params:**
* `days=N` — number of days of history to retrieve (default: 7)

**Response fields:**
An array of objects matching the CSV export format:
```json
[
  {
    "timestamp": "2026-06-19 20:28:19",
    "temperature": 31.27,
    "humidity": 59.35,
    "lux": 4.31,
    "occupancy": 0,
    "real time energy": 5.4312,
    "real time energy (5-min)": 27.156,
    "predicted_energy_8lags": 8.3843,
    "predicted_energy_lower_8lags": 6.3266,
    "predicted_energy_upper_8lags": 10.442,
    "Decision UB 1 (Wh)": 8.7944,
    "Decision UB 2 (Wh)": 9.5413,
    "Decision UB 3 (Wh)": 9.6972,
    "Decision UB Avg (Wh)": 9.3443,
    "Battery Voltage": 22.97,
    "Battery Percentage": 48.1,
    "Battery Lag Checks": "T-now=22.97V, T-1=23.10V, T-2=23.22V",
    "System Mode A,B,C": "B"
  }
]
```