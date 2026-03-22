"""
test_prediction_api.py — Smart Grid Hybrid AI Prediction Service
=================================================================

Dual-protocol ML service:
  • MQTT (primary)  — subscribes to room/sensors, runs inference on every
                       sensor message, publishes contract-compliant results
                       to room/ml/predictions for rule_engine, mqtt_logger,
                       and the dashboard.
  • HTTP  (testing) — POST /predict lets test.py and test_dashboard.html
                       send manual sensor inputs and receive predictions.
                       GET  /predict_next advances through the CSV for
                       quick sequential testing.

Port: 8000 (same port the rest of the system expects).
"""

import os
import json
import threading
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import numpy as np
import pandas as pd
import joblib
from ai_edge_litert.interpreter import Interpreter
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import paho.mqtt.client as mqtt


# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════
BASE_DIR = "."
CSV_PATH = os.path.join(BASE_DIR, "abs_smart_grid_dataset_20k.csv")
WINDOW_SIZE = 24

# MQTT
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_CLIENT_ID = "ml-prediction-service"
TOPIC_SENSORS = "room/sensors"
TOPIC_ML_PREDICTIONS = "room/ml/predictions"

# Energy thresholds (for contract compatibility)
PEAK_DEMAND_KW = float(os.environ.get("PEAK_DEMAND_KW", 2.4))

# Simulation index (CSV row pointer)
current_sim_index = WINDOW_SIZE


# ═══════════════════════════════════════════════════════════════════
# LOAD AI ASSETS ON MODULE IMPORT
# ═══════════════════════════════════════════════════════════════════
print("Loading AI assets...")
df_sim = pd.read_csv(CSV_PATH)
if 'Luminous_Intensity' in df_sim.columns and 'Luminous_Intensity_Lux' not in df_sim.columns:
    df_sim = df_sim.rename(columns={'Luminous_Intensity': 'Luminous_Intensity_Lux'})
df_sim['Timestamp'] = pd.to_datetime(df_sim['Timestamp'])

scaler = joblib.load(os.path.join(BASE_DIR, "v2_scalar.joblib"))
lgb_corrector = joblib.load(os.path.join(BASE_DIR, "v3_residual_lightgbm_model.joblib"))

interpreter = Interpreter(model_path=os.path.join(BASE_DIR, "v2_final_te_gru_model.tflite"))
interpreter.allocate_tensors()
input_details = interpreter.get_input_details()
output_details = interpreter.get_output_details()
print("AI assets loaded ✓")


# ═══════════════════════════════════════════════════════════════════
# BAYESIAN UNCERTAINTY ESTIMATOR
# ═══════════════════════════════════════════════════════════════════
class MHUncertaintyEstimator:
    def __init__(self, iterations=150, burn_in=30, step_size=0.05):
        self.iterations = iterations
        self.burn_in = burn_in
        self.step_size = step_size

    def estimate_sigma(self, errors_window: np.ndarray, initial_sigma: float) -> float:
        if len(errors_window) < 2 or initial_sigma <= 0: return 0.1
        current_sigma = initial_sigma
        accepted_sigmas = []

        def log_likelihood(sigma):
            if sigma <= 0.001: return -np.inf
            return np.sum(-np.log(sigma) - (errors_window**2) / (2 * sigma**2))

        current_ll = log_likelihood(current_sigma)
        for _ in range(self.iterations):
            proposed_sigma = np.random.normal(current_sigma, self.step_size)
            if proposed_sigma <= 0.001: continue
            proposed_ll = log_likelihood(proposed_sigma)
            if proposed_ll > current_ll or np.random.rand() < np.exp(proposed_ll - current_ll):
                current_sigma = proposed_sigma
                current_ll = proposed_ll
            accepted_sigmas.append(current_sigma)

        return float(np.mean(accepted_sigmas[self.burn_in:])) if len(accepted_sigmas) > self.burn_in else current_sigma

mh_estimator = MHUncertaintyEstimator()


def unscale_prediction(scaled_pred: float, y_raw: np.ndarray, y_scaled: np.ndarray) -> float:
    m, c = np.polyfit(y_scaled, y_raw, 1)
    return float((scaled_pred * m) + c)


# ═══════════════════════════════════════════════════════════════════
# CORE PREDICTION PIPELINE
# ═══════════════════════════════════════════════════════════════════
def run_prediction(live_window: pd.DataFrame) -> dict:
    """Run the full GRU + LightGBM hybrid pipeline on a 25-row window.

    Returns a dict with all prediction outputs.
    """
    current_hour_data = live_window.iloc[-1].copy()

    # Engineer Time Features
    live_window['Hour'], live_window['DayOfWeek'], live_window['Month'] = live_window['Timestamp'].dt.hour, live_window['Timestamp'].dt.dayofweek, live_window['Timestamp'].dt.month
    live_window['Hour_Sin'], live_window['Hour_Cos'] = np.sin(2 * np.pi * live_window['Hour'] / 24), np.cos(2 * np.pi * live_window['Hour'] / 24)
    live_window['DayOfWeek_Sin'], live_window['DayOfWeek_Cos'] = np.sin(2 * np.pi * live_window['DayOfWeek'] / 7), np.cos(2 * np.pi * live_window['DayOfWeek'] / 7)
    live_window['Month_Sin'], live_window['Month_Cos'] = np.sin(2 * np.pi * live_window['Month'] / 12), np.cos(2 * np.pi * live_window['Month'] / 12)
    live_window['Is_Weekend'] = (live_window['DayOfWeek'] >= 5).astype(int)

    live_window['Lag_1h'] = live_window['Energy_kW'].shift(1).bfill()
    live_window['Lag_2h'] = live_window['Energy_kW'].shift(2).bfill()
    live_window['Lag_3h'] = live_window['Energy_kW'].shift(3).bfill()
    live_window['Lag_24h'] = live_window['Energy_kW'].shift(24).bfill()
    live_window['Rolling_Mean_3h'] = live_window['Energy_kW'].rolling(3, min_periods=1).mean()
    live_window['Rolling_Std_3h'] = live_window['Energy_kW'].rolling(3, min_periods=1).std().fillna(0)

    expected_gru = ['Energy_kW', 'Temperature_C', 'Humidity_%', 'Luminous_Intensity_Lux', 'Lag_1h', 'Lag_2h', 'Lag_3h', 'Lag_24h', 'Rolling_Mean_3h', 'Rolling_Std_3h', 'Occupancy', 'Is_Weekend', 'Hour_Sin', 'Hour_Cos', 'DayOfWeek_Sin', 'DayOfWeek_Cos', 'Month_Sin', 'Month_Cos']
    expected_lgb = expected_gru[1:]

    # STRICT COLUMN ALIGNMENT FIX
    scaler_cols = [c.split('__')[-1] for c in scaler.get_feature_names_out()]
    scaled_window = pd.DataFrame(scaler.transform(live_window[scaler_cols]), columns=scaler_cols)

    # TE-GRU Inference (Only give it the first 24 rows)
    tensor_input = np.array([scaled_window[expected_gru].iloc[:-1].values], dtype=np.float32)
    interpreter.set_tensor(input_details[0]['index'], tensor_input)
    interpreter.invoke()
    gru_scaled = interpreter.get_tensor(output_details[0]['index'])[0][0]

    gru_raw = unscale_prediction(gru_scaled, live_window['Energy_kW'].iloc[:-1].values, scaled_window['Energy_kW'].iloc[:-1].values)

    # LightGBM Inference (Only give it the 25th live row)
    lgb_input = scaled_window[expected_lgb].iloc[-1:].reset_index(drop=True)
    residual_correction = lgb_corrector.predict(lgb_input)[0]

    hybrid_final_kwh = gru_raw + residual_correction

    # Bayesian Bounds
    recent_errors = live_window['Energy_kW'].iloc[:-1].values - live_window['Energy_kW'].iloc[:-1].mean()
    dynamic_sigma = mh_estimator.estimate_sigma(recent_errors, initial_sigma=0.5)

    stat_lower = hybrid_final_kwh - (1.96 * dynamic_sigma)
    lower_bound = stat_lower if stat_lower > 0 else (hybrid_final_kwh * 0.90)
    upper_bound = hybrid_final_kwh + (1.96 * dynamic_sigma)

    return {
        "timestamp": str(current_hour_data['Timestamp']),
        "live_sensors": {
            "temperature_c": float(current_hour_data['Temperature_C']),
            "humidity": float(current_hour_data['Humidity_%']),
            "lux": float(current_hour_data['Luminous_Intensity_Lux']),
            "occupancy": int(current_hour_data['Occupancy']),
            "energy_kw": float(current_hour_data['Energy_kW']),
        },
        "predictions": {
            "hybrid_final_kwh": round(hybrid_final_kwh, 4),
            "safety_lower_bound": round(lower_bound, 4),
            "safety_upper_bound": round(upper_bound, 4)
        }
    }


# ═══════════════════════════════════════════════════════════════════
# MQTT BRIDGE (Primary system workflow)
# ═══════════════════════════════════════════════════════════════════
mqtt_client = mqtt.Client(
    client_id=MQTT_CLIENT_ID,
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
)


def build_mqtt_payload(result: dict) -> dict:
    """Convert internal result into the flat contract payload
    expected by rule_engine.py, mqtt_logger.py, and the dashboard.
    """
    pred = result["predictions"]
    return {
        "predicted_energy_kwh": pred["hybrid_final_kwh"],
        "upper_bound_energy_kwh": pred["safety_upper_bound"],
        "predicted_energy_range": pred["safety_upper_bound"],
        "peak_demand": PEAK_DEMAND_KW,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "fastapi-local-model",
    }


def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        client.subscribe(TOPIC_SENSORS, qos=1)
        print(f"✓ MQTT bridge connected — subscribed to '{TOPIC_SENSORS}'")
    else:
        print(f"✗ MQTT connection failed (rc={rc})")


def on_mqtt_message(client, userdata, msg):
    """Sensor message arrives → run prediction → publish result to MQTT."""
    global current_sim_index
    try:
        if current_sim_index >= len(df_sim):
            print("⚠ Simulation finished — resetting index")
            current_sim_index = WINDOW_SIZE

        live_window = df_sim.iloc[
            current_sim_index - WINDOW_SIZE : current_sim_index + 1
        ].copy()
        current_sim_index += 1

        result = run_prediction(live_window)
        mqtt_payload = build_mqtt_payload(result)

        client.publish(TOPIC_ML_PREDICTIONS, json.dumps(mqtt_payload), qos=1)
        print(
            f"  ► MQTT published to {TOPIC_ML_PREDICTIONS}: "
            f"{mqtt_payload['predicted_energy_kw']:.4f} kW"
        )
    except Exception as exc:
        print(f"✗ MQTT prediction error: {exc}")


def start_mqtt_bridge():
    """Connect MQTT client and start network loop.

    If the broker is not reachable yet (e.g. Mosquitto started after this
    service), a background thread retries every 5 seconds until it connects.
    """
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.on_message = on_mqtt_message
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)

    def _try_connect():
        while True:
            try:
                mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                mqtt_client.loop_start()
                print(f"MQTT bridge started → {MQTT_BROKER}:{MQTT_PORT}")
                return  # success — stop retrying
            except OSError as exc:
                print(f"⚠ MQTT broker unreachable ({exc}) — retrying in 5s…")
                import time
                time.sleep(5)

    # Run the connection attempts in a daemon thread so FastAPI starts immediately
    t = threading.Thread(target=_try_connect, daemon=True)
    t.start()


# ═══════════════════════════════════════════════════════════════════
# FASTAPI APP (HTTP — for testing only)
# ═══════════════════════════════════════════════════════════════════
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Start MQTT bridge on boot, clean up on shutdown."""
    start_mqtt_bridge()
    yield
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    print("MQTT bridge disconnected.")


app = FastAPI(
    title="Smart Grid Hybrid AI - TEST SIMULATOR",
    description="HTTP endpoints are for manual testing only. "
                "Production data flows through MQTT.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Pydantic model for manual input ---
class SensorInput(BaseModel):
    """Sensor values typed by the user for testing."""
    temperature_c: float = 28.0
    humidity: float = 60.0
    lux: float = 400.0
    occupancy: int = 1
    energy_kw: float = 1.5


# --- HTTP endpoint: manual input (for test.py / test_dashboard.html) ---
@app.post("/predict")
def predict_manual(sensor: SensorInput):
    """Accept manually-typed sensor values, inject them into the model's
    current CSV window row, and return the prediction.

    The GRU still uses 24 rows of CSV history for context, but YOUR
    values replace the 25th (current) row so you can see how the model
    responds to your inputs.
    """
    global current_sim_index

    if current_sim_index >= len(df_sim):
        current_sim_index = WINDOW_SIZE

    live_window = df_sim.iloc[
        current_sim_index - WINDOW_SIZE : current_sim_index + 1
    ].copy()

    # Override the last row with manual inputs
    idx = live_window.index[-1]
    live_window.loc[idx, 'Temperature_C'] = sensor.temperature_c
    live_window.loc[idx, 'Humidity_%'] = sensor.humidity
    live_window.loc[idx, 'Luminous_Intensity_Lux'] = sensor.lux
    live_window.loc[idx, 'Occupancy'] = sensor.occupancy
    live_window.loc[idx, 'Energy_kW'] = sensor.energy_kw

    return run_prediction(live_window)


# --- HTTP endpoint: CSV auto-step (quick sequential test) ---
@app.get("/predict_next")
def predict_next_hour():
    """Advance one row through the CSV dataset and return the prediction."""
    global current_sim_index

    if current_sim_index >= len(df_sim):
        return {"error": "Simulation finished. End of dataset."}

    live_window = df_sim.iloc[
        current_sim_index - WINDOW_SIZE : current_sim_index + 1
    ].copy()
    current_sim_index += 1

    return run_prediction(live_window)


# --- Serve test_dashboard.html at root ---
@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    html_path = os.path.join(BASE_DIR, "test_dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r") as f:
            return f.read()
    return "<h1>test_dashboard.html not found in ML/</h1>"


# ═══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5000)
