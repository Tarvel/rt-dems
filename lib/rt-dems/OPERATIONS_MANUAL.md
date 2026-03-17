# Smart Room Energy Management System - Operations Manual

This manual is for the current working system where:

1. The ML service in `ML/app.py` uses `local_inference_wrapper.py`.
2. The ML service both serves HTTP (`/predict`) and participates in MQTT.
3. Rule decisions are watt-based.

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
5. FastAPI ML service
6. Data simulator (or real hardware publisher)

## 4. Startup Order

Use separate terminals.

Important: For realtime dashboard values, the simulator (or hardware publisher) must be running continuously.

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

### Terminal 5 - FastAPI ML service (required)

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate
cd ML
python app.py
```

Health check (new terminal):

```bash
curl -s http://127.0.0.1:5000/
```

Expected response should confirm the FastAPI service is running.

### Terminal 6 - Simulator (or hardware publisher)

This step is required for realtime updates when hardware is not connected.

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate
python simulation/data_simulator.py
```

Keep this terminal running. If it stops, new sensor values will stop.

### Terminal 7 - Open dashboard in browser (optional)

Open this file in your browser:

```
file:///home/tai/Downloads/PEOJECT%20RESEARCH%20REFERENCES/PROJECT_CODE/dashboard/index.html
```

### Optional Terminal 8 - HTTP sanity test

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE/ML
source ../venv/bin/activate
python test_api.py
```

## 5. What to Expect When Running

1. Simulator publishes sensor payloads to `room/sensors`.
2. ML service subscribes to `room/sensors`, runs model inference, publishes to `room/ml/predictions`.
3. Logger stores rolling 5-minute averages in SQLite.
4. Rule engine evaluates every configured interval and publishes `room/relays/state`.
5. API endpoints expose persisted history.
6. FastAPI HTTP endpoint on port `5000` stays available for direct `/predict` testing.

## 6. Configuration

### 6.1 Rule engine environment variables

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

### 6.2 Broker and topic defaults

1. MQTT broker: `localhost`
2. MQTT port: `1883`
3. Sensor topic: `room/sensors`
4. Prediction topic: `room/ml/predictions`

## 7. Verification Commands

### 7.1 Subscribe to all MQTT messages

```bash
mosquitto_sub -t "room/#" -v
```

### 7.2 Check database data

```bash
sqlite3 room_backend/db.sqlite3 "SELECT id,timestamp,temperature,humidity,battery_level FROM energy_sensorlog ORDER BY id DESC LIMIT 5;"
sqlite3 room_backend/db.sqlite3 "SELECT id,timestamp,predicted_energy_range,peak_demand FROM energy_mlprediction ORDER BY id DESC LIMIT 5;"
sqlite3 room_backend/db.sqlite3 "SELECT id,timestamp,mode,relay_1,relay_2,relay_3 FROM energy_relaystate ORDER BY id DESC LIMIT 5;"
```

### 7.3 Check HTTP endpoints

```bash
curl -s http://127.0.0.1:8000/api/v1/sensors/latest/
curl -s http://127.0.0.1:8000/api/v1/predictions/latest/
curl -s http://127.0.0.1:8000/api/v1/relays/current/
curl -s http://127.0.0.1:5000/predict -X POST -H "Content-Type: application/json" -d '{"timestamp":"2026-03-17 14:00:00","temperature_c":32.5,"humidity":60.0,"lux":450.0,"occupancy":1,"lag_1h":1.1,"lag_2h":1.05,"lag_3h":0.95,"lag_24h":1.2}'
```

## 8. systemd Deployment

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

## 9. Troubleshooting

1. MQTT disconnected
   - Ensure broker is running with `systemd/mosquitto.conf`.
   - Ensure port `1883` is available.

2. No predictions arriving in `room/ml/predictions`
   - Ensure `ML/app.py` is running.
   - Ensure model files in `ML/` are present.

3. `/predict` fails
   - Ensure `ML/app.py` started without model load errors.
   - Check terminal output for missing model/scaler files.

4. Rule mode seems wrong
   - Validate watt thresholds (`MODE_A_MAX_W`, `MODE_B_MAX_W`, `MODE_C_MAX_W`).
   - Confirm prediction payload contains `predicted_power_w` or compatible fallback fields.

5. Database locked errors
   - Confirm WAL mode:
     ```bash
     sqlite3 room_backend/db.sqlite3 "PRAGMA journal_mode;"
     ```

## 10. Quick Checklist

```text
1. Start Mosquitto
2. Start Django
3. Start mqtt_logger
4. Start rule_engine
5. Start FastAPI ML service (`ML/app.py`)
6. Start simulator (or hardware publisher) and keep it running
7. Open dashboard (`dashboard/index.html`) in browser
8. Verify live MQTT traffic with: mosquitto_sub -t "room/#" -v
9. Verify dashboard/API responses
```

Shutdown order:

1. Stop simulator/publishers
2. Stop ML app
3. Stop workers
4. Stop Django
5. Stop Mosquitto
