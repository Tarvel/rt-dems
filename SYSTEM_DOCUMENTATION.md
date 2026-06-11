# Smart Room Energy Management System — Complete Technical Documentation

This document explains **everything** about how the system works, piece by piece, in simple English. Read this and you will be able to fully explain and defend every part of the project.

---

## Table of Contents

1. [The Big Picture — What This System Actually Does](#1-the-big-picture)
2. [The Hardware Layer — What is Physically Connected](#2-the-hardware-layer)
3. [The Software Components — Every Piece Explained](#3-the-software-components)
4. [The Full Data Flow — From Sensor to Screen](#4-the-full-data-flow)
5. [MQTT Explained — The Messaging System](#5-mqtt-explained)
6. [The Database — SQLite and WAL Mode](#6-the-database)
7. [The MQTT Logger — How Sensor Data Gets Saved](#7-the-mqtt-logger)
8. [The Rule Engine — The Brain of the System](#8-the-rule-engine)
9. [The Django API — How the Frontend Gets Historical Data](#9-the-django-api)
10. [The Dashboard — The Live Visual Interface](#10-the-dashboard)
11. [The Data Simulator — Fake Data for Testing](#11-the-data-simulator)
12. [Systemd Services — Running Everything Automatically](#12-systemd-services)
13. [Security and Concurrency Design](#13-security-and-concurrency-design)
14. [How to Defend Each Design Decision](#14-how-to-defend-each-design-decision)

---

## 1. The Big Picture

### What problem does this solve?

University hostels waste a lot of energy. Lights stay on in empty rooms. Air conditioners run when nobody is there. Heavy electrical appliances run even when the battery system is almost dead. This system solves that by automatically controlling which electrical devices are allowed to be energized, based on real-time data from sensors.

### What does the system do in one sentence?

It reads sensor data (temperature, occupancy, battery level), receives energy predictions from a machine learning model, and then automatically switches electrical relays ON or OFF to save energy — all running on a single Raspberry Pi.

### The three teams

This is a group project split between three teams:

| Team                     | Responsibility                                                                     | What they give us                             | What they take from us               |
| ------------------------ | ---------------------------------------------------------------------------------- | --------------------------------------------- | ------------------------------------ |
| **Hardware (Group 1)**   | Physical sensors (temperature, humidity, motion, lux, battery) + ESP32 relay controller | Sensor data published to `room/hardware/nano` via MQTT; relay actuation on `room/relays/state` | Relay mode decisions via MQTT        |
| **ML Team (Group 2)**    | Machine learning models (LightGBM + XGBoost + MH Blend) that predict energy usage | Trained model artifacts (`.pkl` files + CSV)  | We call their model via HTTP `POST /predict` |
| **Us (Group 3)**         | The Raspberry Pi server, database, MQTT broker, API endpoints, rule engine, hardware bridge, dashboard | REST API endpoints, MQTT topics for live data | We receive sensor data via MQTT and run ML predictions via HTTP |

### What the Raspberry Pi does (our responsibility)

The Raspberry Pi (4 or 5) acts as four things at once:

1. **MQTT Broker** — It runs Mosquitto, which is the message post office. Every team sends and receives messages through it.
2. **Database Server** — It stores the 5-minute averaged historical data in SQLite.
3. **ML Inference Server** — It runs the LightGBM + XGBoost prediction model as a FastAPI service, called by the rule engine via HTTP.
4. **Control Hub** — It runs the rule engine that makes EDFI-based decisions about which relays to turn ON or OFF, publishing decisions via MQTT for the ESP32 to actuate.

---

## 2. The Hardware Layer

### The Raspberry Pi

- **Model:** Raspberry Pi 4 (4GB RAM) or Raspberry Pi 5
- **OS:** Raspberry Pi OS (Linux-based)
- **Network:** Connected to the local network via Wi-Fi or Ethernet
- **GPIO Pins:** 40 pins on the header, but **not used by the rule engine**. Relay actuation is delegated to an external ESP32 microcontroller via MQTT.

### The 3 Relays

A relay is like a remote-controlled switch. It uses a small electrical signal from the Raspberry Pi to turn a much larger electrical circuit ON or OFF. Think of it like using a TV remote (small signal) to turn on a TV (big device).

We have 3 relays, each connected to a different GPIO pin on the Pi:

| Relay | GPIO Pin (BCM) | Priority Level | What it controls |
|-------|---------------|----------------|-----------------|
| Relay 1 | Pin 17 | Priority 1 (Critical) | Essential loads — lights, emergency systems |
| Relay 2 | Pin 27 | Priority 2 (Medium) | Comfort loads — fans, ventilation |
| Relay 3 | Pin 22 | Priority 3 (Luxury) | Heavy loads — AC, heaters, high-energy appliances |

### How relays work with the ESP32

- The rule engine publishes `relay_1`, `relay_2`, `relay_3` booleans to `room/relays/state` via MQTT
- The ESP32 subscribes to this topic and drives its GPIO pins accordingly
- A `true` value means the relay should be ON (circuit closed, device energized)
- A `false` value means the relay should be OFF (circuit open, device de-energized)
- We use **BCM numbering** on the ESP32 side (the pin mapping is configured in the ESP32 firmware)

### The 3 Operating Modes

| Mode | Name | Relay 1 | Relay 2 | Relay 3 | Meaning |
|------|------|:-------:|:-------:|:-------:|---------|
| **A** | Peak Demand | ON | ON | ON | Everything runs. Battery is healthy, energy supply is good. |
| **B** | Average Load | ON | ON | OFF | Fans and lights work, but heavy appliances like AC are cut off. |
| **C** | Baseline Load | ON | OFF | OFF | Survival mode. Only the most critical devices stay energized. |

---

## 3. The Software Components

Here is every piece of software in the system and what it does:

### 3.1 Eclipse Mosquitto (The MQTT Broker)

**What it is:** A lightweight message broker. Think of it as a post office.

**What it does:** It sits on the Pi and accepts messages from anyone on the network. When someone publishes a message to a "topic" (like a mailing address), Mosquitto delivers that message to everyone who has subscribed to that topic.

**Why we use it:** It is the standard MQTT broker for IoT. It is tiny, fast, and runs perfectly on a Raspberry Pi. It handles the real-time communication between all teams.

**Config file:** `systemd/mosquitto.conf`

The config sets up two listeners:
- **Port 1883** — Standard MQTT protocol. Used by all Python scripts (logger, rule engine, simulator).
- **Port 9001** — WebSocket protocol. Used by the browser dashboard (because web browsers cannot use raw MQTT, they must use WebSockets).

```
listener 1883 0.0.0.0    ← Python clients connect here
protocol mqtt

listener 9001 0.0.0.0    ← Browser dashboard connects here
protocol websockets

allow_anonymous true      ← No username/password needed (okay for local network)
```

### 3.2 SQLite3 Database

**What it is:** A file-based database. The entire database is a single file called `db.sqlite3`.

**What it does:** Stores historical data — the 5-minute averaged sensor readings, ML predictions, and every relay decision the rule engine has ever made.

**Why we use it:** No need to install a heavy database server like MySQL or PostgreSQL. SQLite runs directly inside our Python code. Perfect for a Pi with limited resources.

**Why WAL mode matters:** See Section 6 for the full explanation.

### 3.3 Django + Django REST Framework

**What it is:** Django is a Python web framework. Django REST Framework (DRF) is an add-on that makes it easy to create JSON API endpoints.

**What it does:** It serves the historical data from SQLite to the frontend team via HTTP GET requests. The frontend calls URLs like `/api/v1/sensors/` and gets back JSON data.

**Why we use it:** Django handles all the boring stuff automatically — database connections, URL routing, pagination, JSON serialization. DRF adds ready-made list views with filtering and pagination out of the box.

### 3.4 mqtt_logger.py (Background Worker)

**What it is:** A standalone Python script that runs in the background forever.

**What it does:** It listens for sensor and ML messages on MQTT, collects them in memory, and every 5 minutes computes the average and writes one row to the database.

**Why it exists:** The hardware team publishes sensor data very frequently (every few seconds). If we wrote every single reading to the database, it would fill up the SD card and slow everything down. By averaging over 5 minutes, we store useful summarized data without wasting storage.

### 3.5 rule_engine.py (Background Worker)

**What it is:** Another standalone Python script that runs in the background forever.

**What it does:** It operates on two timescales:

1. **Continuous prediction (~every 5 seconds):** Every time new sensor data arrives on MQTT, the rule engine sends the **real-time sensor reading** (not averaged) to the ML service via `POST /predict`. The result is cached in memory so a fresh prediction is always available.

2. **Decision interval (every 5 minutes):** A background timer fires and uses the cached ML prediction to evaluate the EDFI-based threshold rules. It decides which mode (A, B, or C) to use and **publishes the decision to MQTT** (`room/relays/state`). An external ESP32 microcontroller subscribes to this topic and actuates the physical relay modules.

3. **Battery lag tracking (every 30 seconds):** A separate background thread shifts the battery lag readings (T-now, T-1, T-2) and publishes lightweight `battery_lag_update` payloads to the dashboard.

**EDFI threshold logic:** The rule engine uses three configurable thresholds from `.env` (`PEAK_THRESHOLD`, `MODERATE_THRESHOLD`, `BASELINE_THRESHOLD`) to classify predicted energy into load tiers, then factors in battery stability to select a mode. See Section 8 for the full decision hierarchy.

**Why it exists:** This is the core intelligence of the system. Without it, the sensors just collect data but nothing happens. The rule engine is what turns data into action.

**Important architecture note:** The rule engine does **not** drive any local GPIO pins. All hardware actuation is handled by the ESP32 over MQTT. This decouples the decision logic from the physical hardware, allowing the Pi to focus on computation while the ESP32 handles electrical switching.

### 3.6 ML Prediction Service (FastAPI) — LIGHT_ML_MODEL

**File:** `LIGHT_ML_MODEL/main.py` (configurable via `ML_SERVICE_SCRIPT` in `.env`)

**What it is:** A FastAPI server that runs the **LightGBM + XGBoost + Metropolis-Hastings (MH) blend** energy prediction model. This replaced the earlier TE-GRU + LightGBM model.

**What it does:** It operates in **HTTP-only mode**. The rule engine calls `POST /predict` with live sensor values, and the service returns a prediction. MQTT auto-prediction is disabled to prevent duplicate predictions — the rule engine controls the prediction frequency.

**How predictions work:**

1. The rule engine sends real-time sensor values (`temperature_c`, `humidity`, `lux`, `occupancy`, `datetime_str`) via `POST /predict`.
2. The service uses `datetime_str` to find the closest matching row in a historical CSV file (`mydatanew.csv`, 42,240 rows). This CSV provides the **energy context window** — the last 30 rows of energy history needed for lag features.
3. The service injects the live sensor values into the last row of the context window.
4. It engineers 14 features: 4 sensor values + 4 energy lag features (`lag1`, `lag24`, `rolling3`, `rolling24`) + 4 cyclical time features (`hour_sin`, `hour_cos`, `dow_sin`, `dow_cos`) + `hour` + `day_of_week`.
5. LightGBM and XGBoost each produce a raw prediction. A Metropolis-Hastings adaptive blend combines them.
6. An XGBoost residual correction model adjusts the blended output.
7. LightGBM quantile models produce upper and lower confidence bounds.

**Why the CSV context window exists:** The model was trained with energy-based lag features (`lag1` = energy 5 min ago, `lag24` = energy 2 hours ago). Since our hardware does not measure actual energy consumption (`E=0.000 kWh` in sensor data), the CSV provides historical energy values to compute these lags. Without it, the model has no "memory" and cannot produce meaningful predictions. The CSV pointer advances with each call so that successive predictions draw from different energy history.

**Model assets required** (in `LIGHT_ML_MODEL/model_assets/`):

| File | Purpose |
|------|---------|
| `xgb.pkl` | XGBoost base model |
| `lgb.pkl` | LightGBM base model |
| `beta.pkl` | MH blend weights |
| `scaler.pkl` | Feature scaler |
| `res_model.pkl` | XGBoost residual correction model |
| `lgb_lower.pkl` | LightGBM lower quantile model |
| `lgb_upper.pkl` | LightGBM upper quantile model |
| `residual_std.pkl` | Residual standard deviation |
| `mydatanew.csv` | Historical energy context (42,240 rows) |

**How to swap models:** Change two lines in `.env` and restart:

```bash
# Old model (TE-GRU + LightGBM):
MODEL_ASSET_DIR=new_ml_model/New folder
ML_SERVICE_SCRIPT=workers/ml_service.py

# New model (LightGBM + XGBoost + MH Blend) — current:
MODEL_ASSET_DIR=LIGHT_ML_MODEL/model_assets
ML_SERVICE_SCRIPT=LIGHT_ML_MODEL/main.py
```

**MQTT retry:** If the MQTT broker is not running when the ML service starts, it retries the connection every 5 seconds in the background.

**Port:** The ML service runs on port `5000`.

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `POST /predict` | Accept live sensor values → return energy prediction | Primary endpoint (called by rule engine) |
| `GET /predict_next` | Advance CSV pointer by one row → return prediction | Simulation/testing |
| `GET /metadata` | Show loaded models, CSV status, config | Debugging |
| `GET /reset` | Reset CSV pointer to beginning | Testing |

### 3.7 data_simulator.py (Testing Tool)

**What it is:** A Python script that plays back real sensor data from a CSV file.

**What it does:** It reads from `abs_smart_grid_dataset_20k.csv` and publishes each row as a sensor payload to `room/sensors` via MQTT. It is **prediction-paced** — after publishing a row, it waits for the ML service to publish a prediction before advancing to the next row. This means the simulation speed is determined by how fast the model responds, not a fixed timer.

**What it publishes:** Each payload includes `temperature_c`, `temperature`, `humidity`, `lux`, `occupancy`, `energy_kw`, and `battery_level`. It does NOT publish `voltage` or `current` (these are optional and the logger defaults them to `0.0`).

**Battery simulation:** Battery starts at 85% and drains 0.1% per row deterministically (no randomness), flooring at 20%.

**Hard reset on boot:** Every time the simulator starts, it resets to Row 1 and calls the ML API's `/reset` endpoint to synchronise the model's CSV pointer.

**Why it exists:** During development and testing, you do not have the hardware team's sensors connected. The simulator stands in for the real hardware.

### 3.8 Manual ML Test Tools

**Files:** `ML/test.py` (CLI) and `ML/test_dashboard.html` (browser)

**What they are:** Two tools for manually testing the ML model's predictions via HTTP. They do not use MQTT.

**What they do:**
- `test.py` — An interactive CLI where you type sensor values (temperature, humidity, lux, occupancy, energy) and see the model's prediction.
- `test_dashboard.html` — A web page served by the ML service at `http://127.0.0.1:5000`. It has input fields for each sensor value and shows the prediction output.

**Why they exist:** For supervisor demonstrations. The team can manually input specific values and compare the model output against their own manual calculations to verify the model works correctly.

### 3.9 hw_bridge.py (Hardware Bridge)

**File:** `workers/hw_bridge.py`

**What it is:** A lightweight MQTT translator that bridges Group 1's raw hardware data format into the system's standard `room/sensors` format.

**What it does:**
1. Subscribes to `room/hardware/nano` (the topic Group 1's ESP32/Arduino publishes to).
2. Parses the incoming JSON payload which contains combined environmental + battery fields.
3. Normalizes field names (e.g., maps hardware field names to `temperature_c`, `humidity`, `lux`, `occupancy`, `battery_level`, etc.).
4. Re-publishes the cleaned payload to `room/sensors` for all other services to consume.

**Why it exists:** Group 1's hardware sends data in their own format. Rather than modifying every other component to understand their format, the bridge acts as a single translation layer. If Group 1 changes their JSON structure, only `hw_bridge.py` needs updating.

**Controlled by:** The `DATA_SOURCE` variable in `.env`:
- `hardware` — Only hw_bridge runs (production with real sensors)
- `simulator` — Only data_simulator runs (development/testing)
- `both` — Both run simultaneously (debugging only)

### 3.10 dashboard/index.html (Browser Dashboard)

**What it is:** A single HTML file with CSS and JavaScript embedded.

**What it does:** Opens in any web browser and connects directly to the MQTT broker via WebSockets. It displays live sensor data, battery level, predicted load, battery lag trend (T-now, T-1, T-2), and the current relay mode in real time — no page refresh needed.

**Why it exists:** It gives a visual way to monitor the system. It also proves that the MQTT data flow works end-to-end.

### 3.11 test_rule_engine_mqtt.py (MQTT Integration Test)

**File:** `simulation/test_rule_engine_mqtt.py`

**What it is:** A test script that validates the rule engine publishes the correct JSON payloads for every mode transition.

**What it does:** It mocks the MQTT client, injects sensor/ML state directly into the rule engine's globals, runs evaluations, and asserts the published payload has the correct mode, relay booleans, and reason strings. It covers all mode transitions, day/night lag thresholds, and edge cases like missing ML predictions.

**On any machine:** Runs without hardware or a real MQTT broker. Just `python simulation/test_rule_engine_mqtt.py`.

---

## 4. The Full Data Flow

This is the most important section. Here is the complete journey of data through the system, step by step:

```
STEP 1: Physical sensors  →  STEP 2: Hardware publishes  →  STEP 3: hw_bridge translates
                                      to room/hardware/nano         to room/sensors
                                                                           │
                            ┌──────────────────────────────────────────────┤
                            │                     │                        │
                            ▼                     ▼                        ▼
                     STEP 4a: Logger        STEP 4b: Rule Engine    STEP 4c: Dashboard
                     receives & buffers     receives latest          shows live data
                            │                     │
                            │ (every 5 min)       │ (every ~5s, rate-limited)
                            ▼                     ▼
                     STEP 5a: Computes avg  STEP 5b: Calls ML service
                     and writes to SQLite   via POST /predict (HTTP)
                            │                     │
                            ▼                     │ (every 5 min decision)
                     STEP 6a: Republishes   STEP 5c: Evaluates EDFI
                     averaged data to MQTT  threshold rules
                            │                     │
                            ▼                     ▼
                     STEP 7: Django serves  STEP 6b: Publishes mode
                     historical data via    to room/relays/state +
                     REST API               prediction to room/ml/predictions
                                                  │
                                                  ▼
                                           STEP 7: ESP32 receives
                                           and drives GPIO relays
```

### Detailed walkthrough:

**Step 1: Physical measurement.**
The hardware team's sensors physically measure the room's temperature, humidity, light level (lux), whether someone is present (occupancy via radar), and the battery percentage/voltage.

**Step 2: Hardware team publishes to MQTT.**
Their microcontroller (ESP32/Arduino) packages the readings into a JSON message and publishes it to `room/hardware/nano`.

**Step 3: Hardware Bridge translates.**
The `hw_bridge.py` worker subscribes to `room/hardware/nano`, parses the raw hardware payload, normalizes the field names, and re-publishes as a clean JSON payload to `room/sensors`. This decouples the hardware team's format from the rest of the system.

**Step 4a: The Logger receives and buffers.**
When `mqtt_logger.py` receives a sensor message on `room/sensors`, it adds it to an in-memory buffer (a Python list).

**Step 4b: The Rule Engine receives and caches.**
When `rule_engine.py` receives a sensor message, it overwrites its `latest_sensor` dictionary. It then immediately sends the real-time reading to the ML service via `POST /predict` (HTTP, rate-limited to every ~5 seconds). The ML result is cached in `latest_ml`.

**Step 5a: Logger computes averages (every 5 minutes).**
A background timer fires every 5 minutes. The logger computes the arithmetic average of each field, writes one row to `energy_sensorlog`, and publishes the averaged data to `room/data/averaged`.

**Step 5b: Rule Engine calls ML service (continuous).**
The rule engine sends the latest real-time sensor reading to the ML service via HTTP `POST /predict`. The ML service uses the sensor values + a CSV-based energy context window to compute a prediction with confidence bounds.

**Step 5c: Rule Engine evaluates (every 5 minutes).**
The decision timer fires and uses the cached ML prediction (`latest_ml`) to evaluate EDFI threshold rules. It classifies the predicted energy into Peak/Moderate/Baseline/Very Low tiers, checks battery stability, and determines the mode (A, B, or C).

**Step 6b: Rule Engine publishes.**
After deciding a mode, the rule engine publishes the relay states to `room/relays/state` (for the ESP32 and dashboard) and the ML prediction to `room/ml/predictions` (for the logger and dashboard).

**Step 7: Data is available two ways.**
- **Live (real-time):** The dashboard gets instant updates via MQTT. No delay.
- **Historical:** The frontend team can call the Django API (e.g., `GET /api/v1/sensors/`) to fetch the 5-minute averaged data for charts, graphs, and analysis.

---

## 5. MQTT Explained

### What is MQTT?

MQTT stands for **Message Queuing Telemetry Transport**. It is a lightweight messaging protocol designed for IoT devices. It works on the **publish/subscribe** pattern.

### The Publish/Subscribe Pattern

Imagine a radio station:
- The radio station **broadcasts** on a specific frequency (the "topic").
- Anyone who tunes their radio to that frequency (they "subscribe") will hear the broadcast.
- The radio station does not need to know who is listening.
- Listeners do not need to know who is broadcasting.

MQTT works the same way:
- A **publisher** sends a message to a **topic** (like `room/sensors`).
- The **broker** (Mosquitto) receives the message and forwards it to all **subscribers** of that topic.
- Publishers and subscribers do not need to know about each other. They only know about the broker.

### Why MQTT and not HTTP?

| Feature | MQTT | HTTP |
|---------|------|------|
| Connection | Stays open (persistent) | Opens and closes per request |
| Direction | Two-way (publish and subscribe) | One-way (request and response) |
| Overhead | Tiny (2-byte header) | Large (headers, cookies, etc.) |
| Real-time | Yes — push-based, instant delivery | No — client must poll (ask repeatedly) |
| Good for IoT | Yes — designed for constrained devices | No — too heavy for sensors |

### Our MQTT Topics

| Topic | Who publishes | Who subscribes | What data | How often |
|-------|--------------|----------------|-----------|-----------| 
| `room/sensors` | Hardware Bridge (`hw_bridge.py`) or simulator | Logger, Rule Engine, Dashboard, ML Service (passive listener) | Temperature, humidity, lux, occupancy, energy_kw, battery_level (voltage/current optional) | Every ~2 seconds (hardware) or prediction-paced (simulator) |
| `room/ml/predictions` | Rule Engine (after calling ML via HTTP) | Logger, Dashboard | Predicted energy (Wh), upper/lower bounds, sensor snapshot | At each 5-minute decision interval |
| `room/data/averaged` | Logger | Dashboard | 5-minute averaged sensor data | Every 5 minutes |
| `room/relays/state` | Rule Engine | Dashboard, ESP32 | Current mode (A/B/C), relay states, battery lag (T-now/T-1/T-2), reason | Full payload at decision interval + lightweight battery_lag_update every 30s |
| `room/hardware/nano` | Group 1 hardware (ESP32/Arduino) | Hardware Bridge | Raw combined sensor + battery JSON | Every ~2 seconds |

### QoS (Quality of Service)

We use **QoS 1** for all messages. This means:
- **QoS 0:** Fire and forget. Message might be lost. We do not use this.
- **QoS 1:** At least once delivery. The broker guarantees the message is delivered at least once. If the network hiccups, it will retry. This is what we use.
- **QoS 2:** Exactly once delivery. More overhead, slower. Not needed for sensor data.

### The JSON Payloads

Every MQTT message in our system carries a JSON payload. JSON is a text format that looks like a Python dictionary:

**Sensor payload** (published by hardware team or simulator to `room/sensors`):
```json
{
    "timestamp": "2026-03-22T10:00:00",
    "temperature_c": 27.5,
    "temperature": 27.5,
    "humidity": 62.3,
    "lux": 450.2,
    "occupancy": 1,
    "energy_kw": 1.2345,
    "battery_level": 73.5
}
```

**Notes on the sensor payload:**
- `temperature_c` is the preferred field name. `temperature` is a legacy alias (the logger accepts either).
- `lux` is the luminous intensity in lux — used by the ML model.
- `energy_kw` is the actual measured energy — shown on the dashboard as "Actual Load".
- `voltage` and `current` are **optional**. If missing, the logger defaults them to `0.0` for averaging.

**ML payload** (published by rule engine to `room/ml/predictions` after calling the ML service via HTTP):
```json
{
    "predicted_energy_wh": 45.23,
    "upper_bound_energy_wh": 52.87,
    "lower_bound_energy_wh": 37.59,
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

**Note:** The ML service itself (LIGHT_ML_MODEL) returns a richer payload including `hybrid_final_wh`, `safety_lower_bound_wh`, `safety_upper_bound_wh`, `lgb_raw_wh`, `xgb_raw_wh`, and blend weights. The rule engine extracts the key fields and re-publishes them in the simplified format above.

**Relay state payload — full decision** (published by rule engine to `room/relays/state`):
```json
{
    "mode": "B",
    "relay_1": true,
    "relay_2": true,
    "relay_3": false,
    "battery_t_now": 73.5,
    "battery_t1": 74.0,
    "battery_t2": 74.5,
    "battery_lag_drop": 1.0,
    "battery_lag_interval_seconds": 30,
    "reason": "MODERATE LOAD (EDFI 22.50, 15 <= x < 30); battery_stable(60%) = True → Smart B",
    "timestamp": "2026-03-04T12:30:00+00:00"
}
```

**Relay state payload — lightweight battery lag update** (published every 30 seconds):
```json
{
    "type": "battery_lag_update",
    "battery_t_now": 73.5,
    "battery_t1": 74.0,
    "battery_t2": 74.5,
    "timestamp": "2026-03-04T12:30:15+00:00"
}
```

---

## 6. The Database

### Why SQLite?

SQLite is a serverless database. This means:
- No separate database process needs to be running.
- The entire database is a single file (`db.sqlite3`).
- Python has built-in support for SQLite (the `sqlite3` module).
- It is fast enough for our use case (writing one row every 5 minutes).
- It uses very little memory — perfect for a 4GB Raspberry Pi.

### The WAL Mode Problem and Solution

**The problem:** SQLite normally uses a locking mechanism where only ONE process can write at a time. If the Django server is reading the database while `mqtt_logger.py` tries to write, you get a `database is locked` error. On a Pi running multiple services, this happens often.

**The solution: WAL (Write-Ahead Logging) mode.**

In normal mode, SQLite locks the entire database file when writing. In WAL mode:
- **Readers never block writers.** Django can read while the logger writes.
- **Writers never block readers.** The logger can write while Django reads.
- **Multiple readers can work simultaneously.** Django, the admin panel, and you running `sqlite3` from the terminal can all read at the same time.

WAL works by writing changes to a separate "WAL file" first, then merging them into the main database file later. Readers look at both the main file and the WAL file to see the complete data.

**How we enable WAL mode:**

In `settings.py`, we use a Django signal:

```python
def _enable_wal(sender, connection, **kwargs):
    if connection.vendor == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")    # Enable WAL
        cursor.execute("PRAGMA busy_timeout=5000;")    # Wait up to 5 seconds if locked
```

This function runs automatically every time Django opens a new database connection. The `PRAGMA busy_timeout=5000` tells SQLite to wait up to 5 seconds before giving up if the database happens to be busy, instead of failing immediately.

The background workers (`mqtt_logger.py` and `rule_engine.py`) also set WAL mode independently when they connect to the database, so all three processes are always in WAL mode.

### The 3 Database Tables

| Table Name | Created by | Written by | Read by | Purpose |
|-----------|------------|-----------|---------|---------|
| `energy_sensorlog` | Django migrations | `mqtt_logger.py` | Django API | 5-minute averaged sensor readings |
| `energy_mlprediction` | Django migrations | `mqtt_logger.py` | Django API | ML predictions at each 5-minute flush |
| `energy_relaystate` | Django migrations | `rule_engine.py` | Django API | Every relay decision with timestamp and reason |

#### Table: energy_sensorlog

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Auto-incrementing primary key |
| timestamp | DateTime | When this average was recorded |
| temperature | Float | Averaged temperature in °C |
| humidity | Float | Averaged relative humidity in % |
| occupancy | Integer | 1 = room was mostly occupied, 0 = mostly empty |
| voltage | Float | Averaged voltage in Volts |
| current | Float | Averaged current in Amps |
| battery_level | Float | Averaged battery percentage |

#### Table: energy_mlprediction

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Auto-incrementing primary key |
| timestamp | DateTime | When this prediction was recorded |
| predicted_energy_range | Float | ML model's predicted energy consumption (kWh) |
| peak_demand | Float | The peak demand threshold (kWh) |

#### Table: energy_relaystate

| Column | Type | Description |
|--------|------|-------------|
| id | Integer | Auto-incrementing primary key |
| timestamp | DateTime | When this decision was made |
| mode | Text | "A", "B", or "C" |
| relay_1 | Boolean | True = ON, False = OFF |
| relay_2 | Boolean | True = ON, False = OFF |
| relay_3 | Boolean | True = ON, False = OFF |
| reason | Text | Human-readable explanation of why this mode was chosen |

---

## 7. The MQTT Logger — How Sensor Data Gets Saved

**File:** `workers/mqtt_logger.py`

### What it does step by step:

1. **Starts up** and connects to the MQTT broker on `localhost:1883`.
2. **Subscribes** to two topics: `room/sensors` and `room/ml/predictions`.
3. **When a sensor message arrives:** It parses the JSON, validates that all required fields are present (`voltage` and `current` are optional and default to `0.0`), and adds the data to an in-memory buffer (a Python list called `sensor_buffer`).
4. **When an ML message arrives:** Same thing — parses, validates, adds to `ml_buffer`.
5. **Every 5 minutes** (300 seconds), a background timer triggers the **flush** operation:
   - It copies all readings from the buffer and clears the buffer (so new readings during the flush go into a fresh buffer).
   - It computes the average of each field across all readings.
   - It writes one row to `energy_sensorlog` and one row to `energy_mlprediction`.
   - It publishes the averaged data to `room/data/averaged` so the dashboard can show it.
6. **On shutdown** (Ctrl+C or `systemctl stop`), it does one final flush to save any remaining buffered data, then disconnects cleanly.

### Thread safety

The buffer is accessed by two threads:
- The **MQTT thread** adds data to the buffer when messages arrive.
- The **flush thread** reads and clears the buffer every 5 minutes.

If both threads try to modify the buffer at the same time, data could get corrupted. To prevent this, we use a `threading.Lock()`:

```python
buffer_lock = threading.Lock()

# When adding data:
with buffer_lock:
    sensor_buffer.append(payload)

# When flushing:
with buffer_lock:
    sensors_snapshot = sensor_buffer.copy()
    sensor_buffer.clear()
```

The `with buffer_lock` statement means "wait until no other thread is using this lock, then lock it for me, and unlock when I'm done." This guarantees only one thread touches the buffer at a time.

### How the average is computed

```python
def compute_sensor_average(readings):
    n = len(readings)       # How many readings we collected
    avg = {
        "temperature": sum(r["temperature"] for r in readings) / n,
        "humidity":    sum(r["humidity"]    for r in readings) / n,
        "occupancy":   1 if sum(r["occupancy"] for r in readings) / n >= 0.5 else 0,
        "voltage":     sum(r.get("voltage", 0.0) for r in readings) / n,
        "current":     sum(r.get("current", 0.0) for r in readings) / n,
        "battery_level": sum(r["battery_level"] for r in readings) / n,
    }
    return avg
```

For occupancy, since it is 0 or 1, we use a majority vote: if the average is 0.5 or higher (meaning the room was occupied more than half the time), we record it as 1 (occupied).

Note that `voltage` and `current` use `.get("field", 0.0)` because they are **optional** — the simulator and some hardware setups do not include them. If a reading is missing these fields, they default to `0.0` for averaging purposes.

### Why direct sqlite3 and not Django ORM?

The logger is an independent Python script, not part of the Django web server. Loading the full Django framework just to insert one row every 5 minutes would waste memory on the Pi. Instead, we use Python's built-in `sqlite3` module to write directly to the same database file that Django reads from. This works because SQLite WAL mode allows concurrent access.

---

## 8. The Rule Engine — The Brain of the System

**File:** `workers/rule_engine.py`

This is the most complex and important part of the system. It is the component that makes real decisions and controls physical hardware.

### What it does step by step:

1. **Starts up** and defaults to **Mode C** (the safest mode — only critical devices on). No GPIO initialization is needed.
2. **Connects to MQTT** and subscribes to `room/sensors` and `room/control/override`.
3. **When sensor messages arrive**, it updates `latest_sensor` and triggers a **continuous prediction**: the latest real-time sensor reading is sent to the ML service via `POST /predict` (rate-limited to once every 5 seconds). The result is cached in `latest_ml`.
4. **Every 5 minutes** (`DECISION_INTERVAL_MINUTES`), the decision timer fires. It uses the cached ML prediction to evaluate the EDFI threshold rules and determine the mode.
5. **A separate 30-second background loop** shifts the battery lag readings (T-now, T-1, T-2) and publishes lightweight `battery_lag_update` payloads to the dashboard.
6. **After evaluation**, it publishes the mode decision and relay booleans to `room/relays/state` for the ESP32 and dashboard to consume, publishes the ML prediction to `room/ml/predictions` for the logger and dashboard, and logs the decision to the database.
7. **On shutdown**, it publishes Mode C to MQTT (so the ESP32 drops to safe state) and disconnects.

### The ESP32 Relay Controller Architecture

**The previous approach:** Earlier versions of the system used `RPi.GPIO` or `gpiozero` to drive relay GPIO pins directly on the Raspberry Pi. This created problems:
- `RPi.GPIO` does not work on Pi 5 (different GPIO chip architecture)
- GPIO libraries require root access or group membership
- The rule engine was tightly coupled to hardware

**The current approach:** The rule engine now operates as a pure decision engine. It evaluates rules and publishes mode decisions to `room/relays/state` via MQTT. An external **ESP32 microcontroller** subscribes to this topic and drives its own GPIO pins to actuate the relay modules.

**Benefits of this architecture:**
- **Decoupled:** The Pi focuses on computation (MQTT, ML, database). The ESP32 focuses on hardware switching.
- **Platform-independent:** The rule engine runs identically on any machine (Pi 4, Pi 5, laptop, server).
- **No GPIO dependencies:** No need for `lgpio`, `gpiozero`, `RPi.GPIO`, or root access on the Pi.
- **Testable:** The rule engine can be fully tested by mocking the MQTT client (see `test_rule_engine_mqtt.py`).

### The EDFI Threshold Decision Hierarchy — Complete Explanation

The rule engine uses the **Energy Demand Forecast Index (EDFI)** — the predicted energy in Wh from the ML model — to classify the current load level. Three configurable thresholds in `.env` define the boundaries:

```
PEAK_THRESHOLD=30       # EDFI >= 30 Wh → Peak Load (Smart A territory)
MODERATE_THRESHOLD=15   # EDFI >= 15 Wh → Moderate Load (Smart B territory)
BASELINE_THRESHOLD=1    # EDFI >= 1 Wh  → Baseline Load (Smart C)
```

**Note:** Occupancy and temperature are only used for dashboard visualization and logging — they do not override the energy-based decisions.

#### Battery Stability Lock — The "3-Time Lag" Check

Before making a decision, the engine checks if the battery is rapidly draining.

**The independent background loop:** We maintain three variables (`battery_t_now`, `battery_t1`, `battery_t2`) to represent the exact battery percentage over the last 90 seconds. A separate background thread wakes up every 30 seconds (`BATTERY_LAG_INTERVAL_SECONDS`):

1. The old `battery_t1` moves to `battery_t2`.
2. The old `battery_t_now` moves to `battery_t1`.
3. The latest sensor battery reading becomes the new `battery_t_now`.
4. It publishes a lightweight `battery_lag_update` to MQTT so the dashboard updates instantly.

So we always have a perfectly spaced 90-second view: `[T-2 (60s ago), T-1 (30s ago), T-Now]`

**The stability check:** `battery_stable(levels, min_percent)` returns `True` if all three readings are at or above `min_percent`. If any lag reading is `None` (not yet populated), the battery is assumed stable.

#### Tier 1: Peak Load (EDFI ≥ PEAK_THRESHOLD)

High predicted energy — the room needs maximum power.

| Battery Stable at 80%? | Battery Stable at 60%? | Result |
|:-:|:-:|--------|
| ✅ Yes | — | **Smart A** — Everything on |
| ❌ No | ✅ Yes | **Smart B** — Reduce heavy loads |
| ❌ No | ❌ No | **Smart C** — Survival mode |

#### Tier 2: Moderate Load (MODERATE_THRESHOLD ≤ EDFI < PEAK_THRESHOLD)

Moderate predicted energy — fans and lights but not AC.

| Battery Stable at 60%? | Result |
|:-:|--------|
| ✅ Yes | **Smart B** — Comfort loads on |
| ❌ No | **Smart C** — Survival mode |

#### Tier 3: Baseline Load (BASELINE_THRESHOLD ≤ EDFI < MODERATE_THRESHOLD)

Low predicted energy — only critical devices needed.

→ Always **Smart C** (critical loads only).

#### Tier 4: Very Low Load (EDFI < BASELINE_THRESHOLD)

Negligible energy demand.

→ Always **Smart C**.

### How mode decisions are published

After the rule engine decides a mode, it calls `apply_mode()` which returns relay boolean states. These are then published to `room/relays/state` via MQTT:

```python
def apply_mode(mode):
    if mode == "A":
        return (True, True, True)     # All ON
    elif mode == "B":
        return (True, True, False)    # P1+P2 ON, P3 OFF
    else:  # "C"
        return (True, False, False)   # P1 ON only
```

The returned booleans are packaged into a JSON payload and published to `room/relays/state`. The ESP32 microcontroller subscribes to this topic and drives its GPIO pins accordingly:
- `relay_1: true` → ESP32 sets relay 1 pin HIGH → device ON
- `relay_1: false` → ESP32 sets relay 1 pin LOW → device OFF

### Complete decision flowchart

```
START EVALUATION
    │
    ├── Battery lag maintained by 30s background thread
    │
    ╔══ EDFI THRESHOLD CHECK ══════════════════════╗
    ║  EDFI = predicted_energy_wh from ML model    ║
    ╚═══════╤═══════════╤═══════════╤══════════════╝
            │           │           │
    ≥ PEAK  │  ≥ MODERATE │  ≥ BASELINE │  < BASELINE
    (≥30Wh) │  (15–29Wh)  │  (1–14Wh)   │  (<1Wh)
            ▼           ▼           ▼        ▼
     ┌──────────┐  ┌──────────┐  ┌─────┐  ┌─────┐
     │ bat≥80%  │  │ bat≥60%  │  │     │  │     │
     │ stable?  │  │ stable?  │  │ C   │  │ C   │
     │ Y→ A     │  │ Y→ B     │  │     │  │     │
     │ N→check↓ │  │ N→ C     │  └─────┘  └─────┘
     ├──────────┤  └──────────┘
     │ bat≥60%  │
     │ stable?  │
     │ Y→ B     │
     │ N→ C     │
     └──────────┘
```

---

## 9. The Django API

**Files:** `room_backend/energy/models.py`, `serializers.py`, `views.py`, `urls.py`

### What the API does

The Django API serves **historical data** to the frontend. While the dashboard gets live data from MQTT, the frontend team also needs historical data for graphs and analysis. That is what the API provides.

### The Endpoints

| URL | What it returns |
|-----|----------------|
| `GET /api/v1/sensors/` | Paginated list of ALL 5-minute sensor readings (newest first, 50 per page) |
| `GET /api/v1/sensors/latest/` | Just the single most recent sensor reading |
| `GET /api/v1/predictions/` | Paginated list of ML predictions |
| `GET /api/v1/predictions/latest/` | Just the most recent prediction |
| `GET /api/v1/relays/` | Paginated list of ALL relay decisions (audit trail) |
| `GET /api/v1/relays/current/` | Just the current relay mode |

### How Django talks to the database

Django uses an **ORM** (Object-Relational Mapper). This means we define Python classes (models), and Django automatically translates them into SQL queries.

For example, this Python model:

```python
class SensorLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    temperature = models.FloatField()
    battery_level = models.FloatField()
```

Automatically creates this SQL table:

```sql
CREATE TABLE energy_sensorlog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    temperature REAL NOT NULL,
    battery_level REAL NOT NULL
);
```

And this Python view:

```python
class SensorLogListView(generics.ListAPIView):
    queryset = SensorLog.objects.all()
    serializer_class = SensorLogSerializer
```

Automatically generates a paginated JSON API endpoint that runs:

```sql
SELECT * FROM energy_sensorlog ORDER BY timestamp DESC LIMIT 50;
```

### What a response looks like

```json
{
    "count": 150,
    "next": "http://localhost:8000/api/v1/sensors/?page=2",
    "previous": null,
    "results": [
        {
            "id": 150,
            "timestamp": "2026-03-04T12:30:00Z",
            "temperature": 27.3,
            "humidity": 61.5,
            "occupancy": 1,
            "voltage": 220.2,
            "current": 4.5,
            "battery_level": 74.2
        },
        ...
    ]
}
```

The `count` tells you how many total records exist. The `next` and `previous` fields give you URLs to navigate between pages.

---

## 10. The Dashboard

**File:** `dashboard/index.html`

### How it works

The dashboard is a **single HTML file** that includes:
- **Tailwind CSS** (loaded from CDN) for styling
- **FontAwesome** (loaded from CDN) for icons
- **Paho MQTT JavaScript library** (loaded from CDN) for MQTT communication

### MQTT in the browser

Browsers cannot use raw MQTT (TCP protocol). They use **WebSockets** instead. WebSockets are a browser-friendly protocol that keeps a connection open for two-way communication.

The dashboard connects to: `ws://127.0.0.1:9001`

This is why Mosquitto has two listeners:
- Port 1883 = raw MQTT for Python scripts
- Port 9001 = WebSocket for the browser

### How updates happen

1. The Paho JS client connects to the MQTT broker.
2. It subscribes to `room/sensors` and `room/relays/state`.
3. When a message arrives, the `onMessageArrived` callback fires.
4. The callback parses the JSON payload.
5. It updates the DOM elements (text content, CSS classes, progress bar widths) using `document.getElementById()`.

For example, when a sensor message arrives with `temperature: 30.5`:
- The temperature number updates to "30.5"
- The text color changes to red (because 30.5 > 28)
- The temperature bar grows to reflect the value
- The card flashes briefly to show it updated

### The visual cards

| Card | What it shows | Dynamic behavior |
|------|--------------|-----------------|
| Room Status | Temperature + Occupancy | Temp turns red if > 28°C. Occupancy shows green dot + "Room Occupied" or grey dot + "Room Empty" |
| Battery Status | Battery % + progress bar | Bar is green (>50%), yellow (20-50%), or red (<20%) |
| Active Load Level | Current mode (A/B/C) | Green badge for Mode A, amber for Mode B, red for Mode C. Relay indicator dots show P1/P2/P3 states |
| Activity Log | Scrolling list of events | Shows every incoming MQTT message with timestamp |

---

## 11. The Data Simulator

**File:** `simulation/data_simulator.py`

### What it does

It reads from `abs_smart_grid_dataset_20k.csv` row by row and publishes each row as a sensor payload to `room/sensors` via MQTT. The simulator is **prediction-paced** — after publishing a row, it waits for the ML service to publish a prediction on `room/ml/predictions` before advancing to the next row. This ensures the simulation stays synchronised with the model's internal CSV pointer.

### How it works

1. **On startup**, the simulator always resets to Row 1 of the CSV and calls the ML API's `POST /reset` endpoint so the model's CSV pointer is synchronised.
2. It reads each row's `Temperature_C`, `Humidity_%`, `Luminous_Intensity_Lux` (or `Luminous_Intensity`), `Occupancy`, and `Energy_kW` directly from the CSV.
3. Battery is simulated via a configurable drain mode (see below).
4. Each row is published as a JSON payload to `room/sensors` with the fields: `timestamp`, `temperature_c`, `temperature`, `humidity`, `lux`, `occupancy`, `energy_kw`, and `battery_level`.
5. After publishing, the simulator waits for a prediction to arrive on `room/ml/predictions` (with a configurable timeout, default 30 seconds).
6. Once the prediction arrives (or the timeout expires), it enforces a minimum delay (`MIN_ROW_DELAY`, default 3 seconds) before moving to the next row.

### Battery drain modes

The simulator supports two modes, selected via the `BATTERY_DRAIN_MODE` environment variable:

**`consistent` (default)** — Deterministic linear drain. Battery starts at `BATTERY_START` (default 85%), drops exactly 0.1% per row, and floors at `BATTERY_FLOOR` (default 20%). This produces a smooth, predictable curve for baseline testing.

**`inconsistent`** — Randomised fluctuations designed to stress-test the rule engine's 3-time battery lag:

| Probability | What happens | Purpose |
|-------------|-------------|----------|
| 70% | Normal drain: −0.0% to −0.8% | Gentle, variable depletion |
| 15% | Sharp drop: −2.0% to −5.0% | Triggers lag instability (≥2% drop → mode change) |
| 10% | Flat: 0.0% change | Tests the stability lock (battery barely changing) |
| 5% | Small recovery: +0.5% to +1.5% | Simulates charging or regenerative events |

### Why prediction-paced and not timer-based?

In earlier versions, the simulator published every 5 seconds on a fixed timer. This was changed because the ML model needs time to run inference (GRU + LightGBM + Bayesian uncertainty). If the simulator advances faster than the model can keep up, the internal CSV pointers drift apart and predictions no longer align with the published sensor data. By waiting for each prediction before advancing, we guarantee 1:1 alignment between sensor rows and predictions.

### Key environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ML_API_BASE` | `http://127.0.0.1:5000` | URL of the ML service (for the `/reset` call) |
| `PREDICTION_TIMEOUT` | `30` | Max seconds to wait for a prediction per row |
| `MIN_ROW_DELAY` | `3` | Minimum seconds between rows (keeps output readable) |
| `BATTERY_DRAIN_MODE` | `consistent` | `consistent` (linear) or `inconsistent` (randomised) |
| `BATTERY_START` | `85.0` | Initial battery percentage at simulation start |
| `BATTERY_FLOOR` | `20.0` | Minimum battery percentage (drain stops here) |

---

## 12. Systemd Services

### What is systemd?

Systemd is the service manager built into Linux. It can start, stop, restart, and monitor background services automatically.

### Why use systemd?

On the final deployed Raspberry Pi, you want:
- Services to **start automatically when the Pi boots** (no manual terminal commands).
- Services to **restart automatically if they crash**.
- Easy commands to check status, view logs, start/stop services.

### startall.sh — The Unified Launcher

**File:** `startall.sh` (Linux/Mac) / `startall.ps1` (Windows)

In production, all services are launched by a single script: `startall.sh`. This script:

1. Loads all environment variables from `.env`
2. Stops any existing Mosquitto and starts a fresh instance with our config
3. Starts Django API (`manage.py runserver`)
4. Starts MQTT Logger (`workers/mqtt_logger.py`)
5. Starts Rule Engine (`workers/rule_engine.py`)
6. Starts the ML service (**dynamically** — reads `ML_SERVICE_SCRIPT` from `.env`)
7. Starts the data source (**dynamically** — reads `DATA_SOURCE` from `.env`):
   - `hardware` → `workers/hw_bridge.py` (production)
   - `simulator` → `simulation/data_simulator.py` (testing)
   - `both` → both (debugging only)

The script traps `SIGINT` (Ctrl+C) to cleanly shut down all background processes.

### systemd — Running as a System Service

For fully automated boot-up, the system uses a single systemd service (`smartroom.service`) that calls `startall.sh`:

```ini
[Unit]
Description=Smart Room Energy Management System (all services)
After=network.target

[Service]
Type=simple
User=grandmaster
WorkingDirectory=/home/grandmaster/Documents/project/PROJECT_CODE_4
ExecStart=/bin/bash startall.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Useful commands

```bash
sudo systemctl start smartroom         # Start all services
sudo systemctl stop smartroom          # Stop all services
sudo systemctl restart smartroom       # Restart everything
sudo systemctl status smartroom        # Check status
sudo journalctl -u smartroom -f        # View live logs (all services interleaved)
```

---

## 13. Security and Concurrency Design

### Concurrency (multiple processes accessing the database)

Three processes access the same SQLite database:
1. `mqtt_logger.py` — writes sensor averages every 5 minutes
2. `rule_engine.py` — writes relay decisions every 5 minutes
3. Django — reads data when API requests come in

**Without WAL mode:** Only one process can access the database at a time. If the logger is writing and Django tries to read, Django gets a "database is locked" error.

**With WAL mode:** Readers never block writers, and writers never block readers. All three processes work simultaneously without errors.

**busy_timeout:** Even in WAL mode, two writers cannot write at the exact same moment. The `busy_timeout=5000` setting tells SQLite to wait up to 5 seconds for the other writer to finish, instead of immediately failing. Since our writes happen every 5 minutes and take milliseconds, the chance of a collision is nearly zero.

### Thread safety inside workers

Both `mqtt_logger.py` and `rule_engine.py` use `threading.Lock()` to protect shared data:
- The MQTT client runs on its own internal thread (the network loop).
- The flush/evaluation timer runs on another thread.
- Both threads access shared variables (buffers, latest sensor data).
- The lock prevents both threads from reading/writing the same variable at the same time.

### Network security

- **Mosquitto allows anonymous connections** — this is acceptable because the system runs on a private local network (university LAN). In a production Internet-facing deployment, you would add username/password authentication.
- **Django has ALLOWED_HOSTS = ['*']** — this is acceptable for a LAN deployment. For Internet-facing, you would restrict this to specific IP addresses.

---

## 14. How to Defend Each Design Decision

### "Why a Raspberry Pi and not a cloud server?"

The Pi acts as an **edge server**. Edge computing means processing data close to where it is generated (the hostel room), instead of sending it to a remote cloud server. Benefits:
- **Low latency:** Relay decisions happen in milliseconds, not seconds (no internet round-trip).
- **Works offline:** If the internet goes down, the system keeps running.
- **Privacy:** Sensor data stays on-premises.
- **Cost:** No monthly cloud hosting fees.

### "Why MQTT and not HTTP?"

HTTP is a request-response protocol. The server cannot push data to clients; clients must poll (repeatedly ask). MQTT is publish-subscribe with persistent connections — data is pushed instantly to all subscribers. For IoT sensor data that updates every few seconds, MQTT is far more efficient.

### "Why SQLite and not MySQL/PostgreSQL?"

SQLite requires zero configuration and zero memory for a separate server process. For our workload (one write every 5 minutes, occasional reads), SQLite is more than enough. MySQL or PostgreSQL would waste RAM on the Pi for no benefit.

### "Why Django and not Flask?"

Django comes with an ORM, admin panel, migration system, and DRF provides automatic pagination/serialization. Flask would require us to manually set up all of these. For a university project with tight deadlines, Django saves significant development time.

### "Why 5-minute intervals?"

Five minutes is a standard interval in energy monitoring systems. It is frequent enough to show meaningful trends in hourly/daily charts, but infrequent enough to avoid overwhelming the SQLite database or the Pi's SD card with writes.

### "Why buffer and average, not write every reading?"

If sensors publish every 5 seconds, that is 12 readings per minute, 720 per hour, 17,280 per day. Storing every single reading would fill SD card storage quickly and make API queries slow. By averaging every 5 minutes, we store just 288 rows per day — a 60x reduction — while keeping the important trends.

### "Why a rule engine and not ML for relay control?"

The ML team provides predictions, but the **actual relay control uses deterministic rules** (if/then logic). This is intentional:
- Rules are predictable and explainable (you can always say "the system did X because of Y").
- Rules are auditable (every decision is logged with a reason).
- Rules are fast (no model inference latency).
- The ML predictions are used as an **input** to the rules, not as the decision-maker itself. This is a common pattern called "ML-informed rule-based control."

### "What happens if the Raspberry Pi loses energy?"

- GPIO pins default to LOW when the Pi shuts down → all relays turn OFF → all connected devices turn OFF.
- When the Pi boots back up, systemd automatically starts all services.
- The rule engine defaults to Mode C (safest mode) on startup.
- The database file is safe because WAL mode handles crash recovery automatically.

### "What happens if the MQTT broker goes down?"

- The logger and rule engine both have `reconnect_delay_set(min_delay=1, max_delay=30)` — they will keep trying to reconnect, backing off from 1 second to 30 seconds between attempts.
- During disconnection, the rule engine keeps the last known mode active (relays stay in their current position).
- When reconnected, data flow resumes normally.

---

## Summary

This system is a complete **IoT edge computing** solution that:

1. **Collects** real-time data from sensors via MQTT (hardware bridge translates Group 1 hardware → `room/sensors`)
2. **Predicts** energy consumption using a LightGBM + XGBoost hybrid ML model (called via HTTP by the rule engine)
3. **Stores** 5-minute averaged historical data in SQLite
4. **Decides** which electrical devices to energize using EDFI threshold-based rules with battery stability checks
5. **Controls** physical relays via MQTT → ESP32 (no direct GPIO on the Pi)
6. **Serves** historical data via a Django REST API
7. **Displays** live data in a browser dashboard via MQTT WebSockets

Everything runs on a single Raspberry Pi (4 or 5) with no cloud dependency, no external database server, and no complex infrastructure. The ML model is plug-and-play — swappable via two lines in `.env`. The entire system is designed to be simple, reliable, and explainable.
