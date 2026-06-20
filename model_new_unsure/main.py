#!/usr/bin/env python3
"""
model_new_unsure/main.py — TE-GRU + XGBoost + MH Energy Prediction Service
===========================================================================

FastAPI service that wraps the TE-GRU + XGBoost + Metropolis-Hastings
Bayesian residual correction model.  Designed as a drop-in replacement
for LIGHT_ML_MODEL/main.py with the same API contract.

Data flow:
    Rule Engine  →  POST /predict  →  this service  →  JSON response

This version reads the sequence history directly from the SQLite database
(energy_relaystate table) on every prediction request, falling back to the
static CSV seed data. This makes the service stateless, clock-resilient, and
immune to RAM buffer resets during Pi restarts/reboots.
"""

import os
import sys
import json
import sqlite3
import threading
import warnings
import time as _time_mod
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import joblib

# ── .env support ──
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

# Suppress sklearn feature-name warnings
warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# =============================================================================
# CONFIGURATION
# =============================================================================
BASE_DIR     = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

def _resolve_path(path_value, base=PROJECT_ROOT):
    p = Path(path_value)
    return p if p.is_absolute() else base / p

MODEL_ASSET_DIR = _resolve_path(
    os.environ.get("MODEL_ASSET_DIR", BASE_DIR / "model_assets")
)
CSV_PATH = Path(os.environ.get("CSV_PATH", MODEL_ASSET_DIR / "mydatanew.csv"))
ENERGY_UNIT = os.environ.get("ENERGY_UNIT", "Wh")

# Buffer size: need ≥50 rows for rolling12 + window8 + safety margin
BUFFER_SIZE = int(os.environ.get("BUFFER_SIZE", 60))

PEAK_DEMAND_KW = float(os.environ.get("PEAK_DEMAND_KW", 5.0))


# =============================================================================
# CUSTOM CLASS (must be defined BEFORE joblib.load)
# =============================================================================
class SequenceRegressorPipeline:
    """Wraps a Keras model + optional scaler for sequence prediction."""
    def __init__(self, model, scaler=None, window=8):
        self.model = model
        self.scaler = scaler
        self.window = window

    def predict(self, X, verbose=0):
        X_arr = np.asarray(X)
        if self.scaler is not None:
            original_shape = X_arr.shape
            if X_arr.ndim == 3:
                X_2d = X_arr.reshape(-1, X_arr.shape[-1])
                X_scaled = self.scaler.transform(X_2d)
                X_arr = X_scaled.reshape(original_shape)
            else:
                X_arr = self.scaler.transform(X_arr)
        return self.model.predict(X_arr, verbose=verbose)


# =============================================================================
# LOAD MODEL BUNDLE
# =============================================================================
BUNDLE_PATH = MODEL_ASSET_DIR / "tegru_xgb_mh_pipeline.joblib"

tegru = None
xgb_residual = None
alpha = 0.0
beta = 0.0
q95 = 0.0
q80 = 0.0
feature_columns = []
WINDOW = 8
MODEL_READY = False

ENERGY_COL = "energy"

print(f"Loading AI assets from: {MODEL_ASSET_DIR}")

try:
    bundle = joblib.load(BUNDLE_PATH)
    tegru          = bundle["tegru"]
    xgb_residual   = bundle["xgb_residual"]
    alpha          = bundle["mh_alpha"]
    beta           = bundle["mh_beta"]
    q95            = bundle.get("q95", 0.0)
    q80            = bundle.get("q80", 0.0)
    feature_columns = bundle["feature_columns"]
    WINDOW         = bundle.get("window", 8)
    MODEL_READY    = True
    print(f"  Model loaded: TEGRU + XGB + MH  (window={WINDOW}, features={len(feature_columns)})")
    print(f"  Alpha={alpha:.6f}  Beta={beta:.6f}  Q95={q95:.4f}  Q80={q80:.4f}")
    print(f"  Feature columns (first 10): {feature_columns[:10]}")
except Exception as exc:
    print(f"  [ERROR] Failed to load model: {exc}")

# =============================================================================
# LOAD CSV (cold-start context)
# =============================================================================
df_csv = pd.DataFrame()
CSV_READY = False

try:
    df_csv = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    df_csv.columns = (
        df_csv.columns.astype(str).str.strip().str.lower()
        .str.replace(r"\s+", " ", regex=True)
    )
    # Rename 'energy' to match our ENERGY_COL if needed
    if "energy" not in df_csv.columns and "real time energy" in df_csv.columns:
        df_csv.rename(columns={"real time energy": "energy"}, inplace=True)
    CSV_READY = True
    print(f"  CSV loaded: {CSV_PATH.name}  ({len(df_csv)} rows)")
except Exception as exc:
    print(f"  [WARN] CSV load failed: {exc}")

PREDICTION_READY = MODEL_READY

print(f"[{'OK' if PREDICTION_READY else 'WARN'}] Assets — "
      f"model:{MODEL_READY}  csv:{CSV_READY}")


# =============================================================================
# DATABASE HISTORY RETRIEVAL
# =============================================================================
def get_history_from_db_or_csv() -> list[dict]:
    """Retrieve the last BUFFER_SIZE rows from SQLite database (energy_relaystate)
    and scale 1-min energy_kw to 5-min Wh. Pad using CSV if DB has fewer rows.
    """
    db_path = PROJECT_ROOT / "room_backend" / "db.sqlite3"
    db_history = []
    success = False
    
    if db_path.exists():
        try:
            # Query the database with a short timeout to prevent lockups
            conn = sqlite3.connect(db_path, timeout=5.0)
            cursor = conn.cursor()
            
            # Check if target table exists
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='energy_relaystate'")
            if cursor.fetchone():
                cursor.execute("""
                    SELECT timestamp, temperature, humidity, lux, occupancy, energy_kw
                    FROM (
                        SELECT id, timestamp, temperature, humidity, lux, occupancy, energy_kw
                        FROM energy_relaystate
                        ORDER BY id DESC
                        LIMIT 60
                    )
                    ORDER BY id ASC
                """)
                rows = cursor.fetchall()
                for row in rows:
                    ts_str, temp, hum, lux_val, occ, energy_kw = row
                    
                    try:
                        timestamp = pd.Timestamp(ts_str)
                        if timestamp.tzinfo is not None:
                            timestamp = timestamp.tz_localize(None)
                    except Exception:
                        timestamp = pd.Timestamp.now()
                        
                    db_history.append({
                        "timestamp": timestamp,
                        "temperature": float(temp) if temp is not None else 25.0,
                        "humidity": float(hum) if hum is not None else 50.0,
                        "lux": float(lux_val) if lux_val is not None else 0.0,
                        "occupancy": int(occ) if occ is not None else 0,
                        # Scale 1-min Wh to 5-min Wh
                        "energy": float(energy_kw) * 5.0 if energy_kw is not None else 11.6,
                    })
                success = True
            conn.close()
        except Exception as exc:
            print(f"[WARN] Failed to read history from DB: {exc}. Falling back to CSV.")
            
    # Pad with CSV if we don't have enough DB entries
    if success and len(db_history) >= BUFFER_SIZE:
        return db_history
    else:
        pad_size = BUFFER_SIZE - len(db_history)
        csv_rows = []
        if CSV_READY and not df_csv.empty:
            seed = df_csv.tail(pad_size)
            for _, row in seed.iterrows():
                ts = row["timestamp"]
                if isinstance(ts, str):
                    ts = pd.Timestamp(ts)
                if ts.tzinfo is not None:
                    ts = ts.tz_localize(None)
                csv_rows.append({
                    "timestamp": ts,
                    "temperature": float(row.get("temperature", 25.0)),
                    "humidity": float(row.get("humidity", 50.0)),
                    "lux": float(row.get("lux", 0.0)),
                    "occupancy": int(row.get("occupancy", 0)),
                    "energy": float(row.get(ENERGY_COL, 0.0)),
                })
        # If still not enough (e.g. cold start with empty DB and missing CSV), fill defaults
        while len(csv_rows) < pad_size:
            csv_rows.append({
                "timestamp": pd.Timestamp.now(),
                "temperature": 25.0,
                "humidity": 50.0,
                "lux": 0.0,
                "occupancy": 0,
                "energy": 11.6,
            })
        return csv_rows + db_history


# =============================================================================
# FEATURE ENGINEERING
# =============================================================================
def engineer_features(df):
    """Compute all features the model expects."""
    df = df.copy()

    # The model was trained with "real time energy" as the energy column name.
    # Ensure both aliases exist so feature_columns always finds a match.
    df["real time energy"] = df[ENERGY_COL]

    df["hour"] = df["timestamp"].dt.hour
    df["minute"] = df["timestamp"].dt.minute
    df["dayofweek"] = df["timestamp"].dt.dayofweek
    df["month"] = df["timestamp"].dt.month
    df["day"] = df["timestamp"].dt.day
    df["is_weekend"] = (df["dayofweek"] >= 5).astype(int)

    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)
    df["minute_sin"] = np.sin(2 * np.pi * df["minute"] / 60)
    df["minute_cos"] = np.cos(2 * np.pi * df["minute"] / 60)
    df["dayofweek_sin"] = np.sin(2 * np.pi * df["dayofweek"] / 7)
    df["dayofweek_cos"] = np.cos(2 * np.pi * df["dayofweek"] / 7)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)

    # Cross features
    df["temp_x_humidity"] = df["temperature"] * df["humidity"]
    df["temp_x_occupancy"] = df["temperature"] * df["occupancy"]
    df["humidity_x_occupancy"] = df["humidity"] * df["occupancy"]
    df["lux_x_occupancy"] = df["lux"] * df["occupancy"]
    df["hour_x_occupancy"] = df["hour"] * df["occupancy"]

    # Energy lag features
    for ecol in [ENERGY_COL, "real time energy"]:
        prefix = "energy" if ecol == ENERGY_COL else "real time energy"
        df[f"{prefix}_lag1"] = df[ecol].shift(1)
        df[f"{prefix}_lag2"] = df[ecol].shift(2)
        df[f"{prefix}_lag3"] = df[ecol].shift(3)
        df[f"{prefix}_roll3"] = df[ecol].shift(1).rolling(3).mean()
        df[f"{prefix}_roll6"] = df[ecol].shift(1).rolling(6).mean()
        df[f"{prefix}_roll12"] = df[ecol].shift(1).rolling(12).mean()
        df[f"{prefix}_std6"] = df[ecol].shift(1).rolling(6).std()
        df[f"{prefix}_std12"] = df[ecol].shift(1).rolling(12).std()
        df[f"{prefix}_trend3"] = df[ecol].shift(1) - df[ecol].shift(3)
        df[f"{prefix}_trend6"] = df[ecol].shift(1) - df[ecol].shift(6)
        df[f"{prefix}_trend12"] = df[ecol].shift(1) - df[ecol].shift(12)

    # Sensor lag features (temperature, humidity, lux, occupancy)
    for col in ["temperature", "humidity", "lux", "occupancy"]:
        df[f"{col}_lag1"] = df[col].shift(1)
        df[f"{col}_lag2"] = df[col].shift(2)
        df[f"{col}_lag3"] = df[col].shift(3)
        df[f"{col}_roll3"] = df[col].shift(1).rolling(3).mean()
        df[f"{col}_roll6"] = df[col].shift(1).rolling(6).mean()
        df[f"{col}_roll12"] = df[col].shift(1).rolling(12).mean()
        df[f"{col}_std6"] = df[col].shift(1).rolling(6).std()
        df[f"{col}_std12"] = df[col].shift(1).rolling(12).std()
        df[f"{col}_trend3"] = df[col].shift(1) - df[col].shift(3)
        df[f"{col}_trend6"] = df[col].shift(1) - df[col].shift(6)
        df[f"{col}_trend12"] = df[col].shift(1) - df[col].shift(12)

    df = df.bfill().ffill().fillna(0)
    return df


# =============================================================================
# PREDICTION
# =============================================================================
def run_prediction(temperature, humidity, lux, occupancy, buf_list, timestamp=None):
    """Build features from DB history + current reading, run model, return result."""
    if not MODEL_READY:
        raise HTTPException(503, "Model not loaded")

    if len(buf_list) < 12:
        raise HTTPException(503,
            f"Insufficient history: {len(buf_list)} rows (need ≥12)")

    # 2. Build the working dataframe from buffer
    working_df = pd.DataFrame(buf_list)
    working_df["timestamp"] = pd.to_datetime(working_df["timestamp"])
    if working_df["timestamp"].dt.tz is not None:
        working_df["timestamp"] = working_df["timestamp"].dt.tz_localize(None)

    # 3. Append the current prediction row
    if timestamp is None:
        ts = pd.Timestamp.now()
    else:
        ts = pd.Timestamp(timestamp)
        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)

    current_row = pd.DataFrame([{
        "timestamp": ts,
        "temperature": float(temperature),
        "humidity": float(humidity),
        "lux": float(lux),
        "occupancy": int(occupancy),
        ENERGY_COL: np.nan,  # this is what we're predicting
    }])
    working_df = pd.concat([working_df, current_row], ignore_index=True)

    # 4. Engineer features
    feature_df = engineer_features(working_df)

    # 5. Force energy lag features from actual buffer values
    energy_values = [r["energy"] for r in buf_list]
    last_12 = energy_values[-12:] if len(energy_values) >= 12 else energy_values
    current_idx = feature_df.index[-1]

    forced = {
        "energy_lag1": last_12[-1] if len(last_12) >= 1 else 0.0,
        "energy_lag2": last_12[-2] if len(last_12) >= 2 else 0.0,
        "energy_lag3": last_12[-3] if len(last_12) >= 3 else 0.0,
        "energy_roll3": float(np.mean(last_12[-3:])) if len(last_12) >= 3 else 0.0,
        "energy_roll6": float(np.mean(last_12[-6:])) if len(last_12) >= 6 else 0.0,
        "energy_roll12": float(np.mean(last_12[-12:])) if len(last_12) >= 12 else 0.0,
        "energy_std6": float(np.std(last_12[-6:], ddof=0)) if len(last_12) >= 6 else 0.0,
        "energy_std12": float(np.std(last_12[-12:], ddof=0)) if len(last_12) >= 12 else 0.0,
        "energy_trend3": float(last_12[-1] - last_12[-3]) if len(last_12) >= 3 else 0.0,
        "energy_trend6": float(last_12[-1] - last_12[-6]) if len(last_12) >= 6 else 0.0,
        "energy_trend12": float(last_12[-1] - last_12[-12]) if len(last_12) >= 12 else 0.0,
    }

    for key, value in forced.items():
        feature_df.loc[current_idx, key] = value
        # Always force both "energy_*" and "real time energy_*" variants
        alt_key = f"real time energy_{key.replace('energy_', '')}"
        feature_df.loc[current_idx, alt_key] = value

    # 6. Select feature columns (fill missing with 0)
    X_all = feature_df.reindex(columns=feature_columns, fill_value=0)

    # 7. Prepare inputs
    X_current = X_all.iloc[[-1]]  # 1 row for XGBoost
    n_rows = min(WINDOW, len(X_all))
    X_sequence = X_all.iloc[-n_rows:].values
    if n_rows < WINDOW:
        # Pad with the first row repeated
        pad = np.tile(X_sequence[0], (WINDOW - n_rows, 1))
        X_sequence = np.vstack([pad, X_sequence])
    X_sequence = X_sequence.reshape(1, WINDOW, len(feature_columns))

    # 8. Predict
    tegru_pred = float(tegru.predict(X_sequence, verbose=0)[0].ravel()[0])
    residual_pred = float(xgb_residual.predict(X_current)[0])

    final_pred = tegru_pred + (alpha * residual_pred) + beta
    print(f"[PREDICT] tegru={tegru_pred:.4f} residual={residual_pred:.4f} alpha={alpha:.4f} beta={beta:.4f} final={final_pred:.4f}")
    final_pred = max(0.0, float(final_pred))

    # Zero-occupancy rule
    if occupancy == 0 and CSV_READY:
        zero_df = df_csv[df_csv["occupancy"] == 0]
        if len(zero_df) > 0:
            lowest = float(zero_df[ENERGY_COL].min())
            final_pred = lowest

    # Confidence bounds
    lower_95 = max(0.0, final_pred - q95)
    upper_95 = final_pred + q95
    lower_80 = max(0.0, final_pred - q80)
    upper_80 = final_pred + q80

    return {
        "hybrid_final_wh": round(final_pred, 4),
        "safety_lower_bound_wh": round(lower_95, 4),
        "safety_upper_bound_wh": round(upper_95, 4),
        "lower_80_wh": round(lower_80, 4),
        "upper_80_wh": round(upper_80, 4),
        "tegru_raw_wh": round(tegru_pred, 4),
        "xgb_residual_wh": round(residual_pred, 4),
        "mh_alpha": round(alpha, 6),
        "mh_beta": round(beta, 6),
        "buffer_size": len(buf_list),
    }


# =============================================================================
# FASTAPI APP
# =============================================================================
app = FastAPI(
    title="Smart Grid Hybrid AI — TE-GRU + XGBoost + MH (Database-Backed)",
    description=(
        "Hybrid energy prediction: TE-GRU sequence model + XGBoost "
        "residual correction + Metropolis-Hastings Bayesian calibration."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ensure_ready():
    if not PREDICTION_READY:
        raise HTTPException(503, "Model not loaded — check startup logs")


# =============================================================================
# ENDPOINTS
# =============================================================================
class SensorInput(BaseModel):
    temperature_c: float = 25.0
    humidity:      float = 50.0
    lux:           float = 0.0
    occupancy:     int   = 1
    datetime_str:  str | None = None


@app.post("/predict")
def predict_manual(sensor: SensorInput):
    """Accept live sensor values and return an energy prediction."""
    _ensure_ready()

    # Load history dynamically from database (or CSV fallback)
    buf_list = get_history_from_db_or_csv()

    result = run_prediction(
        temperature=sensor.temperature_c,
        humidity=sensor.humidity,
        lux=sensor.lux,
        occupancy=sensor.occupancy,
        buf_list=buf_list,
        timestamp=sensor.datetime_str,
    )

    return {
        "predicted_energy_wh":      result["hybrid_final_wh"],
        "upper_bound_energy_wh":    result["safety_upper_bound_wh"],
        "lower_bound_energy_wh":    result["safety_lower_bound_wh"],
        "predicted_energy_range_wh": [result["safety_lower_bound_wh"],
                                      result["safety_upper_bound_wh"]],
        "energy_unit":              ENERGY_UNIT,
        "tegru_raw_wh":             result["tegru_raw_wh"],
        "xgb_residual_wh":         result["xgb_residual_wh"],
        "peak_demand":              PEAK_DEMAND_KW,
        "buffer_size":              result["buffer_size"],
        "timestamp":                datetime.now(timezone.utc).isoformat(),
        "source":                   "fastapi-tegru-xgb-mh-db",
    }


@app.get("/metadata")
def metadata():
    buf_list = get_history_from_db_or_csv()
    buf_len = len(buf_list)
    latest_energy = buf_list[-1]["energy"] if buf_list else None

    return {
        "model":            "TE-GRU + XGBoost + MH Bayesian Residual (Database-Backed)",
        "model_ready":      MODEL_READY,
        "csv_ready":        CSV_READY,
        "prediction_ready": PREDICTION_READY,
        "window":           WINDOW,
        "feature_count":    len(feature_columns),
        "alpha":            alpha,
        "beta":             beta,
        "q95":              q95,
        "q80":              q80,
        "buffer_size":      buf_len,
        "buffer_capacity":  BUFFER_SIZE,
        "latest_energy":    latest_energy,
        "energy_unit":      ENERGY_UNIT,
        "asset_dir":        str(MODEL_ASSET_DIR),
    }


@app.get("/")
def root():
    return {"status": "running", "model": "TE-GRU + XGBoost + MH (Database-Backed)"}


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("ML_PORT", 5000))
    uvicorn.run(app, host="0.0.0.0", port=port)