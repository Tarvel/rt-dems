# save this as start_services.ps1

# Dynamically get the absolute path of the directory containing this script
$BASE_DIR = $PSScriptRoot

# Define the path to the virtual environment's Python executable 
# Note: Windows uses \Scripts\ instead of /bin/
$VENV_PYTHON = Join-Path $BASE_DIR "venv\Scripts\python.exe"

# Array to keep track of running process objects so we can kill them later
$script:processes = @()

# Helper function to start a process in the background and track its ID
function Start-TrackedProcess {
    param (
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$WorkingDirectory
    )
    # -NoNewWindow keeps the output in the current console, mimicking the '&' in bash
    # -PassThru returns the process object so we can save and kill it later
    $process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory -NoNewWindow -PassThru
    $script:processes += $process
}

Write-Host "Starting Smart Room Energy Management System from: $BASE_DIR"

# Navigate to the base directory (exit if it fails)
Set-Location -Path $BASE_DIR -ErrorAction Stop

try {
    # 1. Start Mosquitto
    Write-Host "-> Starting Mosquitto broker..."
    # Assuming mosquitto is installed and in your system PATH
    Start-TrackedProcess -FilePath "mosquitto" -ArgumentList "-c systemd/mosquitto.conf -v" -WorkingDirectory $BASE_DIR
    Start-Sleep -Seconds 2

    # 2. Start Django API
    Write-Host "-> Starting Django API..."
    $djangoDir = Join-Path $BASE_DIR "room_backend"
    Start-TrackedProcess -FilePath $VENV_PYTHON -ArgumentList "manage.py runserver 0.0.0.0:8000" -WorkingDirectory $djangoDir

    # 3. Start MQTT Logger
    Write-Host "-> Starting MQTT Logger worker..."
    Start-TrackedProcess -FilePath $VENV_PYTHON -ArgumentList "workers/mqtt_logger.py" -WorkingDirectory $BASE_DIR

    # 4. Start Rule Engine
    Write-Host "-> Starting Rule Engine worker..."
    Start-TrackedProcess -FilePath $VENV_PYTHON -ArgumentList "workers/rule_engine.py" -WorkingDirectory $BASE_DIR

    # 5. Start FastAPI ML Service
    Write-Host "-> Starting FastAPI ML Service..."
    $mlDir = Join-Path $BASE_DIR "ML"
    Start-TrackedProcess -FilePath $VENV_PYTHON -ArgumentList "test_prediction_api.py" -WorkingDirectory $mlDir

    # 6. Start Data Simulator
    Write-Host "-> Starting Data Simulator..."
    Start-TrackedProcess -FilePath $VENV_PYTHON -ArgumentList "simulation/data_simulator.py" -WorkingDirectory $BASE_DIR

    Write-Host "========================================================"
    Write-Host "✅ All services are now running in the background!"
    Write-Host "Dashboard: file://$BASE_DIR/dashboard/index.html"
    Write-Host "Press Ctrl+C to safely stop all services."
    Write-Host "========================================================"

    # The 'while' loop keeps the script open so the try/finally block can listen for Ctrl+C
    while ($true) {
        Start-Sleep -Milliseconds 500
    }
}
finally {
    # This block acts exactly like the Bash 'trap' function
    Write-Host "`nShutting down all Smart Room services..."
    foreach ($p in $script:processes) {
        if ($null -ne $p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Host "Done."
}