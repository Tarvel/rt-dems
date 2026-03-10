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

### 1. Backend Setup (Mosquitto & Python)
Navigate to the backend directory and start the services:
```bash
# Start Mosquitto (Windows example)
cd lib/rt-dems
mosquitto -c systemd/mosquitto.conf -v

# Initialize Python Virtual Environment
cd lib/rt-dems/room_backend
python -m venv venv
..\venv\Scripts\activate
pip install -r requirements.txt

# Start Daphne (Interface Server)
python -m daphne -b 127.0.0.1 -p 8000 room_backend.asgi:application
```

### 2. Workers & Simulation
Start the background workers in separate terminals:
```bash
# MQTT Logger
python lib/rt-dems/workers/mqtt_logger.py

# Rule Engine
python lib/rt-dems/workers/rule_engine.py

# Data Simulator
python lib/rt-dems/simulation/data_simulator.py
```

### 3. Frontend Setup (Flutter)
Run the following commands from the project root:
```bash
# Fetch dependencies
flutter pub get

# Run the application
flutter run
```

## Project Structure
- `lib/main.dart`: Core dashboard UI and logic.
- `lib/rt-dems/`: Backend services, simulation, and workers.
- `lib/rt-dems/room_backend/`: Django/Daphne server for data orchestration.

## License
MIT License - Developed for Advanced Energy Management Systems.
