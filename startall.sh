#!/bin/bash

# Dynamically get the absolute path of the directory containing this script
BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

# Define the path to the virtual environment's Python executable
VENV_PYTHON="$BASE_DIR/venv/bin/python"

# Function to cleanly shut down all background processes when Ctrl+C is pressed
cleanup() {
    echo ""
    echo "Shutting down all Smart Room services..."
    # Kill all processes started by this script
    kill $(jobs -p) 2>/dev/null
    echo "Done."
    exit
}

# Trap the Ctrl+C signal to trigger the cleanup function
trap cleanup SIGINT

echo "Starting Smart Room Energy Management System from: $BASE_DIR"

# Navigate to the base directory (exit if it fails)
cd "$BASE_DIR" || { echo "Error: Could not access $BASE_DIR"; exit 1; }

# 1. Start Mosquitto
echo "-> Starting Mosquitto broker..."
mosquitto -c systemd/mosquitto.conf -v &
# Give the broker 2 seconds to fully initialize
sleep 2 

# 2. Start Django API
echo "-> Starting Django API..."
cd "$BASE_DIR/room_backend"
../venv/bin/python manage.py runserver 0.0.0.0:8000 &

# 3. Start MQTT Logger
echo "-> Starting MQTT Logger worker..."
cd "$BASE_DIR"
"$VENV_PYTHON" workers/mqtt_logger.py &

# 4. Start Rule Engine
echo "-> Starting Rule Engine worker..."
"$VENV_PYTHON" workers/rule_engine.py &

# 5. Start FastAPI ML Service
echo "-> Starting FastAPI ML Service..."
cd "$BASE_DIR/ML"
../venv/bin/python test_prediction_api.py &

# 6. Start Data Simulator
echo "-> Starting Data Simulator..."
cd "$BASE_DIR"
"$VENV_PYTHON" simulation/data_simulator.py &

echo "========================================================"
echo "✅ All services are now running in the background!"
echo "Dashboard: file://$BASE_DIR/dashboard/index.html"
echo "Press Ctrl+C to safely stop all services."
echo "========================================================"

# The 'wait' command keeps the script open so the trap can listen for Ctrl+C
wait
