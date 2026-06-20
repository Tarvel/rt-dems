# Suggestions, Current Errors, and Improvements

## What Was Adjusted Now
- Restored ML API endpoints `POST /reset` and `GET /csv_data` for compatibility with simulator and ML dashboard.
- Restored MQTT sensor override path in ML service so incoming `room/sensors` values influence predictions.
- Restored ML dependency lines in `ML/requirements.txt` for `paho-mqtt` and `requests`.

## Current Errors/Warnings Observed
1. Lint/format warnings (non-blocking):
- Multiple line-length warnings in `ML/test_prediction_api.py`, `simulation/data_simulator.py`, and `workers/rule_engine.py`.
- These do not block execution but reduce readability and CI lint cleanliness.

2. Environment-specific import warning:
- `RPi.GPIO` unresolved on non-Raspberry Pi environments in `workers/rule_engine.py`.
- This is expected on laptops/desktops; the code already has a `MockGPIO` fallback.

3. Workspace hygiene issues:
- Binary artifacts are tracked/changed (`room_backend/db.sqlite3`, `ML/__pycache__/...pyc`).
- Recommend excluding Python cache files and considering whether `db.sqlite3` should be versioned.

## High-Value Improvements Recommended
1. Contract tests for integration safety:
- Add tests that validate payload shape for:
  - `room/sensors`
  - `room/ml/predictions`
  - `room/relays/state`
- This will prevent accidental breakages when ML files are replaced again.

2. Add CI lint + format checks:
- Use `ruff` (lint + import/order/style) and `black` (formatting) for consistent code quality.

3. Add startup health checks:
- Add a lightweight startup script that checks:
  - MQTT broker reachability
  - ML API `/predict_next` and `/reset`
  - DB path writable
- This will shorten deployment troubleshooting time.

4. Stabilize simulator/ML sync:
- Keep `POST /reset` as a permanent endpoint contract.
- Optionally include an ML API `/health` endpoint returning current index and model loaded status.

5. Improve deployment isolation:
- Keep root and ML dependencies synchronized or documented to avoid “works in one venv, fails in another” situations.

6. Documentation alignment:
- Update docs where they still mention removed ML CLI tools or stale behavior.
- Confirm ML service port and endpoint list are consistently documented.

## Optional Next Round (If Needed)
- Clean up all current lint warnings in touched files.
- Add `__pycache__/` and `*.pyc` to `.gitignore`.
- Add a small integration smoke test script that verifies full message flow end-to-end.





May 27 15:53:09 anpr smartroom[4507]: 2026-05-27 15:53:09,878 [INFO] rule_engine: ML prediction fetched: EDFI=7.8723 Wh  [6.6914, 9.0531]
May 27 15:53:09 anpr smartroom[4507]: 2026-05-27 15:53:09,878 [INFO] rule_engine: Env snapshot: temp=33.7°C, humidity=54.5, lux=8.93, occupancy=3, battery=100.0%
May 27 15:53:09 anpr smartroom[4507]: 2026-05-27 15:53:09,878 [INFO] rule_engine: ━━━ Decision: Class C (class unchanged)  R1=ON R2=OFF R3=OFF
May 27 15:53:09 anpr smartroom[4507]: 2026-05-27 15:53:09,878 [INFO] rule_engine:     EDFI: 7.8723 Wh  [lower=6.6914, upper=9.0531]  thresholds: Peak=60 / Mod=20 / Base=5
May 27 15:53:09 anpr smartroom[4507]: 2026-05-27 15:53:09,878 [INFO] rule_engine:     Reason: BASELINE LOAD (EDFI 7.87, 5.0 <= x < 20.0) → Smart C

