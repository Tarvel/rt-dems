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

On Raspberry Pi only (Pi 5 requires `lgpio`, Pi 4 can use either):

```bash
# Pi 5 — lgpio is the ONLY GPIO library that works with the RP1 chip
sudo apt install -y python3-lgpio

# Pi 4 — RPi.GPIO works but lgpio is also fine
# sudo apt install -y python3-rpi.gpio   # optional, gpiozero will use lgpio
```

`gpiozero` (installed via `requirements.txt`) auto-detects the correct backend:
- **Pi 5** → uses `lgpio` (mandatory — RPi.GPIO is NOT compatible with Pi 5)
- **Pi 4** → uses `RPi.GPIO` if available, otherwise `lgpio`
- **Dev machine** → uses `MockFactory` (virtual pins, no hardware)

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

## 6. GPIO Pin Testing (No Breadboard Required)

**File:** `simulation/test_gpio_pins.py`

This interactive tool lets you test the relay GPIO pins without needing a breadboard, LEDs, or any external components. On a Raspberry Pi, it drives the **real GPIO pins**. On a dev machine, it uses virtual (mock) pins for logic testing.

### 6.1 How it works

The tool creates `gpiozero.LED` objects on the same BCM pins the rule engine uses (17, 27, 22). When you type a mode command, it sets those pins to HIGH or LOW exactly as the rule engine would during a real mode switch. On the Pi, this physically changes the voltage on the header pins — you can measure it with a multimeter or verify it with `pinctrl`.

### 6.2 Running the tester

```bash
cd /home/tai/Downloads/PEOJECT\ RESEARCH\ REFERENCES/PROJECT_CODE
source venv/bin/activate
python simulation/test_gpio_pins.py
```

On startup, the tool will tell you whether it detected real hardware:

```
  Hardware: REAL GPIO (Pi detected)     ← on a Raspberry Pi
  Hardware: MOCK (virtual pins)         ← on a dev machine
```

### 6.3 Interactive commands

| Command | What it does |
|---------|-------------|
| `A` | Apply Mode A — sets all 3 pins HIGH (all relays ON) |
| `B` | Apply Mode B — pins 17 + 27 HIGH, pin 22 LOW |
| `C` | Apply Mode C — only pin 17 HIGH (baseline) |
| `1` / `2` / `3` | Toggle that relay pin ON↔OFF |
| `on <pin>` | Force a specific pin ON (e.g. `on 17` or `on 1`) |
| `off <pin>` | Force a specific pin OFF (e.g. `off 27` or `off 2`) |
| `blink <pin>` | Blink a pin ON/OFF 3 times (e.g. `blink 22` or `blink 3`) |
| `status` | Print current state table for all 3 pins |
| `verify` | **(Pi only)** Runs `pinctrl get` to show actual hardware voltage |
| `help` | Show the full command menu |
| `q` | Clean up all pins and exit |

You can use either relay numbers (`1`, `2`, `3`) or BCM pin numbers (`17`, `27`, `22`) interchangeably for all pin commands.

### 6.4 Recommended test walkthrough

Here is a step-by-step sequence to verify all modes and pins work correctly:

```
> A
  ⚡ Mode A — Peak Demand
     Relay 1 (Pin 17): ON
     Relay 2 (Pin 27): ON
     Relay 3 (Pin 22): ON

> verify          ← (Pi only) confirms all 3 header pins are HIGH

> C
  ⚡ Mode C — Baseline Load
     Relay 1 (Pin 17): ON
     Relay 2 (Pin 27): OFF
     Relay 3 (Pin 22): OFF

> verify          ← confirms only pin 17 is HIGH, 27 and 22 are LOW

> B
  ⚡ Mode B — Average Load
     Relay 1 (Pin 17): ON
     Relay 2 (Pin 27): ON
     Relay 3 (Pin 22): OFF

> 3               ← toggles relay 3 (pin 22) ON
> 3               ← toggles it back OFF

> blink 2         ← blinks relay 2 (pin 27) 3 times

> q               ← cleans up all pins and exits
```

### 6.5 Verifying from a separate terminal (Pi only)

While the tester is running, you can confirm the actual hardware pin states from another terminal session:

```bash
# Check individual pin states
pinctrl get 17    # Should show "op dh" (output, driven high) when ON
pinctrl get 27    # Should show "op dl" (output, driven low) when OFF
pinctrl get 22

# Or use a multimeter:
# - Place the negative probe on a GND pin (e.g. pin 6, 9, 14, 20, 25)
# - Place the positive probe on the BCM header pin
# - A HIGH pin reads ~3.3V, a LOW pin reads ~0V
```

### 6.6 Pin header reference

| Relay | BCM Pin | Physical Header Pin | Priority |
|-------|---------|--------------------|-----------|
| Relay 1 | GPIO 17 | Pin 11 | Critical (always ON in all modes) |
| Relay 2 | GPIO 27 | Pin 13 | Medium (ON in Mode A and B) |
| Relay 3 | GPIO 22 | Pin 15 | Luxury (ON in Mode A only) |

## 7. What to Expect When Running

1. Simulator publishes sensor payloads to `room/sensors` every 5 seconds.
2. ML service receives sensor data via MQTT, runs the GRU + LightGBM model, and publishes predictions to `room/ml/predictions`.
3. Logger buffers sensor and prediction data and writes 5-minute averages to SQLite.
4. Rule engine evaluates every configured interval and publishes mode decisions to `room/relays/state`.
5. Battery lag tracker (inside the rule engine) shifts T-now/T-1/T-2 every 30 seconds and publishes lightweight `battery_lag_update` payloads to the dashboard.
6. Django API serves historical data at `/api/v1/*`.
7. Dashboard shows live data from MQTT in the browser.

## 8. Configuration

### 7.1 Environment variables (`.env`)

Copy the provided example file to configure all services:

```bash
cp example.env .env
```

Key variables for the rule engine:

```bash
DECISION_INTERVAL_MINUTES=3
BATTERY_LAG_INTERVAL_SECONDS=30
MAX_BATTERY_DROP_PERCENT=2
MODE_A_MAX_KWH=2.4
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

6. GPIO not working on Pi 5
   - **Symptom:** `RuntimeError: Cannot determine SOC peripheral base address` or `ModuleNotFoundError: No module named 'RPi'`.
   - **Cause:** RPi.GPIO does not support the Raspberry Pi 5's RP1 GPIO chip.
   - **Fix:** Install `lgpio` (the only GPIO library that works on Pi 5):
     ```bash
     sudo apt install -y python3-lgpio
     ```
   - `gpiozero` (used by the rule engine and test script) will automatically use `lgpio` as its backend on Pi 5. No code changes are needed.

7. GPIO permission denied
   - On the Pi, GPIO access may require root. Run the rule engine with `sudo` or add your user to the `gpio` group:
     ```bash
     sudo usermod -aG gpio $USER
     # Log out and back in for the change to take effect
     ```

8. `pinctrl` command not found
   - Install it:
     ```bash
     sudo apt install -y raspi-utils
     ```

## 12. Quick Checklist

```text
 1. Install lgpio on Pi 5:    sudo apt install python3-lgpio
 2. Start Mosquitto
 3. Start Django
 4. Start mqtt_logger
 5. Start rule_engine
 6. Start ML service (ML/test_prediction_api.py)
 7. Start simulator (or hardware publisher) and keep it running
 8. Open dashboard (dashboard/index.html) in browser
 9. Test GPIO pins:           python simulation/test_gpio_pins.py
10. Verify live MQTT traffic: mosquitto_sub -t "room/#" -v
11. Verify dashboard/API responses
```

Shutdown order:

1. Stop simulator/publishers
2. Stop ML service
3. Stop workers
4. Stop Django
5. Stop Mosquitto
