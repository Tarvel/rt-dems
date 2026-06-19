# Smart Grid Hybrid AI Energy Prediction Service

This folder is a testing wrapper for the model artifacts in `New folder`.

The current `New folder` contains a different brain set from `testing2`: it has
the XGBoost residual corrector and adaptive uncertainty layer, but it does not
currently include the two base predictors needed to create the meta-features for
that layer.

## Current Artifact Set

| Path | Status | Purpose |
|---|---:|---|
| `New folder/datanew40.csv` | present | Default CSV context for `/predict_next` and dashboard Auto Next. |
| `New folder/scaler_lgb (1).joblib` | present | Scaler for LightGBM base features. |
| `New folder/scaler_gru (1).joblib` | present | Scaler for TE-GRU sequence features. |
| `New folder/xgb_residual_model.joblib` | present | Corrects the Bayesian hybrid residual. |
| `New folder/uncertainty_model.joblib` | present | Predicts adaptive uncertainty width. |
| `New folder/optimal_z.joblib` | present | Calibrated interval multiplier. |
| `New folder/lgb_model.joblib` | missing | Required LightGBM base predictor. |
| `New folder/tegru_model.tflite` | missing | Required TE-GRU base predictor. |

The residual and uncertainty models expect three inputs:

```text
TEGRU prediction, LightGBM prediction, Bayesian blend prediction
```

That means `xgb_residual_model.joblib` and `uncertainty_model.joblib` cannot make
sensor-to-energy predictions by themselves. Add the matching `lgb_model.joblib`
and `tegru_model.tflite` from the same training run to make `/predict` and
`/predict_next` fully runnable.

Do not mix in the old `testing2` base models for real evaluation unless you only
want a smoke test. These new scalers were fitted on `datanew40.csv`, not the old
`datarig40.csv` training set.

## What The Code Does Now

- Loads `datanew40.csv` by default.
- Recognizes both normal and downloaded Windows filenames, such as
  `scaler_lgb.joblib` and `scaler_lgb (1).joblib`.
- Loads the XGBoost residual model, Random Forest uncertainty model, and
  calibrated `optimal_z` value when present.
- Reports missing required base assets through `/metadata`, the dashboard log,
  and `diagnose.py`.
- Keeps the HTTP and MQTT response shape compatible with the previous dashboard
  while adding residual-stack fields when available.

## Run

```powershell
cd "C:\Users\HP\Downloads\testing3"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python test_prediction_api.py
```

Open:

```text
http://127.0.0.1:5000/
```

Check metadata:

```powershell
Invoke-RestMethod http://127.0.0.1:5000/metadata
```

If the base predictors are still missing, `prediction_ready` will be `false` and
the response will list the exact files needed.

## Expected Complete `New folder`

```text
datanew40.csv
scaler_lgb (1).joblib
scaler_gru (1).joblib
lgb_model.joblib
tegru_model.tflite
xgb_residual_model.joblib
uncertainty_model.joblib
optimal_z.joblib
```

## Manual Prediction Body

```json
{
  "temperature_c": 28.0,
  "humidity": 60.0,
  "lux": 400.0,
  "occupancy": 3,
  "datetime_str": "2026-05-24T14:20:00"
}
```

`datetime_str` is optional. When supplied, the service searches the loaded CSV
for the nearest historical context before replacing the current row with your
manual sensor values.

## Diagnostics

```powershell
python diagnose.py
```

With the current artifact set, the diagnostic intentionally stops and reports:

```text
lgb_model.joblib
tegru_model.tflite
```

After those matching base files are added, the diagnostic will run several CSV
segments and print R2, MAE, and RMSE for GRU, LightGBM, and the final hybrid.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `5000` | HTTP server port. |
| `MODEL_ASSET_DIR` | `New folder` | Folder containing model/scaler artifacts and CSV files. |
| `CSV_FILE` | `datanew40.csv` | Friendly CSV filename inside `MODEL_ASSET_DIR`. |
| `CSV_PATH` | unset | Full explicit CSV path. Overrides `CSV_FILE`. |
| `BAYES_WEIGHT_GRU` | `0.95` | Fallback TE-GRU weight for the Bayesian blend if no saved weight is provided. |
| `INCLUDE_LEGACY_UNIT_ALIASES` | `0` | Set to `1` only if an older client still expects previous `*_kwh` or `*_kw` names. |
| `MQTT_BROKER` | `localhost` | MQTT broker host. |
| `MQTT_PORT` | `1883` | MQTT broker port. |
| `PEAK_DEMAND_KW` | `2.4` | Kept for existing MQTT payload compatibility. |
