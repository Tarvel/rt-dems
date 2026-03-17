# Real-Time Energy Management System (RT-DEMS)

A comprehensive energy management dashboard built with Flutter, featuring real-time telemetry, AI-driven forecasting, and dynamic energy flow visualization.

## Features
- **Real-time Dashboard**: Live monitoring of power, battery, and environmental metrics.
- **Energy Flow Graphic**: Dynamic visualization of power distribution with integrated battery SoC.
- **AI Analytics**: Predictive load modeling and system mode reasoning.
- **Raw Data View**: Direct access to telemetry logs and historical data.

## Prerequisites
Before running the project, ensure you have the following installed:
- [Flutter SDK](https://docs.flutter.dev/get-started/install) (Stable channel)
- [Python 3.10+](https://www.python.org/downloads/)
- [Mosquitto MQTT Broker](https://mosquitto.org/download/)
- [Dart SDK](https://dart.dev/get-dart)

## Getting Started

### 1. Backend Setup (Mosquitto & API)
Navigate to the backend directory and start the services:
```bash
# Start Mosquitto (Windows example)
cd lib/rt-dems
mosquitto -c systemd/mosquitto.conf -v

# Initialize Python Virtual Environment
# (Run this once)
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt

# Start Django API Server
cd room_backend
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

### 2. Workers & ML Services
Start the following in separate terminals (activate venv in each):
```bash
# MQTT Logger (Averages data for DB)
python lib/rt-dems/workers/mqtt_logger.py

# Rule Engine (ML-driven relay control)
python lib/rt-dems/workers/rule_engine.py

# ML Inference Service (FastAPI)
cd lib/rt-dems/ML
python app.py

# Data Simulator (For testing)
python lib/rt-dems/simulation/data_simulator.py
```

### 3. Frontend Setup (Flutter)
Run the following from the project root:
```bash
flutter run -d chrome  # For Web
# or
flutter run           # For Desktop/Mobile
```

## Project Structure
- `lib/main.dart`: Core dashboard UI and MQTT/API integration.
- `lib/rt-dems/`: Backend microservices.
- `lib/rt-dems/ML/`: ML model and inference wrapper.
- `lib/rt-dems/room_backend/`: Django API server.
- `lib/rt-dems/workers/`: Rule engine and database logger.

## License
MIT License - Developed for Advanced Energy Management Systems.
