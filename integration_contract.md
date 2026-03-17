# Integration Contract - MQTT Topics and JSON Payloads

This contract defines active MQTT topics, ownership, and payload schemas.

Broker: `<PI_IP>:1883`
WebSocket listener: `<PI_IP>:9001`
QoS: `1`

## 1. Topic Map

| Topic | Publisher | Subscribers | Description |
|---|---|---|---|
| `room/sensors` | Hardware or simulator | ML service, logger, rule engine, dashboard | Raw telemetry stream |
| `room/ml/predictions` | ML service | logger, rule engine, dashboard | Model predictions |
| `room/data/averaged` | logger | dashboard | 5-minute averaged values |
| `room/relays/state` | rule engine | dashboard | Current mode and relay states |

## 2. Sensor Payload Contract (`room/sensors`)

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

1. `temperature` is retained for legacy compatibility.
2. `temperature_c` is the preferred canonical name.
3. `energy_kwh` is energy for the sample interval (kWh).

## 3. ML Payload Contract (`room/ml/predictions`)

Published by `ML/app.py` for both MQTT-driven and HTTP-driven prediction paths.

```json
{
  "predicted_energy_kw": 1.224,
  "upper_bound_energy_kw": 1.374,
  "predicted_energy_kwh": 0.0612,
  "upper_bound_energy_kwh": 0.0687,
  "predicted_energy_range": 1.374,
  "peak_demand": 2.4,
  "timestamp": "2026-03-17T11:55:00+00:00",
  "source": "fastapi-local-model"
}
```

### Units

1. `predicted_energy_kwh` is energy for the decision interval.
2. `predicted_energy_kw` is the hourly rate used to derive kWh.
3. `predicted_energy_range` is compatibility output in kW.
4. `peak_demand` is compatibility threshold in kW.

## 4. Averaged Data Payload (`room/data/averaged`)

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

## 5. Relay State Payload (`room/relays/state`)

```json
{
  "mode": "B",
  "relay_1": true,
  "relay_2": true,
  "relay_3": false,
  "battery_lag_values": [77.3, 77.9, 78.4],
  "battery_lag_interval_seconds": 30,
  "reason": "Condition 3 - Battery drop within threshold -> switch to Mode B",
  "timestamp": "2026-03-17T12:00:00+00:00"
}
```

`battery_lag_values` is ordered as `[T-now, T-1, T-2]` and updates at rule evaluation cadence.

## 6. Rule Threshold Contract (kWh)

Default limits:

1. `MODE_A_MAX_KWH=2.4`
2. `MODE_B_MAX_KWH=1.4`
3. `MODE_C_MAX_KWH=0.8`

Rule engine references these energy limits for configuration.

## 7. REST API Contract

Base URL: `http://<PI_IP>:8000/api/v1/`

1. `GET /sensors/`
2. `GET /sensors/latest/`
3. `GET /predictions/`
4. `GET /predictions/latest/`
5. `GET /relays/`
6. `GET /relays/current/`

## 8. Dashboard Realtime Contract

The dashboard (`dashboard/index.html`) is MQTT-driven for realtime values.

### 8.1 Topics consumed by dashboard

1. `room/sensors` (primary realtime telemetry)
2. `room/data/averaged` (5-minute context values)
3. `room/ml/predictions` (predicted load in kW/W)
4. `room/relays/state` (current mode and relay states)

### 8.2 Battery lag display behavior

1. The dashboard battery-lag display reads `battery_lag_values` from `room/relays/state`.
2. This makes lag updates follow `RULE_EVAL_INTERVAL_SECONDS` (rule-engine cadence).
3. The values are visualized as `T-now`, `T-1`, and `T-2` and are not fetched from REST API.
