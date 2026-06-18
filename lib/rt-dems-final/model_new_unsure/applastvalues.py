import os
import numpy as np
import pandas as pd
import joblib
import streamlit as st


class SequenceRegressorPipeline:
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


MODEL_PATH = "energy_model_outputs/tegru_xgb_mh_pipeline.joblib"
SENSOR_EXCEL_PATH = "mydatanew.xlsx"   # Convert your Excel to .xlsx
ENERGY_COL = "real time energy"


st.set_page_config(page_title="Real-Time Energy Prediction", layout="centered")
st.title("Real-Time TEGRU-XGBoost-MH Energy Prediction")


@st.cache_resource
def load_model():
    return joblib.load(MODEL_PATH)


bundle = load_model()

tegru = bundle["tegru"]
xgb_residual = bundle["xgb_residual"]

alpha = bundle["mh_alpha"]
beta = bundle["mh_beta"]

q95 = bundle.get("q95", 0.0)
q80 = bundle.get("q80", 0.0)

feature_columns = bundle["feature_columns"]
print("NUMBER OF FEATURES =", len(feature_columns))
print(feature_columns)
WINDOW = bundle.get("window", 8)

st.write("Loaded model: **TEGRU-XGBoost-MH Bayesian Residual Correction**")
st.write(f"Sequence window used by model: **{WINDOW}**")


def read_sensor_excel():
    if not os.path.exists(SENSOR_EXCEL_PATH):
        raise FileNotFoundError(f"Excel file not found: {SENSOR_EXCEL_PATH}")

    df = pd.read_excel(SENSOR_EXCEL_PATH)

    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace(r"\s+", " ", regex=True)
    )

    if "energy" in df.columns and ENERGY_COL not in df.columns:
        df = df.rename(columns={"energy": ENERGY_COL})

    required = ["timestamp", "temperature", "humidity", "lux", "occupancy", ENERGY_COL]
    missing = [c for c in required if c not in df.columns]

    if missing:
        raise ValueError(f"Missing columns: {missing}. Available: {list(df.columns)}")

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")

    for col in ["temperature", "humidity", "lux", "occupancy", ENERGY_COL]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required)
    df = df.sort_values("timestamp").reset_index(drop=True)

    if len(df) < max(12, WINDOW + 5):
        raise ValueError(f"At least {max(12, WINDOW + 5)} rows are required.")

    return df


def save_prediction_to_excel(new_row):
    df = read_sensor_excel()

    new_df = pd.DataFrame([new_row])
    df = pd.concat([df, new_df], ignore_index=True)

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.sort_values("timestamp").drop_duplicates(
        subset=["timestamp"],
        keep="last"
    ).reset_index(drop=True)

    df.to_excel(SENSOR_EXCEL_PATH, index=False)


def get_lowest_zero_occupancy_energy():
    df = read_sensor_excel()
    zero_df = df[df["occupancy"] == 0]

    if len(zero_df) > 0:
        return float(zero_df[ENERGY_COL].min())

    return float(df[ENERGY_COL].min())


def apply_occupancy_rules(prediction_time, occupancy_value):
    messages = []

    if prediction_time.dayofweek == 5 and occupancy_value == 0:
        occupancy_value = 1
        messages.append(
            "Saturday rule applied: occupancy cannot be 0 on Saturday. "
            "Occupancy was automatically changed to 1."
        )

    return occupancy_value, messages


def engineer_features(df):
    df = df.copy()

    df["hour"] = df["timestamp"].dt.hour
    df["minute"] = df["timestamp"].dt.minute
    df["dayofweek"] = df["timestamp"].dt.dayofweek
    df["month"] = df["timestamp"].dt.month
    df["day"] = df["timestamp"].dt.day
    df["is_weekend"] = (df["dayofweek"] >= 5).astype(int)

    df["hour_sin"] = np.sin(2 * np.pi * df["hour"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour"] / 24)

    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)

    df["temp_x_humidity"] = df["temperature"] * df["humidity"]
    df["temp_x_occupancy"] = df["temperature"] * df["occupancy"]
    df["humidity_x_occupancy"] = df["humidity"] * df["occupancy"]
    df["lux_x_occupancy"] = df["lux"] * df["occupancy"]
    df["hour_x_occupancy"] = df["hour"] * df["occupancy"]

    df["energy_lag1"] = df[ENERGY_COL].shift(1)
    df["energy_lag2"] = df[ENERGY_COL].shift(2)
    df["energy_lag3"] = df[ENERGY_COL].shift(3)

    df["energy_roll3"] = df[ENERGY_COL].shift(1).rolling(3).mean()
    df["energy_roll6"] = df[ENERGY_COL].shift(1).rolling(6).mean()
    df["energy_roll12"] = df[ENERGY_COL].shift(1).rolling(12).mean()

    df["energy_std6"] = df[ENERGY_COL].shift(1).rolling(6).std()
    df["energy_std12"] = df[ENERGY_COL].shift(1).rolling(12).std()

    df["energy_trend3"] = df[ENERGY_COL].shift(1) - df[ENERGY_COL].shift(3)
    df["energy_trend6"] = df[ENERGY_COL].shift(1) - df[ENERGY_COL].shift(6)
    df["energy_trend12"] = df[ENERGY_COL].shift(1) - df[ENERGY_COL].shift(12)

    df[f"{ENERGY_COL}_lag1"] = df["energy_lag1"]
    df[f"{ENERGY_COL}_lag2"] = df["energy_lag2"]
    df[f"{ENERGY_COL}_lag3"] = df["energy_lag3"]

    df[f"{ENERGY_COL}_roll3"] = df["energy_roll3"]
    df[f"{ENERGY_COL}_roll6"] = df["energy_roll6"]
    df[f"{ENERGY_COL}_roll12"] = df["energy_roll12"]

    df[f"{ENERGY_COL}_std6"] = df["energy_std6"]
    df[f"{ENERGY_COL}_std12"] = df["energy_std12"]

    df[f"{ENERGY_COL}_trend3"] = df["energy_trend3"]
    df[f"{ENERGY_COL}_trend6"] = df["energy_trend6"]
    df[f"{ENERGY_COL}_trend12"] = df["energy_trend12"]

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


def prepare_realtime_prediction_data(temperature, humidity, lux, occupancy):
    sensor_df = read_sensor_excel()

    latest_time = sensor_df["timestamp"].iloc[-1]
    prediction_time = latest_time + pd.Timedelta(minutes=5)

    occupancy, rule_messages = apply_occupancy_rules(prediction_time, occupancy)

    recent_12 = sensor_df.tail(12).copy()
    history_rows = sensor_df.tail(max(50, WINDOW + 20, 12)).copy()

    st.write("Latest 12 Excel records used for lag generation:")
    st.dataframe(
        recent_12[["timestamp", "temperature", "humidity", "lux", "occupancy", ENERGY_COL]]
        .astype(str)
    )

    prediction_row = {
        "timestamp": prediction_time,
        "temperature": float(temperature),
        "humidity": float(humidity),
        "lux": float(lux),
        "occupancy": float(occupancy),
        ENERGY_COL: np.nan
    }

    working_df = pd.concat(
        [history_rows, pd.DataFrame([prediction_row])],
        ignore_index=True
    )

    feature_df = engineer_features(working_df)

    energy_values = recent_12[ENERGY_COL].astype(float).tolist()
    current_index = feature_df.index[-1]

    forced_energy_features = {
        "energy_lag1": energy_values[-1],
        "energy_lag2": energy_values[-2],
        "energy_lag3": energy_values[-3],
        "energy_roll3": float(np.mean(energy_values[-3:])),
        "energy_roll6": float(np.mean(energy_values[-6:])),
        "energy_roll12": float(np.mean(energy_values[-12:])),
        "energy_std6": float(np.std(energy_values[-6:], ddof=0)),
        "energy_std12": float(np.std(energy_values[-12:], ddof=0)),
        "energy_trend3": float(energy_values[-1] - energy_values[-3]),
        "energy_trend6": float(energy_values[-1] - energy_values[-6]),
        "energy_trend12": float(energy_values[-1] - energy_values[-12]),
    }

    for key, value in forced_energy_features.items():
        feature_df.loc[current_index, key] = value
        alternative_key = f"{ENERGY_COL}_{key.replace('energy_', '')}"
        feature_df.loc[current_index, alternative_key] = value

    X_all = feature_df.reindex(columns=feature_columns, fill_value=0)

    X_current = X_all.iloc[[-1]]

    X_sequence = X_all.iloc[-WINDOW:].values.reshape(
        1,
        WINDOW,
        len(feature_columns)
    )

    return X_current, X_sequence, prediction_time, occupancy, rule_messages, forced_energy_features


def predict_energy(X_current, X_sequence, occupancy):
    tegru_pred = float(tegru.predict(X_sequence, verbose=0)[0].ravel()[0])
    residual_pred = float(xgb_residual.predict(X_current)[0])

    final_pred = tegru_pred + (alpha * residual_pred) + beta
    final_pred = max(0.0, float(final_pred))

    rule_messages = []

    if occupancy == 0:
        lowest_wh = get_lowest_zero_occupancy_energy()
        final_pred = lowest_wh
        rule_messages.append(
            f"Zero-occupancy rule applied: predicted energy forced to "
            f"lowest observed zero-occupancy value = {lowest_wh:.4f} Wh."
        )

    lower_95 = max(0.0, final_pred - q95)
    upper_95 = final_pred + q95

    lower_80 = max(0.0, final_pred - q80)
    upper_80 = final_pred + q80

    return {
        "tegru_pred": tegru_pred,
        "residual_pred": residual_pred,
        "final_pred": final_pred,
        "lower_95": lower_95,
        "upper_95": upper_95,
        "lower_80": lower_80,
        "upper_80": upper_80,
        "rule_messages": rule_messages
    }


st.subheader("Real-Time Input for Next 5-Minute Prediction")

try:
    sensor_df_preview = read_sensor_excel()
    latest_row = sensor_df_preview.iloc[-1]
    next_time = latest_row["timestamp"] + pd.Timedelta(minutes=5)

    st.info(f"Latest Excel time: {latest_row['timestamp']}")
    st.info(f"Next prediction time: {next_time}")

    use_latest_sensor_values = st.checkbox(
        "Use latest temperature, humidity, lux and occupancy from Excel",
        value=True
    )

    if use_latest_sensor_values:
        temperature_input = float(latest_row["temperature"])
        humidity_input = float(latest_row["humidity"])
        lux_input = float(latest_row["lux"])
        occupancy_input = int(latest_row["occupancy"])

        st.write(f"Temperature: `{temperature_input}`")
        st.write(f"Humidity: `{humidity_input}`")
        st.write(f"Lux: `{lux_input}`")
        st.write(f"Occupancy: `{occupancy_input}`")

    else:
        temperature_input = st.number_input("Temperature", value=25.0)
        humidity_input = st.number_input("Humidity", value=60.0)
        lux_input = st.number_input("Lux", value=300.0)
        occupancy_input = st.number_input("Occupancy", value=1, step=1)

except Exception as e:
    st.error(f"Could not read Excel file: {e}")


if st.button("Predict and Save to Excel"):
    try:
        (
            X_current,
            X_sequence,
            prediction_time,
            occupancy_used,
            rule_messages,
            forced_energy_features
        ) = prepare_realtime_prediction_data(
            temperature_input,
            humidity_input,
            lux_input,
            occupancy_input
        )

        result = predict_energy(X_current, X_sequence, occupancy_used)

        for msg in rule_messages:
            st.warning(msg)

        for msg in result["rule_messages"]:
            st.warning(msg)

        new_row = {
            "timestamp": prediction_time,
            "temperature": float(temperature_input),
            "humidity": float(humidity_input),
            "lux": float(lux_input),
            "occupancy": float(occupancy_used),
            ENERGY_COL: float(result["final_pred"]),
            "predicted_energy": float(result["final_pred"]),
            "pi95_lower": float(result["lower_95"]),
            "pi95_upper": float(result["upper_95"]),
            "pi80_lower": float(result["lower_80"]),
            "pi80_upper": float(result["upper_80"]),
            "tegru_prediction": float(result["tegru_pred"]),
            "xgboost_residual_prediction": float(result["residual_pred"]),
            "mh_alpha": float(alpha),
            "mh_beta": float(beta),
        }

        save_prediction_to_excel(new_row)

        st.success(f"Final Predicted Energy: {result['final_pred']:.4f} Wh")
        st.success("New input and prediction saved to Excel successfully.")

        st.write(f"Prediction time saved: **{prediction_time}**")
        st.write(f"Occupancy used: **{occupancy_used}**")

        st.subheader("Energy Lag Values Used")
        st.write(f"energy_lag1 = `{forced_energy_features['energy_lag1']:.4f}`")
        st.write(f"energy_lag2 = `{forced_energy_features['energy_lag2']:.4f}`")
        st.write(f"energy_lag3 = `{forced_energy_features['energy_lag3']:.4f}`")
        st.write(f"energy_roll12 = `{forced_energy_features['energy_roll12']:.4f}`")
        st.write(f"energy_std6 = `{forced_energy_features['energy_std6']:.4f}`")
       # st.write(f"energy_std12 = `{forced_energy_features['energy_std12']:.4f}`")

        st.subheader("Prediction Uncertainty")
        st.write(
            f"**95% Prediction Interval:** "
            f"{result['lower_95']:.4f} Wh to {result['upper_95']:.4f} Wh"
        )
        st.write(
            f"**80% Prediction Interval:** "
            f"{result['lower_80']:.4f} Wh to {result['upper_80']:.4f} Wh"
        )

        st.subheader("Model Breakdown")
        st.write(f"TEGRU Base Prediction: `{result['tegru_pred']:.4f}` Wh")
        st.write(f"XGBoost Residual Prediction: `{result['residual_pred']:.4f}` Wh")
        st.write(f"MH Alpha: `{alpha:.6f}`")
        st.write(f"MH Beta: `{beta:.6f}`")

        st.rerun()

    except Exception as e:
        st.error(f"Prediction failed: {e}")