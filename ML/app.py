from collections import deque
from datetime import datetime, timezone
import json
import os

from fastapi import FastAPI, HTTPException
import paho.mqtt.client as mqtt
from pydantic import BaseModel
import uvicorn

from local_inference_wrapper import LocalEdgeForecaster

app = FastAPI(title="Smart Grid AI Edge API")

print("Starting Local FastAPI Server and loading AI models...")
ai_brain = LocalEdgeForecaster()
print("AI API is live and listening on port 5000!")

DECISION_INTERVAL_MINUTES = int(os.environ.get("DECISION_INTERVAL_MINUTES", 3))
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_TOPIC_SENSORS = os.environ.get("MQTT_TOPIC_SENSORS", "room/sensors")
MQTT_TOPIC_ML = os.environ.get("MQTT_TOPIC_ML", "room/ml/predictions")
MQTT_CLIENT_ID = "room-ml-fastapi"
SENSOR_SAMPLE_MINUTES = int(os.environ.get("SENSOR_SAMPLE_MINUTES", 1))

LAG_1H_STEPS = max(1, 60 // SENSOR_SAMPLE_MINUTES)
LAG_2H_STEPS = max(1, 120 // SENSOR_SAMPLE_MINUTES)
LAG_3H_STEPS = max(1, 180 // SENSOR_SAMPLE_MINUTES)
LAG_24H_STEPS = max(1, 1440 // SENSOR_SAMPLE_MINUTES)
ENERGY_HISTORY_STEPS = LAG_24H_STEPS + 1

mqtt_client = mqtt.Client(
    client_id=MQTT_CLIENT_ID,
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
)

energy_kw_history: deque[float] = deque(maxlen=ENERGY_HISTORY_STEPS)


class SensorData(BaseModel):
    timestamp: str
    temperature_c: float
    humidity: float
    lux: float
    occupancy: int
    lag_1h: float
    lag_2h: float
    lag_3h: float
    lag_24h: float


def _history_value_or_latest(steps_back: int, latest: float) -> float:
    if len(energy_kw_history) > steps_back:
        return energy_kw_history[-(steps_back + 1)]
    return latest


def _build_model_input_from_sensor(payload: dict) -> dict | None:
    timestamp = payload.get("timestamp")
    if timestamp:
        try:
            timestamp = datetime.fromisoformat(
                str(timestamp).replace("Z", "+00:00")
            ).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            timestamp = str(timestamp)
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    temperature = payload.get("temperature_c", payload.get("temperature"))
    humidity = payload.get("humidity")
    lux = payload.get("lux")
    occupancy = payload.get("occupancy")
    if None in (temperature, humidity, lux, occupancy):
        return None

    energy_kw = payload.get("energy_kw")
    if energy_kw is None and payload.get("energy_kwh") is not None:
        energy_kw = float(payload.get("energy_kwh")) * (60 / SENSOR_SAMPLE_MINUTES)
    if (
        energy_kw is None
        and payload.get("voltage") is not None
        and payload.get("current") is not None
    ):
        energy_kw = (
            float(payload.get("voltage")) * float(payload.get("current"))
        ) / 1000.0
    if energy_kw is None:
        return None

    energy_kw = float(energy_kw)
    energy_kw_history.append(energy_kw)

    return {
        "timestamp": timestamp,
        "temperature_c": float(temperature),
        "humidity": float(humidity),
        "lux": float(lux),
        "occupancy": int(occupancy),
        "lag_1h": _history_value_or_latest(LAG_1H_STEPS, energy_kw),
        "lag_2h": _history_value_or_latest(LAG_2H_STEPS, energy_kw),
        "lag_3h": _history_value_or_latest(LAG_3H_STEPS, energy_kw),
        "lag_24h": _history_value_or_latest(LAG_24H_STEPS, energy_kw),
    }


def _build_prediction_payload(sensor_payload: dict) -> dict:
    results = ai_brain.build_features_and_predict(sensor_payload)
    predicted_kw = results.get("predicted_energy_kw")
    upper_bound_kw = results.get("upper_bound_energy_kw")

    if predicted_kw is not None:
        results["predicted_energy_kwh"] = round(
            float(predicted_kw) * (DECISION_INTERVAL_MINUTES / 60), 4
        )
    if upper_bound_kw is not None:
        results["upper_bound_energy_kwh"] = round(
            float(upper_bound_kw) * (DECISION_INTERVAL_MINUTES / 60), 4
        )

    return {
        **results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "fastapi-local-model",
    }


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[INFO] ML service connected to MQTT at {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe(MQTT_TOPIC_SENSORS)
        print(f"[INFO] Subscribed to {MQTT_TOPIC_SENSORS}")
    else:
        print(f"[WARN] MQTT connection failed with rc={rc}")


def on_mqtt_message(client, userdata, msg):
    if msg.topic != MQTT_TOPIC_SENSORS:
        return

    try:
        payload = json.loads(msg.payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[WARN] Bad sensor payload: {exc}")
        return

    model_input = _build_model_input_from_sensor(payload)
    if model_input is None:
        print("[WARN] Missing required sensor fields for ML inference")
        return

    try:
        prediction = _build_prediction_payload(model_input)
    except Exception as exc:
        print(f"[WARN] ML inference failed: {exc}")
        return

    mqtt_client.publish(MQTT_TOPIC_ML, json.dumps(prediction), qos=1)
    print("[INFO] Published ML prediction to room/ml/predictions")


@app.post("/predict")
def predict_energy(data: SensorData):
    try:
        incoming_data = data.model_dump()
        prediction = _build_prediction_payload(incoming_data)
        mqtt_client.publish(MQTT_TOPIC_ML, json.dumps(prediction), qos=1)
        return prediction
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


if __name__ == "__main__":
    try:
        mqtt_client.on_connect = on_connect
        mqtt_client.on_message = on_mqtt_message
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
        mqtt_client.loop_start()
    except OSError as exc:
        print(f"[WARN] MQTT connect failed: {exc}")

    uvicorn.run(app, host="127.0.0.1", port=5000)
