# Smart Room Energy Management System - Backend

Edge backend for Raspberry Pi that:

1. Receives room telemetry over MQTT.
2. Runs ML predictions using `ML/local_inference_wrapper.py`.
3. Applies relay control rules in watts.
4. Persists history in SQLite (WAL mode).
5. Serves historical data through Django REST APIs.

## Architecture

```
Sensor Publisher (ESP or simulator) -> room/sensors
                                     |
                                     v
                               Mosquitto broker
                                     |
      .------------------------------+-------------------------------.
      |                              |                               |
      v                              v                               v
workers/mqtt_logger.py          ML/app.py                      workers/rule_engine.py
(buffer + 5-min avg -> DB)      (model inference + MQTT pub)   (watt-rule decisions + GPIO)
      |                              |                               |
      v                              v                               v
 room/data/averaged            room/ml/predictions             room/relays/state

Django API reads SQLite history at /api/v1/*
```

## ML Service Behavior

`ML/app.py` now uses `LocalEdgeForecaster` from `ML/local_inference_wrapper.py`.

1. On MQTT input, it infers prediction and publishes to `room/ml/predictions`.
2. On HTTP `POST /predict`, it returns normal JSON and also publishes to MQTT.

## Unit Convention

1. Rule engine logic is watt-based (`W`).
2. ML payload includes both:
   - `predicted_power_w` (primary for rule logic)
   - `predicted_power_kw`
3. Compatibility fields are still included:
   - `predicted_energy_range`
   - `peak_demand`

## Quick Start

```bash
cd PROJECT_CODE
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cd room_backend
python manage.py migrate
cd ..

# Terminal 1
mosquitto -c systemd/mosquitto.conf -v

# Terminal 2
cd room_backend && python manage.py runserver 0.0.0.0:8000

# Terminal 3
python workers/mqtt_logger.py

# Terminal 4
python workers/rule_engine.py

# Terminal 5
cd ML && python app.py

# Terminal 6
python simulation/data_simulator.py

# Optional HTTP test
cd ML && python test_api.py
```

## Key Runtime Configuration

Rule engine environment variables:

1. `RULE_EVAL_INTERVAL_SECONDS` default `120` (test)
2. `MODE_A_MAX_W` default `2400`
3. `MODE_B_MAX_W` default `1400`
4. `MODE_C_MAX_W` default `800`

Production interval example:

```bash
export RULE_EVAL_INTERVAL_SECONDS=1800
```

## API Endpoints

See `integration_contract.md`.

1. `GET /api/v1/sensors/`
2. `GET /api/v1/sensors/latest/`
3. `GET /api/v1/predictions/`
4. `GET /api/v1/predictions/latest/`
5. `GET /api/v1/relays/`
6. `GET /api/v1/relays/current/`
