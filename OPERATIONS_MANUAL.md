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
pip install RPi.GPIO
```

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

## 6. What to Expect When Running

1. Simulator publishes sensor payloads to `room/sensors` every 5 seconds.
2. ML service receives sensor data via MQTT, runs the GRU + LightGBM model, and publishes predictions to `room/ml/predictions`.
3. Logger buffers sensor and prediction data and writes 5-minute averages to SQLite.
4. Rule engine evaluates every configured interval and publishes mode decisions to `room/relays/state`.
5. Django API serves historical data at `/api/v1/*`.
6. Dashboard shows live data from MQTT in the browser.

## 7. Configuration

### 7.1 Rule engine environment variables

```bash
export RULE_EVAL_INTERVAL_SECONDS=120
export MODE_A_MAX_W=2400
export MODE_B_MAX_W=1400
export MODE_C_MAX_W=800
```

Production interval example:

```bash
export RULE_EVAL_INTERVAL_SECONDS=1800
```

### 7.2 Broker and topic defaults

1. MQTT broker: `localhost`
2. MQTT port: `1883`
3. Sensor topic: `room/sensors`
4. Prediction topic: `room/ml/predictions`

## 8. Verification Commands

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

## 9. systemd Deployment

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

## 10. Troubleshooting

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

## 11. Quick Checklist

```text
1. Start Mosquitto
2. Start Django
3. Start mqtt_logger
4. Start rule_engine
5. Start ML service (ML/test_prediction_api.py)
6. Start simulator (or hardware publisher) and keep it running
7. Open dashboard (dashboard/index.html) in browser
8. Verify live MQTT traffic with: mosquitto_sub -t "room/#" -v
9. Verify dashboard/API responses
```

Shutdown order:

1. Stop simulator/publishers
2. Stop ML service
3. Stop workers
4. Stop Django
5. Stop Mosquitto
