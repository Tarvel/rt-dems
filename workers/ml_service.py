"""
Smart Grid Hybrid AI Prediction Service  (model-agnostic)
=========================================================

Plug-and-play ML service. To swap models:
  1. Drop the new model folder anywhere inside the project.
  2. Set MODEL_ASSET_DIR in .env to point to it.
  3. Restart the service.  No code changes required.

Required artifacts inside MODEL_ASSET_DIR:
  - tegru_model.tflite      (TE-GRU TFLite model)
  - lgb_model.joblib         (LightGBM model)
  - scaler_gru.joblib        (GRU feature scaler)
  - scaler_lgb.joblib        (LightGBM feature scaler)
  - *.csv                    (training CSV, set via CSV_FILE env)

Optional artifacts (auto-detected if present):
  - xgb_residual_model.joblib
  - uncertainty_model.joblib
  - optimal_z.joblib
  - bayes_weight_gru.joblib
"""

import json
import os
import threading
import warnings
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import paho.mqtt.client as mqtt
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

# Load .env from project root (parent of workers/)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

try:
    from tflite_runtime.interpreter import Interpreter
except ImportError:
    from ai_edge_litert.interpreter import Interpreter

warnings.filterwarnings(
    "ignore",
    message="'force_all_finite' was renamed to 'ensure_all_finite'.*",
    category=FutureWarning,
)


# =============================================================================
# CONFIGURATION
# =============================================================================
# PROJECT_ROOT is the parent of workers/  (e.g., PROJECT_CODE/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENERGY_UNIT = os.environ.get("ENERGY_UNIT", "Wh")
INCLUDE_LEGACY_UNIT_ALIASES = os.environ.get("INCLUDE_LEGACY_UNIT_ALIASES", "0").lower() in {
    "1",
    "true",
    "yes",
}


def _resolve_path(path_value: str | Path, base: Path = PROJECT_ROOT) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else base / path


MODEL_DIR = _resolve_path(os.environ.get("MODEL_ASSET_DIR", "new_ml_model/New folder"))
CSV_OPTIONS = {
    "datanew40": "datanew40.csv",
    "datanew40.csv": "datanew40.csv",
    "datarig40": "datarig40.csv",
    "datarig40.csv": "datarig40.csv",
    "rigdata_20k": "RIGDATA_20k.csv",
    "rigdata_20k.csv": "RIGDATA_20k.csv",
    "rigdata_40k": "RIGDATA_40k.csv",
    "rigdata_40k.csv": "RIGDATA_40k.csv",
}


def _resolve_csv_path() -> Path:
    if os.environ.get("CSV_PATH"):
        return _resolve_path(os.environ["CSV_PATH"])

    csv_file = os.environ.get("CSV_FILE", "datanew40.csv").strip()
    filename = CSV_OPTIONS.get(csv_file.lower(), csv_file)
    return _resolve_path(filename, MODEL_DIR)


CSV_PATH = _resolve_csv_path()

SEQ_LENGTH = int(os.environ.get("SEQ_LENGTH", 48))
FEATURE_HISTORY = int(os.environ.get("FEATURE_HISTORY", 24))
WINDOW_SIZE = SEQ_LENGTH + FEATURE_HISTORY

# Default GRU blend weight — overridden at runtime by bayes_weight_gru.joblib if present.
BAYES_WEIGHT_GRU = float(os.environ.get("BAYES_WEIGHT_GRU", 0.92))

# MQTT
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_CLIENT_ID = "ml-prediction-service"
TOPIC_SENSORS = "room/sensors"
TOPIC_ML_PREDICTIONS = "room/ml/predictions"

# Simulation index (CSV row pointer)
current_sim_index = WINDOW_SIZE


# =============================================================================
# ASSET DISCOVERY
# =============================================================================
asset_warnings: list[str] = []
asset_errors: list[str] = []
loaded_artifacts: dict[str, str | None] = {}


def _artifact_path(label: str, *names: str, patterns: tuple[str, ...] = ()) -> Path | None:
    """Find a model artifact by exact name first, then by glob pattern."""
    for name in names:
        path = MODEL_DIR / name
        if path.exists():
            loaded_artifacts[label] = str(path)
            return path

    for pattern in patterns:
        matches = sorted(MODEL_DIR.glob(pattern))
        if matches:
            loaded_artifacts[label] = str(matches[0])
            return matches[0]

    loaded_artifacts[label] = None
    return None


def _safe_joblib_load(label: str, path: Path | None, required: bool = False):
    if path is None:
        if required:
            asset_errors.append(f"Missing required artifact: {label}")
        return None

    try:
        return joblib.load(path)
    except Exception as exc:
        message = f"Could not load {label} from {path.name}: {type(exc).__name__}: {exc}"
        if required:
            asset_errors.append(message)
        else:
            asset_warnings.append(message)
        return None


def _safe_float_joblib(label: str, path: Path | None, default: float):
    value = _safe_joblib_load(label, path, required=False)
    if value is None:
        return default
    try:
        return float(value)
    except Exception as exc:
        asset_warnings.append(f"Could not coerce {label} to float: {exc}")
        return default


scaler_lgb_path = _artifact_path(
    "scaler_lgb",
    "scaler_lgb.joblib",
    "scaler_lgb (1).joblib",
    patterns=("scaler_lgb*.joblib",),
)
scaler_gru_path = _artifact_path(
    "scaler_gru",
    "scaler_gru.joblib",
    "scaler_gru (1).joblib",
    patterns=("scaler_gru*.joblib",),
)
lgb_model_path = _artifact_path("lgb_model", "lgb_model.joblib", "lightgbm_model.pkl")
tegru_model_path = _artifact_path(
    "tegru_model",
    "tegru_model.tflite",
    "te_gru_model_final.tflite",
    patterns=("*.tflite",),
)
xgb_residual_path = _artifact_path("xgb_residual_model", "xgb_residual_model.joblib")
uncertainty_path = _artifact_path("uncertainty_model", "uncertainty_model.joblib")
optimal_z_path = _artifact_path("optimal_z", "optimal_z.joblib")
bayes_weight_path = _artifact_path(
    "bayes_weight_gru",
    "bayes_weight_gru.joblib",
    "w_star_lvl1.joblib",
    "hybrid_weight_gru.joblib",
)


# =============================================================================
# CSV AND MODEL LOADING
# =============================================================================
def _load_simulation_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    df = pd.read_csv(path)
    rename_map = {
        "timestamp": "Timestamp",
        "Time": "Timestamp",
        "temperature": "Temperature_C",
        "Temperature(C)": "Temperature_C",
        "humidity": "Humidity_%",
        "Humidity(%)": "Humidity_%",
        "lux": "Luminous_Intensity_Lux",
        "Light(lux)": "Luminous_Intensity_Lux",
        "occupancy": "Occupancy",
        "Occupancy": "Occupancy",
        "energy": "Energy_Wh",
        "ENERGY": "Energy_Wh",
        "Energy_kW": "Energy_Wh",
    }
    df = df.rename(columns={src: dst for src, dst in rename_map.items() if src in df.columns})

    required = [
        "Timestamp",
        "Temperature_C",
        "Humidity_%",
        "Luminous_Intensity_Lux",
        "Occupancy",
        "Energy_Wh",
    ]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"CSV is missing required columns: {missing}")

    df["Timestamp"] = pd.to_datetime(df["Timestamp"])
    df = df.sort_values("Timestamp").reset_index(drop=True)

    for col in ["Temperature_C", "Humidity_%", "Luminous_Intensity_Lux", "Occupancy", "Energy_Wh"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    if "time_of_day" in df.columns:
        df["time_of_day"] = pd.to_numeric(df["time_of_day"], errors="coerce")
        df["time_of_day"] = df["time_of_day"].fillna(df["Timestamp"].dt.hour)
    else:
        df["time_of_day"] = df["Timestamp"].dt.hour

    return df


print("Loading AI assets...")
try:
    df_sim = _load_simulation_csv(CSV_PATH)
except Exception as exc:
    df_sim = pd.DataFrame()
    asset_errors.append(f"Could not load CSV {CSV_PATH}: {type(exc).__name__}: {exc}")

scaler_lgb = _safe_joblib_load("scaler_lgb", scaler_lgb_path, required=True)
scaler_gru = _safe_joblib_load("scaler_gru", scaler_gru_path, required=True)
lgb_model = _safe_joblib_load("lgb_model", lgb_model_path, required=False)
xgb_residual_model = _safe_joblib_load("xgb_residual_model", xgb_residual_path, required=False)
uncertainty_model = _safe_joblib_load("uncertainty_model", uncertainty_path, required=False)
optimal_z = _safe_float_joblib("optimal_z", optimal_z_path, default=1.5)
BAYES_WEIGHT_GRU = _safe_float_joblib("bayes_weight_gru", bayes_weight_path, default=BAYES_WEIGHT_GRU)

LGB_FEATURES = list(getattr(scaler_lgb, "feature_names_in_", [])) if scaler_lgb is not None else []
GRU_FEATURES = list(getattr(scaler_gru, "feature_names_in_", [])) if scaler_gru is not None else []

interpreter = None
input_details = []
output_details = []
if tegru_model_path is None:
    loaded_artifacts["tegru_model"] = None
else:
    try:
        interpreter = Interpreter(model_path=str(tegru_model_path))
        interpreter.allocate_tensors()
        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()
    except Exception as exc:
        asset_warnings.append(
            f"Could not load TE-GRU model from {tegru_model_path.name}: {type(exc).__name__}: {exc}"
        )
        interpreter = None

BASE_PREDICTORS_READY = lgb_model is not None and interpreter is not None
RESIDUAL_STACK_READY = xgb_residual_model is not None and uncertainty_model is not None
SCALERS_READY = scaler_lgb is not None and scaler_gru is not None
CSV_READY = not df_sim.empty
PREDICTION_READY = CSV_READY and SCALERS_READY and BASE_PREDICTORS_READY

if RESIDUAL_STACK_READY and not BASE_PREDICTORS_READY:
    asset_warnings.append(
        "Residual/uncertainty models were found, but they require lgb_model.joblib "
        "and tegru_model.tflite base predictors before live sensor inference can run."
    )

print(
    "[OK]" if PREDICTION_READY else "[WARN]",
    f"AI assets checked - CSV: {CSV_PATH.name}, unit: {ENERGY_UNIT}, "
    f"residual_stack: {RESIDUAL_STACK_READY}",
)


# =============================================================================
# CONTEXT HELPERS
# =============================================================================
def _missing_required_assets() -> list[str]:
    missing = []
    if not CSV_READY:
        missing.append(str(CSV_PATH))
    if scaler_lgb is None:
        missing.append("scaler_lgb.joblib or scaler_lgb (1).joblib")
    if scaler_gru is None:
        missing.append("scaler_gru.joblib or scaler_gru (1).joblib")
    if lgb_model is None:
        missing.append("lgb_model.joblib")
    if interpreter is None:
        missing.append("tegru_model.tflite")
    return missing


def _ensure_prediction_ready() -> None:
    if PREDICTION_READY:
        return

    raise HTTPException(
        status_code=503,
        detail={
            "error": "Prediction assets are incomplete.",
            "missing_required_assets": _missing_required_assets(),
            "asset_errors": asset_errors,
            "asset_warnings": asset_warnings,
            "loaded_artifacts": loaded_artifacts,
            "note": (
                "The XGBoost residual and uncertainty files need base TE-GRU and "
                "LightGBM predictions first. Add the matching lgb_model.joblib and "
                "tegru_model.tflite files to New folder, then restart the service."
            ),
        },
    )


def _find_csv_index(datetime_str: str) -> int | None:
    """Find a CSV row that can provide matching historical context."""
    if df_sim.empty:
        return None

    try:
        target = pd.Timestamp(datetime_str)
        # CSV timestamps are tz-naive; strip tz to avoid comparison errors
        if target.tzinfo is not None:
            target = target.tz_localize(None)
    except Exception:
        return None

    valid_df = df_sim[df_sim.index >= WINDOW_SIZE]
    if len(valid_df) == 0:
        return WINDOW_SIZE

    diffs = (valid_df["Timestamp"] - target).abs()
    best_idx = int(diffs.idxmin())
    if diffs.loc[best_idx] <= pd.Timedelta(hours=1):
        return best_idx

    mask_mdh = (
        (valid_df["Timestamp"].dt.month == target.month)
        & (valid_df["Timestamp"].dt.dayofweek == target.dayofweek)
        & (valid_df["Timestamp"].dt.hour == target.hour)
    )
    matches_mdh = valid_df[mask_mdh]
    if len(matches_mdh) > 0:
        return int(matches_mdh.index[-1])

    mask_dh = (
        (valid_df["Timestamp"].dt.dayofweek == target.dayofweek)
        & (valid_df["Timestamp"].dt.hour == target.hour)
    )
    matches_dh = valid_df[mask_dh]
    if len(matches_dh) > 0:
        return int(matches_dh.index[-1])

    mask_h = valid_df["Timestamp"].dt.hour == target.hour
    matches_h = valid_df[mask_h]
    if len(matches_h) > 0:
        return int(matches_h.index[-1])

    return WINDOW_SIZE


def _context_window(ctx_index: int) -> pd.DataFrame:
    if df_sim.empty:
        raise ValueError("No simulation CSV is loaded.")

    ctx_index = max(WINDOW_SIZE, min(int(ctx_index), len(df_sim) - 1))
    return df_sim.iloc[ctx_index - WINDOW_SIZE : ctx_index + 1].copy()


# =============================================================================
# BAYESIAN UNCERTAINTY AND ADAPTIVE WEIGHT ESTIMATOR
# =============================================================================
class MHWeightEstimator:
    """Metropolis-Hastings sampler for adaptive component weighting."""

    def __init__(self, n_iterations=1000, proposal_std=0.02, temperature=1e-4):
        self.n_iterations = n_iterations
        self.proposal_std = proposal_std
        self.temperature = temperature

    def estimate_weight(self, y_true: np.ndarray, pred_gru: np.ndarray, pred_lgbm: np.ndarray, w_init=0.5) -> float:
        if len(y_true) < 2:
            return w_init

        def hybrid_loss(w):
            blended = w * pred_gru + (1 - w) * pred_lgbm
            return float(np.mean((y_true - blended) ** 2))

        w_current = w_init
        loss_current = hybrid_loss(w_current)
        best_w = w_current
        best_loss = loss_current

        rng = np.random.RandomState(int(abs(loss_current * 1e5)) % (2**31))

        for _ in range(self.n_iterations):
            w_proposed = np.clip(w_current + rng.normal(0, self.proposal_std), 0.0, 1.0)
            loss_proposed = hybrid_loss(w_proposed)

            delta_loss = loss_proposed - loss_current
            alpha = 1.0 if delta_loss <= 0 else float(np.exp(-delta_loss / max(self.temperature, 1e-8)))

            if rng.uniform(0, 1) < alpha:
                w_current = w_proposed
                loss_current = loss_proposed
                if loss_current < best_loss:
                    best_loss = loss_current
                    best_w = w_current

        return best_w


class HistoryTracker:
    """Rolling window of true values and component predictions."""

    def __init__(self, max_size=200):
        self.y_true = []
        self.pred_gru = []
        self.pred_lgbm = []
        self.max_size = max_size

    def add(self, actual, gru_val, lgbm_val):
        if actual is not None and not np.isnan(actual):
            self.y_true.append(actual)
            self.pred_gru.append(gru_val)
            self.pred_lgbm.append(lgbm_val)
            if len(self.y_true) > self.max_size:
                self.y_true.pop(0)
                self.pred_gru.pop(0)
                self.pred_lgbm.pop(0)

    def clear(self):
        self.y_true.clear()
        self.pred_gru.clear()
        self.pred_lgbm.clear()

    def get(self):
        if len(self.y_true) >= 3:
            return np.array(self.y_true), np.array(self.pred_gru), np.array(self.pred_lgbm)
        return None, None, None


mh_estimator = MHWeightEstimator()
history_tracker = HistoryTracker()


# =============================================================================
# CORE PREDICTION PIPELINE
# =============================================================================
def _build_feature_frame(live_window: pd.DataFrame) -> pd.DataFrame:
    """Recreate the feature engineering from the new MODEL notebook."""
    w = live_window.copy()
    w["Timestamp"] = pd.to_datetime(w["Timestamp"])
    w = w.sort_values("Timestamp")

    w["temperature"] = pd.to_numeric(w["Temperature_C"], errors="coerce")
    w["humidity"] = pd.to_numeric(w["Humidity_%"], errors="coerce")
    w["lux"] = pd.to_numeric(w["Luminous_Intensity_Lux"], errors="coerce")
    w["occupancy"] = pd.to_numeric(w["Occupancy"], errors="coerce")

    w["hour"] = w["Timestamp"].dt.hour
    w["day_of_week"] = w["Timestamp"].dt.dayofweek
    if "time_of_day" in w.columns:
        w["time_of_day"] = pd.to_numeric(w["time_of_day"], errors="coerce")
        w["time_of_day"] = w["time_of_day"].fillna(w["hour"])
    else:
        w["time_of_day"] = w["hour"]

    w["hour_sin"] = np.sin((2 * np.pi * w["hour"]) / 24)
    w["hour_cos"] = np.cos((2 * np.pi * w["hour"]) / 24)
    w["dow_sin"] = np.sin((2 * np.pi * w["day_of_week"]) / 7)
    w["dow_cos"] = np.cos((2 * np.pi * w["day_of_week"]) / 7)
    w["is_weekend"] = w["day_of_week"].isin([5, 6]).astype(int)

    for col in ["temperature", "humidity", "lux"]:
        w[f"{col}_lag1"] = w[col].shift(1)
        w[f"{col}_lag24"] = w[col].shift(24)
        w[f"{col}_mean3"] = w[col].rolling(3).mean()
        w[f"{col}_mean24"] = w[col].rolling(24).mean()

    feature_columns = list(dict.fromkeys(LGB_FEATURES + GRU_FEATURES + ["hour"]))
    features = w[feature_columns].copy()
    model_columns = list(dict.fromkeys(LGB_FEATURES + GRU_FEATURES))
    return features.dropna(subset=model_columns)


def _invoke_gru(gru_sequence: np.ndarray, target_hour: float) -> float:
    if interpreter is None:
        raise RuntimeError("TE-GRU model is not loaded.")

    numeric_input = np.array([gru_sequence], dtype=np.float32)
    hour_input = np.array([[target_hour]], dtype=np.float32)

    for detail in input_details:
        name = detail["name"].lower()
        shape = tuple(int(dim) for dim in detail["shape"])
        value = hour_input if "hour" in name or (len(shape) == 2 and shape[-1] == 1) else numeric_input
        interpreter.set_tensor(detail["index"], value.astype(detail["dtype"]))

    interpreter.invoke()
    return float(np.ravel(interpreter.get_tensor(output_details[0]["index"]))[0])


def _component_weight() -> float:
    if RESIDUAL_STACK_READY:
        return float(np.clip(BAYES_WEIGHT_GRU, 0.0, 1.0))

    y_hist, gru_hist, lgbm_hist = history_tracker.get()
    if y_hist is not None:
        return float(mh_estimator.estimate_weight(y_hist, gru_hist, lgbm_hist))
    return 0.5


def _fallback_residual_std(best_w: float, hybrid_final: float) -> float:
    y_hist, gru_hist, lgbm_hist = history_tracker.get()
    if y_hist is not None:
        blended_hist = best_w * gru_hist + (1 - best_w) * lgbm_hist
        return float(np.std(y_hist - blended_hist))
    return max(0.05, abs(hybrid_final) * 0.10)


def run_prediction(live_window: pd.DataFrame) -> dict:
    """Run TE-GRU + LightGBM, then optional XGBoost residual correction."""
    _ensure_prediction_ready()

    current_hour_data = live_window.iloc[-1].copy()
    feature_df = _build_feature_frame(live_window)

    if len(feature_df) < SEQ_LENGTH + 1:
        raise ValueError(
            f"Not enough engineered rows for inference. Need {SEQ_LENGTH + 1}, got {len(feature_df)}."
        )

    lgb_input = scaler_lgb.transform(feature_df[LGB_FEATURES].iloc[[-1]])
    scaled_gru = scaler_gru.transform(feature_df[GRU_FEATURES])

    gru_sequence = scaled_gru[-SEQ_LENGTH:]
    target_hour = float(feature_df["hour"].iloc[-1])

    gru_raw = _invoke_gru(gru_sequence, target_hour)
    lgbm_raw = float(lgb_model.predict(lgb_input)[0])

    best_w = _component_weight()
    bayes_hybrid_raw = best_w * gru_raw + (1 - best_w) * lgbm_raw

    residual_correction = None
    uncertainty = None
    z_value = optimal_z if RESIDUAL_STACK_READY else 1.5

    if RESIDUAL_STACK_READY:
        meta_features = np.array([[gru_raw, lgbm_raw, bayes_hybrid_raw]], dtype=np.float32)
        residual_correction = float(np.ravel(xgb_residual_model.predict(meta_features))[0])
        uncertainty = abs(float(np.ravel(uncertainty_model.predict(meta_features))[0]))
        hybrid_final = bayes_hybrid_raw + residual_correction
        spread = z_value * uncertainty
    else:
        hybrid_final = bayes_hybrid_raw
        spread = z_value * _fallback_residual_std(best_w, hybrid_final)

    lower_bound = max(0.0, hybrid_final - spread)
    upper_bound = hybrid_final + spread

    actual_val = current_hour_data.get("Energy_Wh", np.nan)
    actual_wh = round(float(actual_val), 4) if pd.notna(actual_val) else None

    history_tracker.add(actual_wh, gru_raw, lgbm_raw)

    base_gru_wh = round(gru_raw, 4)
    lgbm_wh = round(lgbm_raw, 4)
    bayes_hybrid_wh = round(bayes_hybrid_raw, 4)
    hybrid_final_wh = round(hybrid_final, 4)
    lower_bound_wh = round(lower_bound, 4)
    upper_bound_wh = round(upper_bound, 4)

    predictions = {
        "energy_unit": ENERGY_UNIT,
        "actual_energy_wh": actual_wh,
        "base_gru_wh": base_gru_wh,
        "lgbm_wh": lgbm_wh,
        "bayesian_hybrid_wh": bayes_hybrid_wh,
        "hybrid_final_wh": hybrid_final_wh,
        "safety_lower_bound_wh": lower_bound_wh,
        "safety_upper_bound_wh": upper_bound_wh,
        "hybrid_weight_gru": round(best_w, 4),
        "residual_stack_enabled": RESIDUAL_STACK_READY,
    }
    if residual_correction is not None:
        predictions["xgb_residual_correction_wh"] = round(residual_correction, 4)
    if uncertainty is not None:
        predictions["predicted_uncertainty_wh"] = round(uncertainty, 4)
        predictions["optimal_z"] = round(float(z_value), 4)

    if INCLUDE_LEGACY_UNIT_ALIASES:
        predictions.update(
            {
                "actual_energy_kw": actual_wh,
                "base_gru_kwh": base_gru_wh,
                "lgbm_kwh": lgbm_wh,
                "hybrid_final_kwh": hybrid_final_wh,
                "safety_lower_bound": lower_bound_wh,
                "safety_upper_bound": upper_bound_wh,
            }
        )

    return {
        "timestamp": str(current_hour_data["Timestamp"]),
        "energy_unit": ENERGY_UNIT,
        "live_sensors": {
            "temperature_c": float(current_hour_data["Temperature_C"]),
            "humidity": float(current_hour_data["Humidity_%"]),
            "lux": float(current_hour_data["Luminous_Intensity_Lux"]),
            "occupancy": int(current_hour_data["Occupancy"]),
        },
        "predictions": predictions,
    }


# =============================================================================
# MQTT BRIDGE
# =============================================================================
mqtt_client = mqtt.Client(
    client_id=MQTT_CLIENT_ID,
    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
)


def build_mqtt_payload(result: dict) -> dict:
    """Convert internal result into the flat MQTT contract payload."""
    pred = result["predictions"]
    predicted_wh = pred["hybrid_final_wh"]
    upper_wh = pred["safety_upper_bound_wh"]
    lower_wh = pred["safety_lower_bound_wh"]
    payload = {
        "predicted_energy_wh": predicted_wh,
        "upper_bound_energy_wh": upper_wh,
        "lower_bound_energy_wh": lower_wh,
        "predicted_energy_range_wh": [lower_wh, upper_wh],
        "energy_unit": ENERGY_UNIT,
        "residual_stack_enabled": RESIDUAL_STACK_READY,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "fastapi-local-model",
    }
    if "predicted_uncertainty_wh" in pred:
        payload["predicted_uncertainty_wh"] = pred["predicted_uncertainty_wh"]

    if INCLUDE_LEGACY_UNIT_ALIASES:
        payload.update(
            {
                "predicted_energy_kw": predicted_wh,
                "upper_bound_energy_kw": upper_wh,
                "lower_bound_energy_kw": lower_wh,
                "predicted_energy_range": [lower_wh, upper_wh],
            }
        )
    return payload


def on_mqtt_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        # No longer subscribing to room/sensors — the rule engine
        # calls POST /predict via HTTP (single-timer architecture).
        print("[OK] MQTT bridge connected (HTTP-only mode — no sensor subscription)")
    else:
        print(f"[ERROR] MQTT connection failed (rc={rc})")


def start_mqtt_bridge():
    """Connect MQTT client and start the network loop in the background."""
    mqtt_client.on_connect = on_mqtt_connect
    mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)

    def _try_connect():
        while True:
            try:
                mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
                mqtt_client.loop_start()
                print(f"MQTT bridge started -> {MQTT_BROKER}:{MQTT_PORT}")
                return
            except Exception as exc:
                print(f"[WARN] MQTT broker unreachable ({exc}) - retrying in 5s...")
                import time

                time.sleep(5)

    threading.Thread(target=_try_connect, daemon=True).start()


# =============================================================================
# FASTAPI APP
# =============================================================================
@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Start MQTT bridge on boot, clean up on shutdown."""
    start_mqtt_bridge()
    yield
    try:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        print("MQTT bridge disconnected.")
    except Exception:
        pass


app = FastAPI(
    title="Smart Grid Hybrid AI - TEST SIMULATOR",
    description="HTTP endpoints are for manual testing. Production data flows through MQTT.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SensorInput(BaseModel):
    """Sensor values typed by the user for testing."""

    temperature_c: float = 28.0
    humidity: float = 60.0
    lux: float = 400.0
    occupancy: int = 1
    datetime_str: str | None = None


@app.post("/predict")
def predict_manual(sensor: SensorInput):
    """Accept manual sensor values, inject them into context, and predict."""
    global current_sim_index
    _ensure_prediction_ready()

    # Always use time-based matching for consistent context selection.
    # If no datetime provided, use current local time.
    dt_str = sensor.datetime_str or datetime.now().isoformat()
    matched_idx = _find_csv_index(dt_str)
    if matched_idx is not None:
        ctx_index = matched_idx
    else:
        if current_sim_index >= len(df_sim):
            current_sim_index = WINDOW_SIZE
        ctx_index = current_sim_index

    live_window = _context_window(ctx_index)

    idx = live_window.index[-1]
    live_window.loc[idx, "Temperature_C"] = sensor.temperature_c
    live_window.loc[idx, "Humidity_%"] = sensor.humidity
    live_window.loc[idx, "Luminous_Intensity_Lux"] = sensor.lux
    live_window.loc[idx, "Occupancy"] = sensor.occupancy
    live_window.loc[idx, "Energy_Wh"] = np.nan

    if sensor.datetime_str:
        try:
            user_ts = pd.Timestamp(sensor.datetime_str)
            # Strip timezone — CSV timestamps are tz-naive
            if user_ts.tzinfo is not None:
                user_ts = user_ts.tz_localize(None)
        except Exception:
            user_ts = live_window.loc[idx, "Timestamp"]
    else:
        user_ts = live_window.loc[idx, "Timestamp"]

    live_window.loc[idx, "Timestamp"] = user_ts
    live_window.loc[idx, "time_of_day"] = user_ts.hour

    return run_prediction(live_window)


@app.get("/metadata")
def metadata():
    """Return active model, CSV, and unit metadata for dashboards and testers."""
    return {
        "model_dir": str(MODEL_DIR),
        "csv_path": str(CSV_PATH),
        "csv_name": CSV_PATH.name,
        "available_csv_files": sorted(set(CSV_OPTIONS.values())),
        "energy_unit": ENERGY_UNIT,
        "include_legacy_unit_aliases": INCLUDE_LEGACY_UNIT_ALIASES,
        "sequence_length": SEQ_LENGTH,
        "feature_history": FEATURE_HISTORY,
        "window_size": WINDOW_SIZE,
        "rows_loaded": len(df_sim),
        "artifact_mode": "residual_uncertainty_stack" if RESIDUAL_STACK_READY else "base_hybrid",
        "prediction_ready": PREDICTION_READY,
        "base_predictors_ready": BASE_PREDICTORS_READY,
        "residual_stack_ready": RESIDUAL_STACK_READY,
        "bayes_weight_gru": round(BAYES_WEIGHT_GRU, 4),
        "optimal_z": round(float(optimal_z), 4),
        "missing_required_assets": _missing_required_assets(),
        "asset_errors": asset_errors,
        "asset_warnings": asset_warnings,
        "loaded_artifacts": loaded_artifacts,
        "lgb_features": LGB_FEATURES,
        "gru_features": GRU_FEATURES,
    }


@app.get("/predict_next")
def predict_next_hour():
    """Advance one row through the CSV dataset and return the prediction."""
    global current_sim_index
    _ensure_prediction_ready()

    if current_sim_index >= len(df_sim):
        return {"error": "Simulation finished. End of dataset."}

    live_window = _context_window(current_sim_index)
    current_sim_index += 1

    return run_prediction(live_window)


@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    # Look for dashboard in the model folder's parent (e.g., new_ml_model/)
    html_path = MODEL_DIR.parent / "test_dashboard.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>test_dashboard.html not found.</h1>"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    uvicorn.run(app, host="0.0.0.0", port=port)
