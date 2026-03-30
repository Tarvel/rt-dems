# ============================================================
# RT-DEMS Backend Startup Script (targets lib/rt-dems-final)
# ============================================================

# 1. MQTT Broker
Write-Host "Checking for MQTT Broker..." -ForegroundColor Cyan
$portCheck = netstat -an | findstr "0.0.0.0:1883"
if ($portCheck) {
    Write-Host "[OK] MQTT Broker is already running on port 1883." -ForegroundColor Green
} else {
    Write-Host "[1/6] Launching Mosquitto Broker..." -ForegroundColor Green
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "mosquitto -c lib/rt-dems-final/systemd/mosquitto.conf -v"
}

# 2. Start Django API Server
Write-Host "[2/6] Launching Django API..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd lib/rt-dems-final; ./venv/Scripts/python room_backend/manage.py runserver 0.0.0.0:8000"

Start-Sleep -Seconds 5

# 3. Start MQTT Logger
Write-Host "[3/6] Launching MQTT Logger..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd lib/rt-dems-final; ./venv/Scripts/python workers/mqtt_logger.py"

# 4. Start Rule Engine
Write-Host "[4/6] Launching Rule Engine..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd lib/rt-dems-final; ./venv/Scripts/python workers/rule_engine.py"

# 5. Start ML Prediction Service
Write-Host "[5/6] Launching ML Prediction Service..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd lib/rt-dems-final/ML; ../venv/Scripts/python test_prediction_api.py"

# 6. Start Data Simulator
Write-Host "[6/6] Launching Data Simulator..." -ForegroundColor Green
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd lib/rt-dems-final; ./venv/Scripts/python simulation/data_simulator.py"

Write-Host "`nAll 6 services triggered. Wait ~10s for services to initialise." -ForegroundColor Cyan
