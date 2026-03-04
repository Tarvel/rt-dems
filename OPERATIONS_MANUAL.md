# Smart Room Energy Management System — Complete Operations Manual

This manual walks you through **every step** to get the entire system running on your Raspberry Pi (or a development machine for testing).

---

## Table of Contents

1. [Prerequisites & Installation](#1-prerequisites--installation)
2. [Directory Structure](#2-directory-structure)
3. [Step-by-Step Startup Guide](#3-step-by-step-startup-guide)
4. [Testing the Full Pipeline](#4-testing-the-full-pipeline)
5. [API Endpoints Reference](#5-api-endpoints-reference)
6. [Production Deployment with systemd](#6-production-deployment-with-systemd)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Prerequisites & Installation

### 1.1 System Packages

Install Mosquitto (MQTT broker) on your Raspberry Pi or Linux dev machine:

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients sqlite3
```

Verify Mosquitto installed:

```bash
mosquitto -h
# Should print the Mosquitto help/usage text
```

### 1.2 Python Virtual Environment

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE

# Create virtual environment (skip if "venv" folder already exists)
python3 -m venv venv

# Activate it
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

> **On the Raspberry Pi only**, also install GPIO support:
> ```bash
> pip install RPi.GPIO
> ```
> On your laptop/desktop, skip this — the code has a built-in MockGPIO that works automatically.

### 1.3 Django Database Setup

```bash
cd room_backend

# Generate migration files
python manage.py makemigrations energy

# Apply migrations (creates db.sqlite3 with all tables)
python manage.py migrate

# (Optional) Create an admin user for the Django admin panel
python manage.py createsuperuser
```

Verify WAL mode is active:

```bash
sqlite3 db.sqlite3 "PRAGMA journal_mode;"
# Expected output: wal
```

Go back to project root:

```bash
cd ..
```

---

## 2. Directory Structure

```
PROJECT_CODE/
├── room_backend/                # Django project
│   ├── manage.py                # Django CLI entry point
│   ├── db.sqlite3               # SQLite database (auto-created by migrate)
│   ├── room_backend/            # Django settings package
│   │   ├── settings.py          # DB config, WAL mode, DRF
│   │   ├── urls.py              # Root URL routing → /api/v1/
│   │   └── wsgi.py              # WSGI entry point
│   └── energy/                  # Main Django app
│       ├── models.py            # SensorLog, MLPrediction, RelayState
│       ├── serializers.py       # DRF read-only serializers
│       ├── views.py             # 6 GET API endpoints
│       ├── urls.py              # App URL patterns
│       └── admin.py             # Admin panel registrations
│
├── workers/                     # Background MQTT services
│   ├── mqtt_logger.py           # Buffers sensors → 5-min avg → SQLite
│   └── rule_engine.py           # Rule evaluation → GPIO relays
│
├── simulation/
│   └── data_simulator.py        # Publishes fake sensor + ML data
│
├── dashboard/
│   └── index.html               # Browser-based live dashboard
│
├── systemd/                     # Deployment configs
│   ├── mqtt-logger.service
│   ├── rule-engine.service
│   └── mosquitto.conf           # Mosquitto config (MQTT + WebSocket)
│
├── integration_contract.md      # MQTT topic/payload reference
├── requirements.txt
└── README.md
```

---

## 3. Step-by-Step Startup Guide

You need **5 terminal windows** (or tmux panes). Here is the exact order:

### Terminal 1 — Start the MQTT Broker (Mosquitto)

This must start FIRST because everything else connects to it.

```bash
# Stop any existing Mosquitto instance
sudo systemctl stop mosquitto 2>/dev/null

# Start with our custom config (includes WebSocket on port 9001)
mosquitto -c /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE/systemd/mosquitto.conf -v
```

You should see output like:

```
1709312345: mosquitto version X.X.X starting
1709312345: Opening ipv4 listen socket on port 1883.
1709312345: Opening websockets listen socket on port 9001.
```

> **Key detail:** Port 1883 is for the Python workers. Port 9001 is for the browser dashboard.

**Leave this terminal running.**

---

### Terminal 2 — Start the Django API Server

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate
cd room_backend

python manage.py runserver 0.0.0.0:8000
```

You should see:

```
System check identified no issues (0 silenced).
Starting development server at http://0.0.0.0:8000/
```

Test it: Open a browser and go to `http://127.0.0.1:8000/api/v1/sensors/` — you should see an empty JSON list.

**Leave this terminal running.**

---

### Terminal 3 — Start the MQTT Logger

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate

python workers/mqtt_logger.py
```

You should see:

```
INFO mqtt_logger: Starting MQTT Logger (flush every 300s)
INFO mqtt_logger: Connected to MQTT broker at localhost:1883
INFO mqtt_logger: Subscribed to room/sensors, room/ml/predictions
```

This worker:
- Listens for sensor data on `room/sensors`
- Listens for ML predictions on `room/ml/predictions`
- Buffers all readings in memory
- Every **5 minutes**, writes the averaged values to SQLite
- Republishes the average to `room/data/averaged`

**Leave this terminal running.**

---

### Terminal 4 — Start the Rule Engine

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate

python workers/rule_engine.py
```

You should see:

```
INFO rule_engine: Starting Rule Engine (eval every 300s)
INFO rule_engine: Running on Raspberry Pi: False          ← (True on actual Pi)
INFO rule_engine: GPIO initialized: P1=pin17, P2=pin27, P3=pin22
INFO rule_engine: Initial state: Mode C (Baseline Load)
INFO rule_engine: Connected to MQTT broker at localhost:1883
```

This worker:
- Listens for sensor data on `room/sensors`
- Listens for ML predictions on `room/ml/predictions`
- Every **5 minutes**, evaluates the 3-phase rule hierarchy
- Controls GPIO relays (or prints mock GPIO states on non-Pi machines)
- Publishes the relay state to `room/relays/state`
- Logs every decision to the `energy_relaystate` table

**Leave this terminal running.**

---

### Terminal 5 — Start the Data Simulator

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate

python simulation/data_simulator.py
```

You should see:

```
============================================================
  Smart Room — Data Simulator
============================================================
  Broker  : 127.0.0.1:1883
  Topics  : room/sensors, room/ml/predictions
  Interval: every 5s
============================================================

✓ Simulator connected to MQTT broker

Publishing simulated data… Press Ctrl+C to stop.

  [   1] Temp:  26.2°C | Batt:  84.7% | Occ: OCCUPIED | ML: 2341 vs 2500
  [   2] Temp:  26.0°C | Batt:  84.3% | Occ: OCCUPIED | ML: 1876 vs 2500
  ...
```

The simulator publishes every **5 seconds** so you can see data flowing quickly. The logger and rule engine still evaluate every 5 minutes.

**Verification checkpoints** (in the other terminals):
- **Terminal 3 (Logger):** Should print `Buffered sensor reading (N in buffer)` as data arrives.
- **Terminal 4 (Rule Engine):** Should print `Updated latest sensor data` as data arrives.

---

### Open the Dashboard

Open this file in your browser:

```
file:///home/tai/Downloads/PEOJECT%20RESEARCH%20REFERENCES/PROJECT_CODE/dashboard/index.html
```

Or if running on the Pi and accessing from another device on the LAN:

```
http://<PI_IP_ADDRESS>:8000/dashboard/index.html
```

> **Note:** The dashboard connects via WebSocket to `ws://127.0.0.1:9001`. If you're accessing the dashboard from a different machine on the LAN, edit the `MQTT_HOST` variable in `dashboard/index.html` to the Pi's IP address.

The dashboard should show:
- **Connection badge:** Green "Connected"
- **Room Status:** Live temperature (turns red if > 28°C), occupancy dot
- **Battery Status:** Percentage with animated progress bar
- **Active Load Level:** Will show the current relay mode after the rule engine evaluates
- **Activity Log:** Scrolling list of incoming MQTT messages

---

## 4. Testing the Full Pipeline

### 4.1 Verify Data is Flowing

After starting all 5 services + the simulator, wait for about 30 seconds. Then check:

**Check MQTT messages are arriving** (quick CLI test):

```bash
# In a new terminal — subscribe to ALL room/ topics
mosquitto_sub -t "room/#" -v
```

You should see messages scrolling on `room/sensors` and `room/ml/predictions`.

**Check the logger wrote to the database** (wait at least 5 minutes, or modify `FLUSH_INTERVAL` in `mqtt_logger.py` to `30` for testing):

```bash
sqlite3 room_backend/db.sqlite3 "SELECT * FROM energy_sensorlog ORDER BY id DESC LIMIT 5;"
```

**Check the rule engine logged decisions:**

```bash
sqlite3 room_backend/db.sqlite3 "SELECT * FROM energy_relaystate ORDER BY id DESC LIMIT 5;"
```

**Check the API serves the data:**

```bash
# Sensor logs
curl -s http://127.0.0.1:8000/api/v1/sensors/ | python3 -m json.tool

# Latest sensor
curl -s http://127.0.0.1:8000/api/v1/sensors/latest/ | python3 -m json.tool

# Current relay state
curl -s http://127.0.0.1:8000/api/v1/relays/current/ | python3 -m json.tool
```

### 4.2 Speed Up Testing (Optional)

For fast testing, you can temporarily reduce the flush/evaluation interval:

| File | Variable | Default | Test Value |
|------|----------|---------|------------|
| `workers/mqtt_logger.py` | `FLUSH_INTERVAL` | `300` (5 min) | `30` (30 sec) |
| `workers/rule_engine.py` | `EVAL_INTERVAL` | `300` (5 min) | `30` (30 sec) |
| `simulation/data_simulator.py` | `PUBLISH_INTERVAL` | `5` (5 sec) | `3` (3 sec) |

> **Remember to change these back to 300 seconds before your final presentation!**

### 4.3 Manual MQTT Publish Test

Instead of the simulator, you can publish a single message manually:

```bash
# Publish a sensor reading
mosquitto_pub -t "room/sensors" -m '{"temperature":30.5,"humidity":62.0,"occupancy":1,"voltage":220.0,"current":5.0,"battery_level":75.0}'

# Publish an ML prediction
mosquitto_pub -t "room/ml/predictions" -m '{"predicted_energy_range":2800,"peak_demand":2500}'
```

---

## 5. API Endpoints Reference

Base URL: `http://<PI_IP>:8000/api/v1/`

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/sensors/` | Paginated 5-min sensor logs (newest first) |
| GET | `/api/v1/sensors/latest/` | Single latest sensor reading |
| GET | `/api/v1/predictions/` | Paginated ML prediction history |
| GET | `/api/v1/predictions/latest/` | Latest ML prediction |
| GET | `/api/v1/relays/` | Paginated relay decision history |
| GET | `/api/v1/relays/current/` | Current relay mode & state |

**Pagination:** Add `?page=2`, `?page=3`, etc. Default page size is 50.

---

## 6. Production Deployment with systemd

When deploying to the Raspberry Pi for your final presentation:

### 6.1 Install Mosquitto Config

```bash
sudo cp systemd/mosquitto.conf /etc/mosquitto/conf.d/room.conf
sudo systemctl restart mosquitto
sudo systemctl enable mosquitto   # Start on boot
```

### 6.2 Install Worker Services

**Edit the service files first** if your project path is different from `/home/pi/PROJECT_CODE`:

```bash
# Edit paths in both .service files to match your actual install directory
nano systemd/mqtt-logger.service
nano systemd/rule-engine.service
```

Then install:

```bash
sudo cp systemd/mqtt-logger.service /etc/systemd/system/
sudo cp systemd/rule-engine.service /etc/systemd/system/

sudo systemctl daemon-reload

# Start and enable services
sudo systemctl enable --now mqtt-logger
sudo systemctl enable --now rule-engine
```

### 6.3 Check Service Status

```bash
sudo systemctl status mqtt-logger
sudo systemctl status rule-engine

# View live logs
sudo journalctl -u mqtt-logger -f
sudo journalctl -u rule-engine -f
```

### 6.4 Run Django with Gunicorn (Production)

For production, replace `manage.py runserver` with Gunicorn:

```bash
pip install gunicorn

cd room_backend
gunicorn room_backend.wsgi:application --bind 0.0.0.0:8000 --workers 2
```

---

## 7. Troubleshooting

### "Address already in use" on port 1883

```bash
# Check what's using the port
sudo lsof -i :1883

# Kill existing Mosquitto
sudo systemctl stop mosquitto
# Or kill by PID
sudo kill <PID>
```

### "Database is locked" errors

This should not happen with WAL mode. Verify:

```bash
sqlite3 room_backend/db.sqlite3 "PRAGMA journal_mode;"
# Must return: wal
```

If it doesn't, delete the database and re-run migrations:

```bash
rm room_backend/db.sqlite3
cd room_backend && python manage.py migrate && cd ..
```

### Dashboard shows "Disconnected"

1. Make sure Mosquitto is running **with the custom config** (which enables WebSockets on port 9001).
2. Check that you started Mosquitto with: `mosquitto -c systemd/mosquitto.conf -v`
3. If accessing from another machine, change `MQTT_HOST` in `dashboard/index.html` to the Pi's IP.

### Workers can't connect to MQTT

```bash
# Test if Mosquitto is listening
mosquitto_pub -t "test" -m "hello"
mosquitto_sub -t "test" -C 1

# If it fails, check Mosquitto logs
sudo journalctl -u mosquitto -n 20
```

### Rule Engine says "No sensor data yet"

The rule engine waits for at least one MQTT message before evaluating. Start the simulator or publish a manual test message.

### GPIO permission denied (on Pi)

The rule engine must run as root for GPIO access:

```bash
sudo python workers/rule_engine.py
# Or use the systemd service (which runs as root)
```

---

## Quick-Reference: Startup Checklist

```
1. ☐  mosquitto -c systemd/mosquitto.conf -v          (Terminal 1)
2. ☐  cd room_backend && python manage.py runserver    (Terminal 2)
3. ☐  python workers/mqtt_logger.py                    (Terminal 3)
4. ☐  python workers/rule_engine.py                    (Terminal 4)
5. ☐  python simulation/data_simulator.py              (Terminal 5)
6. ☐  Open dashboard/index.html in browser
```

**Shutdown order:** Stop the simulator first (Ctrl+C), then the workers, then Django, then Mosquitto.
