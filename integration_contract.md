# Integration Contract - MQTT Topics and JSON Payloads

This contract defines active MQTT topics, ownership, and payload schemas.

Broker: `<PI_IP>:1883`
WebSocket listener: `<PI_IP>:9001`
QoS: `1`

## 1. Topic Map

| Topic | Publisher | Subscribers | Description |
|---|---|---|---|
| `room/sensors` | hw_bridge or simulator | ML service, logger, rule engine, dashboard | Normalised telemetry stream |
| `room/hardware/nano` | Group 1 ESP32 | hw_bridge | Combined environmental + battery data |
| `room/ml/predictions` | ML service | logger, rule engine, dashboard | Model predictions |
| `room/data/averaged` | logger | dashboard | 5-minute averaged values |
| `room/relays/state` | rule engine | **ESP32 relay controller**, dashboard | Current mode and relay states |
| `room/control/override` | dashboard | rule engine | Manual mode/relay override commands |

## 2. Group 1 Hardware Payload Contract

Group 1's ESP32 publishes a single combined payload (environmental sensors + battery) to `room/hardware/nano`. The `hw_bridge.py` worker normalises this into the `room/sensors` schema (section 3) so downstream subscribers need no changes.

### 2a. Combined Payload (`room/hardware/nano`)

```json
{
  "temperature": 31.6,
  "humidity": 63.2,
  "voltage": 0.0,
  "current": 0.00,
  "power": 0.0,
  "energy": 0.000,
  "lux": 5.75,
  "ultrasonic_occupancy": 0,
  "radar_motion": 1,
  "battery_voltage": 26.2,
  "soc": 100
}
```

| Field | Type | Unit | Notes |
|---|---|---|---|
| `temperature` | float | °C | Room temperature |
| `humidity` | float | % | Relative humidity |
| `voltage` | float | V | Mains voltage |
| `current` | float | A | Load current |
| `power` | float | W | Instantaneous power |
| `energy` | float | kWh | Cumulative energy (mapped to `energy_kw` in bridge) |
| `lux` | float | lx | Ambient light level |
| `ultrasonic_occupancy` | int | 0/1 | Ultrasonic presence (mapped to `occupancy`) |
| `radar_motion` | int | 0/1 | Radar-based motion detection (passed through) |
| `battery_voltage` | float | V | Battery terminal voltage |
| `soc` | float | % | State of Charge (mapped to `battery_level`) |

### 2b. Field mapping (hw_bridge normalisation)

| Group 1 field | → `room/sensors` field | Transform |
|---|---|---|
| `temperature` | `temperature`, `temperature_c` | Direct copy |
| `humidity` | `humidity` | Direct copy |
| `voltage` | `voltage` | Direct copy |
| `current` | `current` | Direct copy |
| `energy` | `energy_kw` | Key rename |
| `lux` | `lux` | Direct copy |
| `ultrasonic_occupancy` | `occupancy` | Key rename + int cast |
| `radar_motion` | `radar_motion` | Pass-through |
| `soc` | `battery_level` | Key rename |
| `battery_voltage` | `battery_voltage` | Pass-through |

## 3. Sensor Payload Contract (`room/sensors`)

### Required fields

```json
{
  "temperature_c": 32.5,
  "temperature": 32.5,
  "humidity": 60.0,
  "lux": 450.0,
  "occupancy": 1,
  "voltage": 220.0,
  "current": 6.2,
  "energy_kwh": 0.0227,
  "battery_level": 78.0
}
```

### Notes

1. `temperature` is kept for legacy compatibility.
2. `temperature_c` is the preferred name.
3. `energy_kwh` is energy for the sample interval.
4. `voltage` and `current` are optional (the backend logger defaults them to `0.0` if missing).
5. When `source` is `"group1_hardware"`, the data originated from the hw_bridge (real sensors). When absent, it is from the simulator.

## 4. ML Payload Contract (`room/ml/predictions`)

Published by `new_ML/new_prediction_api.py` via MQTT when a sensor message arrives on `room/sensors`.

```json
{
  "predicted_energy_wh": 45.2301,
  "upper_bound_energy_wh": 52.8745,
  "lower_bound_energy_wh": 37.5857,
  "predicted_energy_range_wh": [37.5857, 52.8745],
  "energy_unit": "Wh",
  "peak_demand": 2.4,
  "timestamp": "2026-05-24T17:00:00+00:00",
  "source": "fastapi-local-model"
}
```

### Field descriptions

| Field | Unit | Description |
|---|---|---|
| `predicted_energy_wh` | Wh | Hybrid prediction (TE-GRU + LightGBM with Bayesian adaptive weighting) |
| `upper_bound_energy_wh` | Wh | Upper confidence bound (z=1.5 × residual std) |
| `lower_bound_energy_wh` | Wh | Lower confidence bound |
| `predicted_energy_range_wh` | Wh | `[lower, upper]` bounds as an array |
| `energy_unit` | string | Always `"Wh"` |
| `peak_demand` | kW | Configurable threshold (default 2.4 kW) |
| `timestamp` | ISO 8601 | When the prediction was made |
| `source` | string | Always `"fastapi-local-model"` |

### Consumer lookup order

The rule engine resolves predicted energy from whichever key the ML payload provides, in this order:

1. `predicted_energy_kw` (legacy)
2. `predicted_energy_wh` (new_ML — current)
3. `predicted_energy_range` (legacy fallback)
4. `predicted_energy_kwh` (legacy fallback)

## 5. Averaged Data Payload (`room/data/averaged`)

Published every logger flush cycle:

```json
{
  "temperature": 31.8,
  "humidity": 58.9,
  "occupancy": 1,
  "voltage": 219.7,
  "current": 5.6,
  "battery_level": 77.3,
  "predicted_energy_range": 1.37,
  "peak_demand": 2.4,
  "timestamp": "2026-03-17T12:00:00+00:00"
}
```

## 6. Relay State Payload (`room/relays/state`)

This topic publishes three different types of payloads.

**A. Full Rule Evaluation Payload (every decision interval)**
```json
{
  "mode": "B",
  "relay_1": true,
  "relay_2": true,
  "relay_3": false,
  "auto": true,
  "battery_t_now": 77.3,
  "battery_t1": 77.9,
  "battery_t2": 78.4,
  "battery_lag_drop": 1.1,
  "battery_lag_interval_seconds": 30,
  "reason": "Condition 3 - Battery drop within threshold -> switch to Mode B",
  "timestamp": "2026-03-17T12:00:00+00:00"
}
```

| Field | Type | Notes |
|---|---|---|
| `mode` | string | `"A"`, `"B"`, `"C"`, or `"MANUAL"` (when individual relays are overridden) |
| `relay_1` | bool | Water heater state |
| `relay_2` | bool | HVAC / A.C. state |
| `relay_3` | bool | Freezer state |
| `auto` | bool | `true` = AI auto-managed, `false` = manual override active |
| `reason` | string | Human-readable explanation of why this mode was chosen |

**B. Lightweight Battery Lag Update (strictly every 30 seconds)**
```json
{
  "type": "battery_lag_update",
  "battery_t_now": 77.3,
  "battery_t1": 77.9,
  "battery_t2": 78.4,
  "timestamp": "2026-03-17T12:00:15+00:00"
}
```

**C. Manual Override Payload (published when dashboard sends override)**
```json
{
  "mode": "A",
  "relay_1": true,
  "relay_2": true,
  "relay_3": true,
  "auto": false,
  "reason": "Manual override → Mode A",
  "timestamp": "2026-05-24T17:00:00+00:00"
}
```

When `"auto": false`, the dashboard should show the "Manual Override Active" indicator. When `"auto": true`, the system is AI-managed.

## 6b. Manual Override Commands (`room/control/override`)

Published by the **dashboard** to the rule engine. The rule engine subscribes to this topic and responds immediately.

### Enable override — set a mode

Disables auto-management and switches to the specified mode. Relay states are determined by the standard mode mapping (A = all on, B = R1+R2 on, C = R1 only).

```json
{"auto": false, "mode": "A"}
```
```json
{"auto": false, "mode": "B"}
```
```json
{"auto": false, "mode": "C"}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `auto` | bool | ✅ | Must be `false` to enable override |
| `mode` | string | ✅ | `"A"`, `"B"`, or `"C"` |

### Enable override — set individual relays

Disables auto-management and sets each relay independently (ignoring mode presets). The mode field in `room/relays/state` will show `"MANUAL"`.

```json
{"auto": false, "relay_1": true, "relay_2": false, "relay_3": true}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `auto` | bool | ✅ | Must be `false` |
| `relay_1` | bool | optional | Water heater. Defaults to current state if omitted. |
| `relay_2` | bool | optional | HVAC / A.C. Defaults to current state if omitted. |
| `relay_3` | bool | optional | Freezer. Defaults to current state if omitted. |

### Re-enable auto management

Turns off manual override and lets the AI rule engine resume automatic decisions.

```json
{"auto": true}
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `auto` | bool | ✅ | Must be `true` to re-enable auto |

### Testing from terminal

```bash
# Switch to Mode A manually
mosquitto_pub -h localhost -t "room/control/override" -m '{"auto": false, "mode": "A"}'

# Toggle individual relays
mosquitto_pub -h localhost -t "room/control/override" -m '{"auto": false, "relay_1": true, "relay_2": false, "relay_3": true}'

# Turn auto back on
mosquitto_pub -h localhost -t "room/control/override" -m '{"auto": true}'
```

## 7. Rule Threshold Contract


The rule engine uses a 2-step decision hierarchy based on energy sufficiency and battery state.

### Energy threshold

1. `MODE_A_MAX_KWH=2.4` — Peak demand ceiling. Determines Step 1 (≥ threshold) vs Step 2 (< threshold).

### Battery lag thresholds (time-of-day dynamic)

The "stable" check compares the 3-time battery lag drop against a **dynamic threshold** based on solar availability:

| Profile | Hours | Threshold | Rationale |
|---------|-------|-----------|----------|
| **Daytime** | 11:00 AM – 3:59 PM | `MAX_BATTERY_DROP_PERCENT` (default 2%) | Solar is charging. A >2% drop = real overconsumption. |
| **Nighttime** | 4:00 PM – 10:59 AM | `MAX_BATTERY_DROP_NIGHT_PERCENT` (default 8%) | No solar. Normal drain shouldn't trigger Mode C. |

Configurable via:
- `SOLAR_HOUR_START` (default `11`)
- `SOLAR_HOUR_END` (default `16`, exclusive)

### Hardware actuation

The rule engine does **not** drive GPIO pins directly. It publishes the `relay_1`, `relay_2`, `relay_3` booleans to `room/relays/state`. An external **ESP32 microcontroller** subscribes to this topic and actuates the physical relay modules based on these values.

## 8. REST API Contract

Base URL: `http://<PI_IP>:8000/api/v1/`

1. `GET /sensors/`
2. `GET /sensors/latest/`
3. `GET /predictions/`
4. `GET /predictions/latest/`
5. `GET /relays/`
6. `GET /relays/current/`

## 9. ML HTTP Test Endpoints (testing only)

Base URL: `http://<PI_IP>:5000`

These endpoints are for manual testing only. Production predictions flow through MQTT.

| Method | Path | Description |
|---|---|---|
| `POST` | `/predict` | Send manual sensor values, get prediction back |
| `GET` | `/predict_next` | Step through CSV dataset, get next prediction |
| `GET` | `/csv_data` | Returns CSV file used by the ML test dashboard |
| `POST` | `/reset` | Resets ML internal CSV index for simulator sync |
| `GET` | `/` | Serves test_dashboard.html |

### POST /predict request body

```json
{
  "temperature_c": 28.0,
  "humidity": 60.0,
  "lux": 400.0,
  "occupancy": 1,
  "datetime_str": "2026-03-26T14:30"
}
```

`datetime_str` is optional; if omitted, the ML service uses current server time.

All fields have defaults, so you can send an empty `{}` to test with default values.

## 10. Dashboard Realtime Contract

The dashboard (`dashboard/index.html`) is MQTT-driven for realtime values.

### 9.1 Topics consumed by dashboard

1. `room/sensors` (primary realtime telemetry)
2. `room/data/averaged` (5-minute context values)
3. `room/ml/predictions` (predicted load)
4. `room/relays/state` (current mode and relay states)

### 9.2 Battery lag display behavior

1. The dashboard battery-lag display reads `battery_t_now`, `battery_t1`, and `battery_t2` from `room/relays/state`.
2. To ensure real-time responsiveness, the backend pushes a lightweight `type: "battery_lag_update"` message to this topic strictly every 30 seconds, entirely independent of the `DECISION_INTERVAL_MINUTES` cadence.
3. The dashboard ignores the missing `mode` field during these updates and safely updates the lag visualization.
