# Smart Room Energy Management System — Backend

Edge-server backend running on a Raspberry Pi 4 for a university hostel energy management project.

## Architecture

```
Hardware Sensors ──► MQTT (Mosquitto) ──► mqtt_logger.py ──► SQLite (WAL)
ML Predictions  ──► MQTT (Mosquitto) ──► rule_engine.py ──► GPIO Relays
                                                └──► SQLite (audit log)
Django REST API ◄── SQLite ──► Frontend (GET endpoints)
Frontend        ◄── MQTT ──── Live relay state & averaged data
```

## Quick Start (on Raspberry Pi)

```bash
# 1. Clone and enter the project directory
cd PROJECT_CODE

# 2. Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
# On the Pi, also: pip install RPi.GPIO

# 4. Run Django migrations
cd room_backend
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser  # optional

# 5. Start Django dev server (for API)
python manage.py runserver 0.0.0.0:8000

# 6. In separate terminals, start the workers:
cd ../workers
python mqtt_logger.py
python rule_engine.py
```

## Deploy as systemd Services

```bash
# Copy service files
sudo cp systemd/mqtt-logger.service /etc/systemd/system/
sudo cp systemd/rule-engine.service /etc/systemd/system/

# Copy Mosquitto config
sudo cp systemd/mosquitto.conf /etc/mosquitto/conf.d/room.conf

# Reload and start
sudo systemctl daemon-reload
sudo systemctl enable --now mosquitto mqtt-logger rule-engine
```

## Verify SQLite WAL Mode

```bash
sqlite3 room_backend/db.sqlite3 "PRAGMA journal_mode;"
# Expected output: wal
```

## API Endpoints

See [integration_contract.md](integration_contract.md) for full details.

| Endpoint                       | Description                  |
|--------------------------------|------------------------------|
| `GET /api/v1/sensors/`         | Paginated sensor logs        |
| `GET /api/v1/sensors/latest/`  | Latest sensor reading        |
| `GET /api/v1/predictions/`     | ML prediction history        |
| `GET /api/v1/relays/current/`  | Current relay state          |

## GPIO Pin Mapping (BCM)

| Relay     | GPIO Pin | Priority |
|-----------|----------|----------|
| Relay 1   | 17       | P1       |
| Relay 2   | 27       | P2       |
| Relay 3   | 22       | P3       |

Configurable via environment variables: `RELAY_PIN_1`, `RELAY_PIN_2`, `RELAY_PIN_3`.
