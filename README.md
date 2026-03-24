# Smart Room Energy Management System - Backend

Edge backend for Raspberry Pi that:

1. Receives room telemetry over MQTT.
2. Runs ML predictions using `ML/test_prediction_api.py` (GRU + LightGBM hybrid model).
3. Applies relay control rules based on energy predictions and battery state.
4. Persists history in SQLite (WAL mode).
5. Serves historical data through Django REST APIs.

## Architecture

```
Sensor Publisher (ESP or simulator) -> room/sensors
                                     |
                                     v
                               Mosquitto broker
                                     |
       .-----------------------------+-------------------------------.
       |                             |                               |
       v                             v                               v
workers/mqtt_logger.py         ML/test_prediction_api.py       workers/rule_engine.py
(buffer + 5-min avg -> DB)     (GRU+LightGBM + MQTT bridge)   (rule decisions + GPIO)
       |                             |                               |
       v                             v                               v
 room/data/averaged            room/ml/predictions             room/relays/state

Django API reads SQLite history at /api/v1/*
```

## ML Service (`ML/test_prediction_api.py`)

Uses a hybrid GRU (TFLite) + LightGBM model for energy prediction.

### Dual Protocol

1. **MQTT (primary):** Subscribes to `room/sensors`. When a sensor message arrives, it runs the prediction pipeline and publishes results to `room/ml/predictions`. If the broker is not available at startup, it retries every 5 seconds in the background until connected.
2. **HTTP (testing only):** Two endpoints for manual testing:
   - `POST /predict` — accepts sensor values as JSON, returns prediction.
   - `GET /predict_next` — steps through the CSV dataset.
   - `GET /` — serves the test dashboard page.

### Manual Testing Tools

For supervisor demonstrations, two tools let you manually input sensor values and compare the model output against expected calculations:

1. **`ML/test.py`** — Interactive CLI. Type sensor values, see predictions.
2. **`ML/test_dashboard.html`** — Browser page served at `http://127.0.0.1:5000`. Input form with 5 sensor fields and output display.

Both tools are HTTP-only and do not connect to MQTT.

## Quick Start

```bash
cd PROJECT_CODE
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd room_backend
python manage.py migrate
cd ..

# Terminal 1 — MQTT broker
mosquitto -c systemd/mosquitto.conf -v

# Terminal 2 — Django API
cd room_backend && python manage.py runserver 0.0.0.0:8000

# Terminal 3 — MQTT logger
python workers/mqtt_logger.py

# Terminal 4 — Rule engine
python workers/rule_engine.py

# Terminal 5 — ML service
cd ML && python test_prediction_api.py

# Terminal 6 — Data simulator
python simulation/data_simulator.py

# Optional — Manual ML test (CLI)
cd ML && python test.py

# Optional — Manual ML test (browser)
# Open http://127.0.0.1:5000 in your browser
```

## Key Runtime Configuration

Copy `example.env` to `.env` to configure all services. Key variables for the Rule Engine include:

1. `DECISION_INTERVAL_MINUTES` default `3` (test)
2. `BATTERY_LAG_INTERVAL_SECONDS` default `30`
3. `MAX_BATTERY_DROP_PERCENT` default `2`
4. `MODE_A_MAX_KWH` default `2.4`

Production interval example:

```bash
export DECISION_INTERVAL_MINUTES=5
```

## API Endpoints

See `integration_contract.md`.

1. `GET /api/v1/sensors/`
2. `GET /api/v1/sensors/latest/`
3. `GET /api/v1/predictions/`
4. `GET /api/v1/predictions/latest/`
5. `GET /api/v1/relays/`
6. `GET /api/v1/relays/current/`
