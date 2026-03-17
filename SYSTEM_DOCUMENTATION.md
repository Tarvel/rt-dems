# Smart Room Energy Management System - Technical Documentation

## 1. System Objective

The system manages hostel room loads by combining:

1. Environmental and power telemetry from MQTT.
2. ML predictions from a local model wrapper.
3. Rule-based relay control in watt units.
4. Historical storage and API access through Django.

## 2. Components

1. Mosquitto broker (`systemd/mosquitto.conf`)
2. Simulator (`simulation/data_simulator.py`) or hardware publisher
3. ML service (`ML/app.py` + `ML/local_inference_wrapper.py`)
4. Logger worker (`workers/mqtt_logger.py`)
5. Rule engine worker (`workers/rule_engine.py`)
6. Django backend (`room_backend/`)
7. Dashboard (`dashboard/index.html`)

## 3. Data Flow

1. Telemetry is published to `room/sensors`.
2. ML service subscribes to `room/sensors`, runs `LocalEdgeForecaster`, publishes to `room/ml/predictions`.
3. Logger subscribes to both topics, buffers and writes 5-minute averages to SQLite.
4. Rule engine subscribes to both topics, evaluates mode, updates GPIO, logs decisions.
5. Logger publishes `room/data/averaged`; rule engine publishes `room/relays/state`.
6. Django serves persisted historical data through `/api/v1/*` endpoints.

## 4. ML Service Details

### 4.1 Inference source

`ML/app.py` initializes and uses:

1. `LocalEdgeForecaster` from `ML/local_inference_wrapper.py`
2. Existing model artifacts in `ML/` (TFLite, scaler, baseline model)

### 4.2 HTTP and MQTT behavior

1. `POST /predict` performs model inference and returns JSON.
2. MQTT callback (`room/sensors`) performs model inference and publishes results.
3. Both paths publish to `room/ml/predictions`.

### 4.3 Input normalization

For MQTT payloads that do not include lag fields, `ML/app.py` builds fallback lag features from available sensor/power values so inference remains robust.

## 5. Rule Engine Details

### 5.1 Evaluation cadence

Configured by:

1. `RULE_EVAL_INTERVAL_SECONDS` (default `120` for testing)

### 5.2 Load mode watt thresholds

Configured by:

1. `MODE_A_MAX_W` (default `2400`)
2. `MODE_B_MAX_W` (default `1400`)
3. `MODE_C_MAX_W` (default `800`)

### 5.3 Current mode logic summary

1. Battery stability lock is applied when enough history exists.
2. Temperature bias can force Mode B when hot and battery is sufficient.
3. Final mode selection is watt-threshold based against predicted load.
4. Occupancy forced-drop logic is removed in current baseline.

## 6. Unit Contract (W vs kW)

1. Rule engine decisions use watts (`predicted_power_w`).
2. ML outputs include kW for readability and compatibility.
3. Compatibility fields (`predicted_energy_range`, `peak_demand`) are retained for existing consumers.

## 7. MQTT Contract Summary

### 7.1 `room/sensors`

Expected fields include:

1. `temperature_c`
2. `temperature` (compatibility)
3. `humidity`
4. `lux`
5. `occupancy`
6. `voltage`
7. `current`
8. `power_w`
9. `battery_level`

### 7.2 `room/ml/predictions`

Includes:

1. `mean_prediction_kw`
2. `upper_bound_kw`
3. `predicted_power_kw`
4. `predicted_power_w`
5. `predicted_energy_range`
6. `peak_demand`

### 7.3 `room/data/averaged`

Published by logger every flush interval with averaged sensor values and prediction compatibility fields.

### 7.4 `room/relays/state`

Published by rule engine after each evaluation with mode, relay states, reason, and timestamp.

## 8. Database and Concurrency

SQLite WAL mode is used so readers and writers can coexist across:

1. Django API reads
2. Logger writes
3. Rule engine writes

Primary tables:

1. `energy_sensorlog`
2. `energy_mlprediction`
3. `energy_relaystate`

## 9. API Surface

Base path: `/api/v1/`

1. `/sensors/`
2. `/sensors/latest/`
3. `/predictions/`
4. `/predictions/latest/`
5. `/relays/`
6. `/relays/current/`

All are read-only GET endpoints.

## 10. GPIO and Deployment Notes

Default BCM pins:

1. Relay 1 -> 17
2. Relay 2 -> 27
3. Relay 3 -> 22

Override with `RELAY_PIN_1`, `RELAY_PIN_2`, `RELAY_PIN_3`.

For production:

1. Use systemd services in `systemd/`.
2. Set rule interval to `1800` seconds.
3. Keep broker listeners for 1883 (MQTT) and 9001 (WebSocket).
