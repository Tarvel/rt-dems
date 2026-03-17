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
| **Hardware (Group A)**   | Physical sensors (temperature, humidity, motion, voltage, current, battery)        | Sensor data published to MQTT                 | Nothing — they just publish          |
| **ML Team<br>(Group B)** | Machine learning model that predicts energy usage                                  | Predictions published to MQTT                 | Nothing — they just publish          |
| **Us<br>(Group C)**      | The Raspberry Pi server, database, MQTT broker, API endpoints, relay control logic | REST API endpoints, MQTT topics for live data | We receive sensor + ML data via MQTT |

### What the Raspberry Pi does (our responsibility)

The Raspberry Pi 4 acts as three things at once:

1. **MQTT Broker** — It runs Mosquitto, which is the message post office. Every team sends and receives messages through it.
2. **Database Server** — It stores the 5-minute averaged historical data in SQLite.
3. **Control Hub** — It runs the rule engine that makes decisions about which relays to turn ON or OFF.

---

## 2. The Hardware Layer

### The Raspberry Pi 4

- **Model:** Raspberry Pi 4 with 4GB RAM
- **OS:** Raspberry Pi OS (Linux-based)
- **Network:** Connected to the local network via Wi-Fi or Ethernet
- **GPIO Pins:** 40 pins on the board that can send electrical signals to control external devices

### The 3 Relays

A relay is like a remote-controlled switch. It uses a small electrical signal from the Raspberry Pi to turn a much larger electrical circuit ON or OFF. Think of it like using a TV remote (small signal) to turn on a TV (big device).

We have 3 relays, each connected to a different GPIO pin on the Pi:

| Relay | GPIO Pin (BCM) | Priority Level | What it controls |
|-------|---------------|----------------|-----------------|
| Relay 1 | Pin 17 | Priority 1 (Critical) | Essential loads — lights, emergency systems |
| Relay 2 | Pin 27 | Priority 2 (Medium) | Comfort loads — fans, ventilation |
| Relay 3 | Pin 22 | Priority 3 (Luxury) | Heavy loads — AC, heaters, high-energy appliances |

### How relays work with GPIO

- The Pi sends a **HIGH signal** (3.3V) to a GPIO pin → the relay closes the circuit → the connected device turns **ON**
- The Pi sends a **LOW signal** (0V) to a GPIO pin → the relay opens the circuit → the connected device turns **OFF**
- We use **BCM numbering** (Broadcom pin numbering), which is the standard way to refer to GPIO pins in Python

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

**What it does:** Every 5 minutes, it looks at the latest sensor data and ML predictions, runs them through a set of rules (the 3-phase decision hierarchy), decides which mode (A, B, or C) to use, and physically switches the GPIO relays.

**Why it exists:** This is the core intelligence of the system. Without it, the sensors just collect data but nothing happens. The rule engine is what turns data into action.

### 3.6 data_simulator.py (Testing Tool)

**What it is:** A Python script that generates fake sensor and ML data.

**What it does:** It publishes realistic-looking sensor readings and ML predictions to MQTT every 5 seconds. This lets us test the entire pipeline without needing the actual hardware sensors or the ML model.

**Why it exists:** During development and testing, you do not have the hardware team's sensors or the ML team's model running. The simulator stands in for both.

### 3.7 dashboard/index.html (Browser Dashboard)

**What it is:** A single HTML file with CSS and JavaScript embedded.

**What it does:** Opens in any web browser and connects directly to the MQTT broker via WebSockets. It displays live sensor data, battery level, and the current relay mode in real time — no page refresh needed.

**Why it exists:** It gives a visual way to monitor the system. It also proves that the MQTT data flow works end-to-end.

---

## 4. The Full Data Flow

This is the most important section. Here is the complete journey of data through the system, step by step:

```
STEP 1: Sensors measure  →  STEP 2: Hardware publishes  →  STEP 3: Mosquitto delivers
                                                                       │
                           ┌───────────────────────────────────────────┤
                           │                                           │
                           ▼                                           ▼
                    STEP 4a: Logger                            STEP 4b: Rule Engine
                    receives & buffers                         receives & stores latest
                           │                                           │
                           │ (every 5 min)                             │ (every 5 min)
                           ▼                                           ▼
                    STEP 5a: Computes average               STEP 5b: Evaluates rules
                    and writes to SQLite                    and switches GPIO relays
                           │                                           │
                           ▼                                           ▼
                    STEP 6a: Republishes averaged           STEP 6b: Publishes relay
                    data to MQTT for dashboard              state to MQTT for dashboard
                           │                                           │
                           ▼                                           ▼
                    STEP 7: Django serves                   STEP 7: Dashboard shows
                    historical data via API                 live data in browser
```

### Detailed walkthrough:

**Step 1: Physical measurement.**
The hardware team's sensors physically measure the room's temperature, humidity, whether someone is present (occupancy), the mains voltage, the current draw, and the battery percentage.

**Step 2: Hardware team publishes to MQTT.**
Their microcontroller (like an ESP32 or Arduino) packages the readings into a JSON message and publishes it to the MQTT topic `room/sensors`. Similarly, the ML team publishes energy predictions to `room/ml/predictions`.

**Step 3: Mosquitto delivers.**
The Mosquitto broker on the Pi receives the message and immediately forwards it to every client that has subscribed to that topic. In our case, three subscribers get the sensor data:
- `mqtt_logger.py` (for storing)
- `rule_engine.py` (for decision making)
- `dashboard/index.html` (for live display)

**Step 4a: The Logger receives and buffers.**
When `mqtt_logger.py` receives a sensor message, it does NOT write it to the database immediately. Instead, it adds it to an in-memory list (a Python list). This list acts as a buffer.

**Step 4b: The Rule Engine receives and stores the latest.**
When `rule_engine.py` receives a sensor message, it overwrites its `latest_sensor` dictionary with the new values. It always keeps only the most recent reading.

**Step 5a: Logger computes averages (every 5 minutes).**
A background timer fires every 5 minutes. When it fires, the logger:
1. Takes all the readings collected in the buffer (could be 60+ readings if sensors publish every 5 seconds).
2. Computes the arithmetic average of each field (temperature, humidity, voltage, current, battery).
3. For occupancy, it uses majority vote — if more than half the readings say "occupied", the average is 1, otherwise 0.
4. Writes one single row to the `energy_sensorlog` table in SQLite.
5. Clears the buffer and starts collecting again.

**Step 5b: Rule Engine evaluates rules (every 5 minutes).**
A background timer fires every 5 minutes. When it fires, the rule engine:
1. Reads the latest sensor values and ML predictions.
2. Pushes the current battery reading into a rolling window of 3 readings.
3. Runs the 3-phase rule hierarchy (occupancy override → battery stability lock → temperature bias + standard flow).
4. Determines the correct mode (A, B, or C).
5. Sends HIGH or LOW signals to the 3 GPIO pins to physically switch the relays.
6. Writes the decision (mode, relay states, reason) to the `energy_relaystate` table.

**Step 6a: Logger republishes averaged data.**
After writing to the database, the logger also publishes the averaged data to the MQTT topic `room/data/averaged`. This lets the dashboard show the averaged values too.

**Step 6b: Rule Engine publishes relay state.**
After switching the relays, the rule engine publishes the current mode and relay states to `room/relays/state`. The dashboard subscribes to this topic so it can show the user which mode is active.

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

A topic is like an address. Here are all the topics in our system:

| Topic | Who publishes | Who subscribes | What data | How often |
|-------|--------------|----------------|-----------|-----------|
| `room/sensors` | Hardware team | Logger, Rule Engine, Dashboard | Temperature, humidity, occupancy, voltage, current, battery | Every few seconds |
| `room/ml/predictions` | ML team | Logger, Rule Engine | Predicted energy range, peak demand | When model runs |
| `room/data/averaged` | Logger | Dashboard | 5-minute averaged sensor data | Every 5 minutes |
| `room/relays/state` | Rule Engine | Dashboard | Current mode (A/B/C), relay states, reason | Every 5 minutes |

### QoS (Quality of Service)

We use **QoS 1** for all messages. This means:
- **QoS 0:** Fire and forget. Message might be lost. We do not use this.
- **QoS 1:** At least once delivery. The broker guarantees the message is delivered at least once. If the network hiccups, it will retry. This is what we use.
- **QoS 2:** Exactly once delivery. More overhead, slower. Not needed for sensor data.

### The JSON Payloads

Every MQTT message in our system carries a JSON payload. JSON is a text format that looks like a Python dictionary:

**Sensor payload** (published by hardware team to `room/sensors`):
```json
{
    "temperature": 27.5,
    "humidity": 62.3,
    "occupancy": 1,
    "voltage": 220.1,
    "current": 4.8,
    "battery_level": 73.5
}
```

**ML payload** (published by ML team to `room/ml/predictions`):
```json
{
    "predicted_energy_range": 2800.0,
    "peak_demand": 2500.0
}
```

**Relay state payload** (published by rule engine to `room/relays/state`):
```json
{
    "mode": "B",
    "relay_1": true,
    "relay_2": true,
    "relay_3": false,
    "reason": "Phase 3 — Temperature Bias: 29.5°C > 28°C and battery 73.5% > 40% → Mode B",
    "timestamp": "2026-03-04T12:30:00+00:00"
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
3. **When a sensor message arrives:** It parses the JSON, validates that all required fields are present, and adds the data to an in-memory buffer (a Python list called `sensor_buffer`).
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
        "voltage":     sum(r["voltage"]     for r in readings) / n,
        "current":     sum(r["current"]     for r in readings) / n,
        "battery_level": sum(r["battery_level"] for r in readings) / n,
    }
    return avg
```

For occupancy, since it is 0 or 1, we use a majority vote: if the average is 0.5 or higher (meaning the room was occupied more than half the time), we record it as 1 (occupied).

### Why direct sqlite3 and not Django ORM?

The logger is an independent Python script, not part of the Django web server. Loading the full Django framework just to insert one row every 5 minutes would waste memory on the Pi. Instead, we use Python's built-in `sqlite3` module to write directly to the same database file that Django reads from. This works because SQLite WAL mode allows concurrent access.

---

## 8. The Rule Engine — The Brain of the System

**File:** `workers/rule_engine.py`

This is the most complex and important part of the system. It is the component that makes real decisions and controls physical hardware.

### What it does step by step:

1. **Starts up**, initializes GPIO pins (sets them as outputs), and defaults to **Mode C** (the safest mode — only critical devices on).
2. **Connects to MQTT** and subscribes to `room/sensors` and `room/ml/predictions`.
3. **When messages arrive**, it updates its internal state variables (`latest_sensor`, `latest_ml`).
4. **Every 5 minutes**, it runs the full rule evaluation.
5. **After evaluation**, it switches the GPIO pins to match the chosen mode and logs the decision to the database.
6. **On shutdown**, it forces Mode C (for safety) and cleans up GPIO before exiting.

### The MockGPIO System

**Problem:** When you develop on a laptop, there is no RPi.GPIO library and no GPIO pins. The code would crash.

**Solution:** At the top of the file, we try to import `RPi.GPIO`. If it fails (because we are on a laptop), we create a `MockGPIO` class that has the same methods (`setmode`, `setup`, `output`, `cleanup`) but just prints messages to the console instead of controlling real pins.

```python
try:
    import RPi.GPIO as GPIO
    ON_PI = True
except (ImportError, RuntimeError):
    class MockGPIO:
        # ... fake versions of all GPIO methods that just print
    GPIO = MockGPIO
    ON_PI = False
```

This means the same code runs on both a Raspberry Pi (controlling real relays) and a laptop (just printing what it would do).

### The 3-Phase Rule Hierarchy — Complete Explanation

The rules are evaluated in strict order. Once a rule triggers, the evaluation stops. This is like a priority system — higher-priority rules override lower-priority ones.

#### Phase 1: Master Override — Occupancy Check

**The question:** Is the room empty?

**How we track it:** We keep a counter called `occupancy_zero_streak`. Every time we evaluate (every 5 minutes), if occupancy is 0 (empty), we add 1 to the counter. If occupancy is 1 (someone is there), we reset the counter to 0.

**The rule:** If the counter is 1 or more (meaning the room has been empty for at least one 5-minute cycle), immediately force **Mode C**.

**Why:** There is no point running fans or AC in an empty room. This is the highest priority rule because it saves the most energy.

```
If room is empty for ≥ 5 minutes → Mode C (done, skip everything else)
```

#### Phase 2: Battery Stability Lock — The "3-Time Lag" Check

**The question:** Is the battery stable or is it rapidly draining?

**The sliding window:** We maintain a rolling list of the last 3 battery readings taken at 5-minute intervals. This is stored in a `deque` (double-ended queue) with a maximum length of 3:

```python
battery_window = deque(maxlen=3)  # e.g., [85.0, 83.5, 82.0]
```

Every 5 minutes, we push the current battery reading into this window. Python's `deque(maxlen=3)` automatically drops the oldest reading when a 4th is added.

So the window always looks like: `[T-10min, T-5min, T-Now]`

**The drop calculation:**
```python
drop = battery_window[0] - battery_window[-1]   # oldest minus newest
```

Example: If the window is `[85.0, 83.5, 82.0]`, then `drop = 85.0 - 82.0 = 3.0%`.

**The rule:** If the drop is **≤ 2%** (battery is stable — barely draining), we keep the current mode and skip all the rules below. The system does not switch modes unnecessarily.

**Why:** If the battery is barely changing, there is no emergency. Switching modes too often is bad — it would make lights flicker and devices restart constantly. This "stability lock" prevents unnecessary mode switching.

```
If battery is stable (≤2% drop over 10 minutes) → Keep current mode (done, skip Phase 3)
If battery is draining fast (>2% drop) → Continue to Phase 3
```

**Special case:** If we have fewer than 3 readings yet (system just started), we skip this phase and go directly to Phase 3. We assume the battery is not draining fast during startup.

#### Phase 3: Temperature Bias & Standard Flow

This phase has three sub-rules evaluated in order:

##### Step 5: Temperature Bias

**The question:** Is the room too hot?

**The rule:** If temperature is **above 28°C** AND battery is **above 40%**, force **Mode B**.

**Why:** Mode B keeps fans running (relay 2 is ON). If the room is hot, we want fans to work even if we might otherwise go to Mode C. But only if the battery can handle it (above 40%).

```
If temp > 28°C AND battery > 40% → Mode B (done)
```

##### Step 6: Energy ≥ Peak Demand (we have enough energy)

**The question:** Is the predicted energy available enough to meet peak demand?

If `predicted_energy_range >= peak_demand`, we have enough energy. Now it depends on battery level:

| Battery Level | Battery Draining? | Result |
|--------------|-------------------|--------|
| ≥ 80% | No (stable) | **Mode A** — Full energy, everything on |
| ≥ 80% | Yes (dropping fast) | **Mode B** — Have battery but it's draining, be careful |
| 50% – 79% | Any | **Mode B** — Moderate battery, run fans but not heavy loads |
| Below 50% | Any | **Mode C** — Battery too low, survival mode |

##### Step 7: Energy < Peak Demand (energy is tight)

**The question:** Is the predicted energy NOT enough for peak demand?

If `predicted_energy_range < peak_demand`, energy is tight.

| Battery Level | Result |
|--------------|--------|
| ≥ 60% | **Mode A** — We have enough battery to compensate for the energy shortfall |
| < 60% | Re-apply Step 6 logic — battery is too low to compensate, use the strict rules above |

**Why this re-routing exists:** When energy supply is low but battery is high (≥60%), we can run at full energy by drawing from the battery. But if both energy supply is low AND battery is low, we cannot take that risk, so we fall back to the conservative Step 6 rules.

### How GPIO is controlled

After the rule engine decides a mode, it calls `apply_mode()`:

```python
def apply_mode(mode):
    if mode == "A":
        set_relays(True, True, True)     # All ON
    elif mode == "B":
        set_relays(True, True, False)    # P1+P2 ON, P3 OFF
    else:  # "C"
        set_relays(True, False, False)   # P1 ON only

def set_relays(relay_1, relay_2, relay_3):
    GPIO.output(RELAY_PIN_1, GPIO.HIGH if relay_1 else GPIO.LOW)
    GPIO.output(RELAY_PIN_2, GPIO.HIGH if relay_2 else GPIO.LOW)
    GPIO.output(RELAY_PIN_3, GPIO.HIGH if relay_3 else GPIO.LOW)
```

`GPIO.HIGH` sends a 3.3V signal to the pin → relay activates → connected device turns ON.
`GPIO.LOW` sends 0V → relay deactivates → device turns OFF.

### Complete decision flowchart

```
START EVALUATION
    │
    ├── Push battery reading into sliding window [T-10m, T-5m, T-Now]
    │
    ╔══ PHASE 1: OCCUPANCY OVERRIDE ══╗
    ║ Is the room empty for ≥5 min?   ║
    ╚════════╤════════════════════════╝
         YES │                     NO
             ▼                      │
        → MODE C (done)             │
                                    ▼
    ╔══ PHASE 2: STABILITY LOCK ══════╗
    ║ Battery drop ≤2% over 3 reads? ║
    ╚════════╤════════════════════════╝
         YES │                     NO
             ▼                      │
    → KEEP CURRENT MODE (done)      │
                                    ▼
    ╔══ PHASE 3: TEMP BIAS ═══════════╗
    ║ Temp >28°C AND battery >40%?   ║
    ╚════════╤════════════════════════╝
         YES │                     NO
             ▼                      │
        → MODE B (done)             │
                                    ▼
    ╔══ ENERGY vs PEAK DEMAND ════════╗
    ║ predicted_energy ≥ peak_demand? ║
    ╚════════╤════════════╤══════════╝
         YES │            NO │
             ▼               ▼
      ┌──────────┐    Battery ≥60%?
      │ Battery  │      YES → MODE A
      │  ≥80%    │      NO  → Re-apply
      │ stable?  │            left rules
      │ Y→MODE A │
      │ N→MODE B │
      ├──────────┤
      │ 50%-79%  │
      │ → MODE B │
      ├──────────┤
      │  <50%    │
      │ → MODE C │
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

It generates fake but realistic sensor and ML data and publishes it to the MQTT broker. It stands in for the hardware sensors and ML model during development.

### How the data is generated

**Temperature:** Starts at 26°C and drifts randomly by ±0.3°C each tick (stays between 20°C and 35°C). This creates realistic smooth temperature changes.

**Battery:** Starts at 85% and slowly drains by 0.05–0.4% per tick. There is a 5% chance each tick of a "recharge event" that bumps the battery up by 5–15%. This simulates a solar panel or energy supply recharging.

**Occupancy:** Mostly 1 (occupied). There is a 15% chance of starting an "empty streak" lasting 1–4 ticks where occupancy stays at 0.

**ML Predictions:** Random values between 1500–3500 kWh against a fixed peak demand of 2500 kWh. This creates scenarios where energy is sometimes above and sometimes below peak demand, triggering different rule engine paths.

### Why publish every 5 seconds?

In the real system, sensors might publish every few seconds. Publishing every 5 seconds during testing lets you see data flowing in the logger buffer quickly. The logger still waits 5 minutes before computing averages and writing to the database (you can reduce this to 30 seconds for faster testing).

---

## 12. Systemd Services

### What is systemd?

Systemd is the service manager built into Linux. It can start, stop, restart, and monitor background services automatically.

### Why use systemd?

On the final deployed Raspberry Pi, you want:
- Services to **start automatically when the Pi boots** (no manual terminal commands).
- Services to **restart automatically if they crash**.
- Easy commands to check status, view logs, start/stop services.

### Our service files

**mqtt-logger.service:**
```ini
[Unit]
Description=Smart Room MQTT Logger
After=mosquitto.service     ← Start after Mosquitto is running
Wants=mosquitto.service     ← Prefer Mosquitto to be active

[Service]
Type=simple
User=pi                     ← Run as the "pi" user
ExecStart=/path/to/python /path/to/mqtt_logger.py
Restart=on-failure          ← If it crashes, restart it
RestartSec=5                ← Wait 5 seconds before restarting

[Install]
WantedBy=multi-user.target  ← Start when the system reaches multi-user mode (normal boot)
```

**rule-engine.service:**
Same structure, but `User=root` because GPIO access requires root privileges on the Pi.

### Useful systemd commands

```bash
sudo systemctl start mqtt-logger       # Start the service
sudo systemctl stop mqtt-logger        # Stop the service
sudo systemctl restart mqtt-logger     # Restart the service
sudo systemctl status mqtt-logger      # Check if it's running
sudo systemctl enable mqtt-logger      # Start automatically on boot
sudo journalctl -u mqtt-logger -f      # View live logs
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

1. **Collects** real-time data from sensors via MQTT
2. **Stores** 5-minute averaged historical data in SQLite
3. **Decides** which electrical devices to energize using a 3-phase rule hierarchy
4. **Controls** physical relays via GPIO pins
5. **Serves** historical data via a REST API
6. **Displays** live data in a browser dashboard

Everything runs on a single Raspberry Pi 4 with no cloud dependency, no external database server, and no complex infrastructure. The entire system is designed to be simple, reliable, and explainable.
