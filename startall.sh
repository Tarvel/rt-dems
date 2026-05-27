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

# Load .env and export all variables so child processes inherit them
if [ -f "$BASE_DIR/.env" ]; then
    set -a
    source "$BASE_DIR/.env"
    set +a
fi

# 1. Stop any existing Mosquitto (system service or stray process)
echo "-> Stopping any existing Mosquitto..."
sudo systemctl stop mosquitto 2>/dev/null || true
killall mosquitto 2>/dev/null || true
sleep 1

# 2. Start our Mosquitto with our config
echo "-> Starting Mosquitto broker..."
mosquitto -c systemd/mosquitto.conf -d
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

# 4. Start Rule Engine (publishes mode decisions via MQTT — ESP32 actuates relays)
echo "-> Starting Rule Engine worker..."
"$VENV_PYTHON" workers/rule_engine.py &

# 5. Start FastAPI ML Service (model-agnostic — configured via MODEL_ASSET_DIR in .env)
echo "-> Starting FastAPI ML Service..."
PYTHONUNBUFFERED=1 "$VENV_PYTHON" workers/ml_service.py &

# 6. Start data source — controlled by DATA_SOURCE env var
#    "simulator"  = CSV playback only       (default, for development/testing)
#    "hardware"   = Group 1 live sensors    (for production with real hardware)
#    "both"       = both at once            (debugging only — mixed data!)
source "$BASE_DIR/.env" 2>/dev/null
DATA_SOURCE="${DATA_SOURCE:-simulator}"

echo ""
echo "  DATA_SOURCE=$DATA_SOURCE"

case "$DATA_SOURCE" in
  hardware)
    echo "-> Starting Hardware Bridge (Group 1 live sensors)..."
    cd "$BASE_DIR"
    "$VENV_PYTHON" workers/hw_bridge.py &
    echo "   (Data Simulator skipped — using real hardware)"
    ;;
  both)
    echo "-> Starting Hardware Bridge (Group 1 live sensors)..."
    cd "$BASE_DIR"
    "$VENV_PYTHON" workers/hw_bridge.py &
    echo "-> Starting Data Simulator (CSV playback)..."
    cd "$BASE_DIR"
    "$VENV_PYTHON" simulation/data_simulator.py &
    echo "   ⚠ WARNING: Both sources active — data will be mixed!"
    ;;
  *)
    # Default: simulator only
    echo "-> Starting Data Simulator (CSV playback)..."
    cd "$BASE_DIR"
    "$VENV_PYTHON" simulation/data_simulator.py &
    echo "   (Hardware Bridge skipped — using simulated data)"
    ;;
esac

echo "========================================================"
echo "✅ All services are now running in the background!"
echo "Dashboard: file://$BASE_DIR/dashboard/index.html"
echo "Press Ctrl+C to safely stop all services."
echo "========================================================"

# The 'wait' command keeps the script open so the trap can listen for Ctrl+C
wait
