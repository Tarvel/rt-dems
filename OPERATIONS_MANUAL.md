# Smart Room Energy Management System - Operations Manual

This manual covers how to set up and run the system.

## 1. Prerequisites

### 1.1 System packages

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients sqlite3
```

### 1.2 Python environment

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

ML service has its own dependencies:

```bash
cd ML
pip install -r requirements.txt
cd ..
```

On Raspberry Pi only:

```bash
# No GPIO libraries needed on the Pi anymore — the rule engine
# publishes relay decisions via MQTT. An ESP32 handles physical
# relay actuation. lgpio/gpiozero are only needed if you run
# the GPIO test script separately (now removed).
```

### 1.3 Important: ESP32 relay controller

The rule engine no longer drives GPIO pins on the Raspberry Pi. Instead, it publishes mode decisions to `room/relays/state` via MQTT. A separate **ESP32 microcontroller** subscribes to this topic and actuates the physical relay modules using the `relay_1`, `relay_2`, `relay_3` booleans in the payload.

This means you do **not** need `lgpio`, `gpiozero`, or any GPIO-related Python packages on the Pi for normal operation.

## 2. Initial Setup

### 2.1 Django database

```bash
cd room_backend
python manage.py migrate
sqlite3 db.sqlite3 "PRAGMA journal_mode;"
cd ..
```

Expected output: `wal`

## 3. Runtime Topology

At runtime, these processes should be active:

1. Mosquitto broker
2. Django API server
3. MQTT logger worker
4. Rule engine worker
5. FastAPI ML service (`ML/test_prediction_api.py`)
6. Data simulator (or real hardware publisher)

## 4. Startup Order

Use separate terminals. Start Mosquitto first. The ML service will retry the MQTT connection automatically if the broker is not ready yet, but starting Mosquitto first avoids unnecessary retry messages.

For realtime dashboard values, the simulator (or hardware publisher) must be running continuously.

### Terminal 1 - MQTT broker

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
mosquitto -c systemd/mosquitto.conf -v
```

### Terminal 2 - Django API

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate
cd room_backend
python manage.py runserver 0.0.0.0:8000
```

### Terminal 3 - MQTT logger

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate
python workers/mqtt_logger.py
```

### Terminal 4 - Rule engine

The rule engine evaluates energy/battery rules and publishes relay state decisions via MQTT. It does **not** drive local GPIO pins — an ESP32 subscribes to the published topic and handles physical relay actuation.

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate
python workers/rule_engine.py
```

### Terminal 5 - FastAPI ML service

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate
cd ML
python test_prediction_api.py
```

The ML service runs on port `5000`. It connects to the MQTT broker automatically. If the broker is not running yet, it retries every 5 seconds in the background until connected.

Health check:

```bash
curl -s http://127.0.0.1:5000/docs
```

### Terminal 6 - Simulator (or hardware publisher)

This step is needed for realtime updates when hardware is not connected.

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate
python simulation/data_simulator.py
```

Keep this terminal running. If it stops, sensor values stop and ML predictions stop.

**Battery drain modes:** The simulator supports two battery simulation profiles, controlled by the `BATTERY_DRAIN_MODE` environment variable:

```bash
# Default (set in example.env) — randomised fluctuations to stress-test
# the rule engine's 3-time battery lag:
BATTERY_DRAIN_MODE=inconsistent python simulation/data_simulator.py

# Linear drain — deterministic -0.1% per row (for baseline/predictable testing):
BATTERY_DRAIN_MODE=consistent python simulation/data_simulator.py
```

The inconsistent mode produces a mix of normal drain (70%), sharp drops of 2-5% (15%), flat periods (10%), and small recoveries (5%). This ensures the rule engine's lag stability check (`MAX_BATTERY_DROP_PERCENT`) is properly exercised.

### Terminal 7 - Open dashboard in browser (optional)

Open this file in your browser:

```
file:///home/tai/Downloads/PEOJECT%20RESEARCH%20REFERENCES/PROJECT_CODE/dashboard/index.html
```

## 5. Manual ML Testing

For supervisor demonstrations, two tools let you manually type sensor values and see what the model predicts.

### Option A: CLI test (test.py)

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE/ML
source ../venv/bin/activate
python test.py
```

In interactive mode, type values for temperature, humidity, lux, occupancy, and energy. Press Enter to use defaults. The script sends them to `POST /predict` and shows the model output.

Other modes:

```bash
python test.py --auto 10    # Auto-step through 10 CSV rows
```

### Option B: Browser test (test_dashboard.html)

Open `http://127.0.0.1:5000` in your browser (the ML service serves the page). Fill in the 5 input fields and click Predict to see the model output.

Both test tools use HTTP only and do not connect to MQTT.

## 6. Testing the Rule Engine (MQTT-Based)

**File:** `simulation/test_rule_engine_mqtt.py`

This test script validates that the rule engine publishes the correct JSON payloads for every mode transition, without requiring any hardware or a real MQTT broker.

### 6.1 What it tests

- All mode transitions (A, B, C) for both Step 1 and Step 2
- Battery lag stability checks (day vs night thresholds)
- Lag instability forcing mode drops (A→B, B→C)
- No-ML-prediction fallback (maintains current mode)
- Full payload structure (all required keys present)

### 6.2 Running the test

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate
python simulation/test_rule_engine_mqtt.py
```

Expected output:

```
============================================================
  Rule Engine — MQTT Payload Test Suite
============================================================

━━ 1. Mode A — energy sufficient, battery ≥80%, lag stable ━━
  ✓ mode is A
  ✓ relay_1 is True
  ...

============================================================
  ALL 22 TESTS PASSED ✓
============================================================
```

### 6.3 How the ESP32 uses the payload

The rule engine publishes to `room/relays/state` with `relay_1`, `relay_2`, `relay_3` boolean fields. The ESP32 subscribes to this topic and drives its GPIO pins accordingly:

| Payload field | ESP32 action |
|--------------|-------------|
| `relay_1: true` | GPIO pin → HIGH (relay closes, device ON) |
| `relay_1: false` | GPIO pin → LOW (relay opens, device OFF) |
| Same for `relay_2`, `relay_3` | — |

## 7. What to Expect When Running

1. Simulator publishes sensor payloads to `room/sensors` (prediction-paced — waits for ML response before advancing).
2. ML service receives sensor data via MQTT, runs the GRU + LightGBM model, and publishes predictions to `room/ml/predictions`.
3. Logger buffers sensor and prediction data and writes 5-minute averages to SQLite.
4. Rule engine evaluates every configured interval and publishes mode decisions to `room/relays/state` (consumed by ESP32 + dashboard).
5. ESP32 relay controller subscribes to `room/relays/state` and drives physical relay GPIO pins.
5. Battery lag tracker (inside the rule engine) shifts T-now/T-1/T-2 every 30 seconds and publishes lightweight `battery_lag_update` payloads to the dashboard.
6. Django API serves historical data at `/api/v1/*`.
7. Dashboard shows live data from MQTT in the browser.

## 8. Configuration

### 8.1 Environment variables (`.env`)

Copy the provided example file to configure all services:

```bash
cp example.env .env
```

Key variables for the rule engine:

```bash
DECISION_INTERVAL_MINUTES=3
BATTERY_LAG_INTERVAL_SECONDS=30
MAX_BATTERY_DROP_PERCENT=2              # Daytime (solar) threshold
MAX_BATTERY_DROP_NIGHT_PERCENT=8        # Nighttime (no solar) threshold
SOLAR_HOUR_START=11                     # Solar window start (24h)
SOLAR_HOUR_END=16                       # Solar window end (24h, exclusive)
MODE_A_MAX_KWH=2.4
```

Key variables for the data simulator:

```bash
BATTERY_DRAIN_MODE=inconsistent    # "consistent" (linear) or "inconsistent" (randomised)
BATTERY_START=85.0                 # Initial battery % at simulation start
BATTERY_FLOOR=20.0                 # Minimum battery % (drain stops here)
ML_API_BASE=http://127.0.0.1:5000  # ML service URL (for /reset call)
PREDICTION_TIMEOUT=30              # Max seconds to wait for ML prediction per row
MIN_ROW_DELAY=3                    # Min seconds between rows
```

Production interval example in `.env`:

```bash
DECISION_INTERVAL_MINUTES=5
```

### 7.2 Broker and topic defaults

1. MQTT broker: `localhost`
2. MQTT port: `1883`
3. Sensor topic: `room/sensors`
4. Prediction topic: `room/ml/predictions`

## 9. Verification Commands

### 8.1 Subscribe to all MQTT messages

```bash
mosquitto_sub -t "room/#" -v
```

### 8.2 Check database data

```bash
sqlite3 room_backend/db.sqlite3 "SELECT id,timestamp,temperature,humidity,battery_level FROM energy_sensorlog ORDER BY id DESC LIMIT 5;"
sqlite3 room_backend/db.sqlite3 "SELECT id,timestamp,predicted_energy_range,peak_demand FROM energy_mlprediction ORDER BY id DESC LIMIT 5;"
sqlite3 room_backend/db.sqlite3 "SELECT id,timestamp,mode,relay_1,relay_2,relay_3 FROM energy_relaystate ORDER BY id DESC LIMIT 5;"
```

### 8.3 Check HTTP endpoints

```bash
curl -s http://127.0.0.1:8000/api/v1/sensors/latest/
curl -s http://127.0.0.1:8000/api/v1/predictions/latest/
curl -s http://127.0.0.1:8000/api/v1/relays/current/
```

### 8.4 Test ML prediction manually

```bash
curl -s http://127.0.0.1:5000/predict -X POST \
  -H "Content-Type: application/json" \
  -d '{"temperature_c":32.5,"humidity":60.0,"lux":450.0,"occupancy":1,"energy_kw":1.5}'
```

## 10. systemd Deployment

```bash
sudo cp systemd/mqtt-logger.service /etc/systemd/system/
sudo cp systemd/rule-engine.service /etc/systemd/system/
sudo cp systemd/mosquitto.conf /etc/mosquitto/conf.d/room.conf
sudo systemctl daemon-reload
sudo systemctl enable --now mosquitto mqtt-logger rule-engine
```

Check service logs:

```bash
sudo journalctl -u mqtt-logger -f
sudo journalctl -u rule-engine -f
```

## 11. Troubleshooting

1. MQTT disconnected
   - Make sure the broker is running with `systemd/mosquitto.conf`.
   - Make sure port `1883` is available.

2. No predictions in `room/ml/predictions`
   - Make sure `ML/test_prediction_api.py` is running.
   - Make sure the data simulator is running (the ML service needs sensor messages on `room/sensors` to trigger predictions).
   - Check the ML terminal for errors.

3. ML `POST /predict` fails
   - Make sure `ML/test_prediction_api.py` started without errors.
   - Check that the model files are present in `ML/` (`.tflite`, `.joblib`, `.csv`).

4. Port 5000 or 8000 already in use
   - Kill old processes: `lsof -i :5000` or `lsof -i :8000` to find them.

5. Database locked errors
   - Confirm WAL mode:
     ```bash
     sqlite3 room_backend/db.sqlite3 "PRAGMA journal_mode;"
     ```

6. Rule engine MQTT test fails
   - Run `python simulation/test_rule_engine_mqtt.py` and check output for which assertions failed.
   - Verify that `.env` has all required variables.

7. ESP32 not receiving relay commands
   - Verify the ESP32 is subscribed to `room/relays/state`.
   - Check MQTT connectivity: `mosquitto_sub -t "room/relays/state" -v`
   - Ensure the rule engine is running and publishing.

## 12. Quick Checklist

```text
 1. Start Mosquitto
 2. Start Django
 3. Start mqtt_logger
 4. Start rule_engine (publishes to MQTT, no local GPIO)
 5. Start ML service (ML/test_prediction_api.py)
 6. Start simulator (or hardware publisher) and keep it running
 7. Ensure ESP32 relay controller is powered and connected to MQTT
 8. Open dashboard (dashboard/index.html) in browser
 9. Test rule engine:          python simulation/test_rule_engine_mqtt.py
10. Verify live MQTT traffic:  mosquitto_sub -t "room/#" -v
11. Verify dashboard/API responses
```

Shutdown order:

1. Stop simulator/publishers
2. Stop ML service
3. Stop workers (rule engine publishes Mode C on shutdown for ESP32 safety)
4. Stop Django
5. Stop Mosquitto
