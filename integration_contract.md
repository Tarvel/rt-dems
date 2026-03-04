# Integration Contract — MQTT Topics & JSON Payloads

This document defines the exact MQTT topics and JSON payloads used by every team. All teams **MUST** adhere to these schemas. The broker runs on the Raspberry Pi at `<PI_IP>:1883`.

---

## Topic Map

| # | Topic                    | Publisher        | Subscriber(s)              | QoS | Description                              |
|---|--------------------------|------------------|----------------------------|-----|------------------------------------------|
| 1 | `room/sensors`           | Hardware (Grp A) | Logger, Rule Engine        | 1   | Raw sensor readings (high frequency)     |
| 2 | `room/ml/predictions`    | ML Team          | Logger, Rule Engine        | 1   | Energy predictions                       |
| 3 | `room/data/averaged`     | Logger           | Frontend                   | 1   | 5-minute averaged sensor + ML data       |
| 4 | `room/relays/state`      | Rule Engine      | Frontend                   | 1   | Current relay mode & reason              |

---

## Payload Schemas

### 1. `room/sensors` — Published by Hardware Team

The hardware team publishes raw sensor data. Frequency: as often as desired (the logger buffers everything and averages every 5 minutes).

```json
{
  "temperature": 29.5,
  "humidity": 62.3,
  "occupancy": 1,
  "voltage": 220.1,
  "current": 4.8,
  "battery_level": 73.5
}
```

| Field           | Type    | Unit   | Description                                |
|-----------------|---------|--------|--------------------------------------------|
| `temperature`   | `float` | °C     | Room temperature                           |
| `humidity`      | `float` | %      | Relative humidity                          |
| `occupancy`     | `int`   | —      | `1` = room occupied, `0` = room empty      |
| `voltage`       | `float` | V      | Mains / supply voltage                     |
| `current`       | `float` | A      | Current draw                               |
| `battery_level` | `float` | %      | Battery state of charge (0–100)            |

---

### 2. `room/ml/predictions` — Published by ML Team

The ML team publishes energy forecasts. Frequency: whenever a new prediction is generated.

```json
{
  "predicted_energy_range": 5.2,
  "peak_demand": 4.0
}
```

| Field                    | Type    | Unit | Description                                |
|--------------------------|---------|------|--------------------------------------------|
| `predicted_energy_range` | `float` | kWh  | Predicted energy consumption for the period|
| `peak_demand`            | `float` | kWh  | Peak demand threshold                      |

---

### 3. `room/data/averaged` — Published by MQTT Logger

Published by the backend every 5 minutes after averaging. The frontend subscribes to this for live dashboard updates.

```json
{
  "temperature": 29.2,
  "humidity": 61.8,
  "occupancy": 1,
  "voltage": 220.0,
  "current": 4.7,
  "battery_level": 73.0,
  "predicted_energy_range": 5.1,
  "peak_demand": 4.0,
  "timestamp": "2026-03-03T19:30:00+00:00"
}
```

> Contains all sensor fields averaged + latest ML fields + ISO 8601 timestamp.

---

### 4. `room/relays/state` — Published by Rule Engine

Published whenever the rule engine makes a relay decision (every 5 minutes).

```json
{
  "mode": "B",
  "relay_1": true,
  "relay_2": true,
  "relay_3": false,
  "reason": "Phase 3 — Temperature Bias: 29.5°C > 28°C and battery 73.5% > 40% → Mode B",
  "timestamp": "2026-03-03T19:30:00+00:00"
}
```

| Field     | Type     | Description                                         |
|-----------|----------|-----------------------------------------------------|
| `mode`    | `string` | `"A"`, `"B"`, or `"C"`                              |
| `relay_1` | `bool`   | Priority 1 relay (always ON unless system off)       |
| `relay_2` | `bool`   | Priority 2 relay                                     |
| `relay_3` | `bool`   | Priority 3 relay                                     |
| `reason`  | `string` | Human-readable explanation of the decision           |
| `timestamp` | `string` | ISO 8601 UTC timestamp                            |

---

## Relay Mode Reference

| Mode | Name           | Relay 1 (P1) | Relay 2 (P2) | Relay 3 (P3) | Scenario                  |
|------|----------------|:---:|:---:|:---:|----------------------------------------|
| A    | Peak Demand    | ON  | ON  | ON  | Full power — enough energy & battery   |
| B    | Average Load   | ON  | ON  | OFF | Fans OK, heavy loads off               |
| C    | Baseline Load  | ON  | OFF | OFF | Survival mode — essentials only        |

---

## REST API Endpoints

Base URL: `http://<PI_IP>:8000/api/v1/`

| Method | Endpoint               | Description                            |
|--------|------------------------|----------------------------------------|
| GET    | `/api/v1/sensors/`          | Paginated 5-min sensor logs       |
| GET    | `/api/v1/sensors/latest/`   | Latest single sensor reading      |
| GET    | `/api/v1/predictions/`      | Paginated ML prediction history   |
| GET    | `/api/v1/predictions/latest/`| Latest ML prediction             |
| GET    | `/api/v1/relays/`           | Paginated relay decision history  |
| GET    | `/api/v1/relays/current/`   | Current relay mode & state        |

All responses are JSON. Paginated endpoints support `?page=N` (50 items per page).
