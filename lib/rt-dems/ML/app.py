import json
import os
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from local_inference_wrapper import LocalEdgeForecaster

app = FastAPI(title="Smart Grid AI Edge API")

MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_CLIENT_ID = os.environ.get("ML_MQTT_CLIENT_ID", "ml-fastapi-service")
TOPIC_SENSORS = os.environ.get("MQTT_SENSOR_TOPIC", "room/sensors")
TOPIC_PREDICTIONS = os.environ.get(
    "MQTT_ML_PREDICTION_TOPIC", "room/ml/predictions"
)

# Keep peak demand configurable and in watts.
DEFAULT_PEAK_DEMAND_W = float(os.environ.get("PEAK_DEMAND_W", 2400.0))

print("[ML] Loading local inference wrapper...")
ai_brain = LocalEdgeForecaster()
print("[ML] Local model is ready")


class SensorData(BaseModel):
    timestamp: str
    temperature_c: float
    humidity: float
    lux: float
    occupancy: int
    voltage: float | None = None
    current: float | None = None
    battery_level: float | None = None
    power_w: float | None = None
    lag_1h: float | None = None
    lag_2h: float | None = None
    lag_3h: float | None = None
    lag_24h: float | None = None


def _numeric_mean(payload: dict) -> float:
    numeric_values = [
        float(value)
        for value in payload.values()
        if isinstance(value, (int, float))
    ]
    if not numeric_values:
        raise ValueError("No numeric fields found for prediction")
    return sum(numeric_values) / len(numeric_values)


def _now_for_model() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _lag_fallback_kw(payload: dict) -> float:
    if isinstance(payload.get("power_w"), (int, float)):
        return float(payload["power_w"]) / 1000.0
    if isinstance(payload.get("current"), (int, float)) and isinstance(
        payload.get("voltage"), (int, float)
    ):
        return (float(payload["current"]) * float(payload["voltage"])) / 1000.0
    return _numeric_mean(payload)


def _to_model_payload(payload: dict) -> dict:
    base_lag = _lag_fallback_kw(payload)
    temperature_c = payload.get(
        "temperature_c", payload.get("temperature", 25.0)
    )

    return {
        "timestamp": payload.get("timestamp", _now_for_model()),
        "temperature_c": float(temperature_c),
        "humidity": float(payload.get("humidity", 50.0)),
        "lux": float(payload.get("lux", 0.0)),
        "occupancy": int(payload.get("occupancy", 0)),
        "lag_1h": float(payload.get("lag_1h", base_lag)),
        "lag_2h": float(payload.get("lag_2h", base_lag)),
        "lag_3h": float(payload.get("lag_3h", base_lag)),
        "lag_24h": float(payload.get("lag_24h", base_lag)),
    }


def build_prediction(payload: dict) -> dict:
    model_payload = _to_model_payload(payload)
    model_result = ai_brain.build_features_and_predict(model_payload)

    mean_kw = float(model_result["mean_prediction_kw"])
    upper_kw = float(model_result["upper_bound_kw"])

    # Use upper bound for conservative load control.
    predicted_power_kw = round(upper_kw, 4)
    predicted_power_w = round(predicted_power_kw * 1000.0, 2)

    return {
        "mean_prediction_kw": round(mean_kw, 4),
        "upper_bound_kw": round(upper_kw, 4),
        "predicted_power_kw": predicted_power_kw,
        "predicted_power_w": predicted_power_w,
        # Compatibility fields used by existing logger and dashboards.
        "predicted_energy_range": predicted_power_kw,
        "peak_demand": round(DEFAULT_PEAK_DEMAND_W / 1000.0, 4),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "hybridized-model",
    }


def publish_prediction(prediction: dict) -> None:
    mqtt_client.publish(TOPIC_PREDICTIONS, json.dumps(prediction), qos=1)


def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print(f"[ML] Connected to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}")
        client.subscribe([(TOPIC_SENSORS, 1)])
        print(f"[ML] Subscribed to {TOPIC_SENSORS}")
    else:
        print(f"[ML] MQTT connection failed with rc={rc}")


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        prediction = build_prediction(payload)
        publish_prediction(prediction)
    except Exception as exc:
        print(f"[ML] Failed to process MQTT payload: {exc}")


mqtt_client = mqtt.Client(
    client_id=MQTT_CLIENT_ID,
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message


@app.on_event("startup")
def startup_event() -> None:
    print("Starting FastAPI server with MQTT ML pipeline...")
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
    mqtt_client.loop_start()


@app.on_event("shutdown")
def shutdown_event() -> None:
    mqtt_client.loop_stop()
    mqtt_client.disconnect()


@app.post("/predict")
def predict_energy(data: SensorData):
    try:
        prediction = build_prediction(data.model_dump(exclude_none=True))
        publish_prediction(prediction)
        return prediction
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=5000)
