# Group 1 ↔ Group 3 — Hardware Integration Documentation

This document describes the MQTT integration between Group 1's sensor hardware and Group 3's energy management system. It covers exactly what was changed, why, and how the data flows end-to-end.

---

## 1. Overview

Group 1's ESP32 gateway receives serial data from two Arduino boards (NANO and UNO), then forwards it to **two destinations**:

1. **Firebase** (Group 1's database) — unchanged
2. **Our MQTT broker** (Group 3's Raspberry Pi) — added

```
                            Group 1 Hardware
                ┌──────────────────────────────────────┐
                │                                      │
                │   NANO ──serial──▶ ESP32 ──▶ Firebase (PATCH + POST)
                │   (AC sensors)      │                │
                │                     ├──MQTT──▶ room/hardware/nano
                │                     │                │
                │   UNO  ──serial──▶ ESP32 ──▶ Firebase│
                │   (DC battery)      │                │
                │                     └──MQTT──▶ room/hardware/uno
                └──────────────────────────────────────┘
                                      │
                               MQTT (port 1883)
                                      │
                ┌─────────────────────────────────────────────────┐
                │              Group 3 Raspberry Pi               │
                │                                                 │
                │   hw_bridge.py                                  │
                │     ├─ subscribes to room/hardware/nano         │
                │     ├─ subscribes to room/hardware/uno          │
                │     ├─ merges + renames fields                  │
                │     └─ republishes to room/sensors              │
                │                    │                            │
                │        ┌───────────┼───────────┐                │
                │        ▼           ▼           ▼                │
                │   mqtt_logger  rule_engine  ML service          │
                │   (database)   (relay mode) (predictions)       │
                │                    │                            │
                │                    ▼                            │
                │          room/relays/state                      │
                │                    │                            │
                │                    ▼                            │
                │           ESP32 Relay Controller                │
                │           (switches physical loads)             │
                └─────────────────────────────────────────────────┘
```

---

## 2. What Was Changed in `ESPbroadcast.ino`

The updated file is at `docs/ESPbroadcast.ino`. Three targeted changes were made. Everything else — WiFi, Firebase, serial reading, UNO packet assembler — is **completely untouched**.

### Change 1: Topic routing (the critical fix)

**Problem:** The original code published both NANO and UNO data to a single topic (`rtdems/telemetry`). Our backend needs them on **separate topics** so `hw_bridge.py` can distinguish environmental data from battery data and apply the correct field mapping.

**Before:**
```cpp
// Both NANO and UNO data went to the same topic
mqttClient.publish("rtdems/telemetry", incomingJson.c_str());
```

**After:**
```cpp
// Route to the correct topic based on which board sent the data
const char* topic;
if (sourceBoard == "NANO") {
    topic = TOPIC_NANO;   // "room/hardware/nano"
} else {
    topic = TOPIC_UNO;    // "room/hardware/uno"
}
mqttClient.publish(topic, incomingJson.c_str());
```

This works because `broadcastData()` already receives a `sourceBoard` parameter (`"NANO"` or `"UNO"`) — we just use it to pick the right topic.

### Change 2: Reconnection rate-limiting

**Problem:** The original `maintainMQTT()` called `mqttClient.connect()` every single `loop()` cycle when the broker was unreachable. This floods the serial monitor with `"Failed"` messages and wastes CPU.

**Before:**
```cpp
void maintainMQTT() {
    if (WiFi.status() == WL_CONNECTED && !mqttClient.connected()) {
        // Tries to connect EVERY loop() iteration
        mqttClient.connect(clientId.c_str());
    }
    mqttClient.loop();
}
```

**After:**
```cpp
void maintainMQTT() {
    if (WiFi.status() != WL_CONNECTED) return;
    if (mqttClient.connected()) {
        mqttClient.loop();
        return;
    }

    // Only try once every 5 seconds
    unsigned long now = millis();
    if (now - lastMqttAttempt < MQTT_RETRY_MS) return;
    lastMqttAttempt = now;

    mqttClient.connect(clientId.c_str());
}
```

### Change 3: Buffer size

**Problem:** PubSubClient's default buffer is 256 bytes. The NANO payload is ~250 bytes — right at the limit. Large payloads get **silently truncated** (no error, just missing data).

**Added in `setup()`:**
```cpp
mqttClient.setBufferSize(512);
```

---

## 3. What Group 1 Needs To Do

1. Open the updated `ESPbroadcast.ino`
2. Verify the broker IP on **line 14**: `const char* mqtt_server = "192.168.9.53";`
   - Change this if the Pi's IP address has changed
3. Upload to the ESP32
4. Open Serial Monitor (115200 baud) and confirm you see:
   ```
   >>> WI-FI CONNECTED!
   >>> ESP32 GATEWAY: DUAL-NODE + MQTT ONLINE
   Connecting to Raspberry Pi (MQTT)... Connected!
   ```

**If the Pi is not running**, you'll see `"Failed, rc=-2. Will retry in 5s."` — this is normal. Firebase continues working. The ESP32 will auto-connect as soon as the Pi comes online.

---

## 4. What Group 3 Needs Running

For the integration to work end-to-end, these services must be running on the Pi:

| Service | Command | Purpose |
|---------|---------|---------|
| **Mosquitto** | `mosquitto -c systemd/mosquitto.conf -v` | MQTT broker (receives Group 1's data) |
| **hw_bridge.py** | `python workers/hw_bridge.py` | Translates Group 1 field names → our schema |
| **mqtt_logger.py** | `python workers/mqtt_logger.py` | Buffers and stores 5-min averages to SQLite |
| **rule_engine.py** | `python workers/rule_engine.py` | Evaluates rules, publishes relay decisions |
| **ML service** | `python ML/test_prediction_api.py` | Runs predictions on incoming sensor data |

Or just run `./startall.sh` which starts everything.

---

## 5. MQTT Topic Map

| Topic | Publisher | Subscriber | Payload |
|---|---|---|---|
| `room/hardware/nano` | Group 1 ESP32 | `hw_bridge.py` | NANO environmental JSON |
| `room/hardware/uno` | Group 1 ESP32 | `hw_bridge.py` | UNO battery JSON |
| `room/sensors` | `hw_bridge.py` | logger, rule engine, ML, dashboard | Normalised sensor JSON |
| `room/relays/state` | `rule_engine.py` | ESP32 relay controller, dashboard | Mode + relay booleans |

---

## 6. Payload Formats

### 6a. NANO → `room/hardware/nano` (published by Group 1)

```json
{
  "temperature": 28.5,
  "humidity": 65.0,
  "voltage": 220.4,
  "current": 1.45,
  "power": 319.5,
  "energy": 12.345,
  "lux": 450.0,
  "ultrasonic_occupancy": 1,
  "radar_motion": 0
}
```

### 6b. UNO → `room/hardware/uno` (published by Group 1)

```json
{
  "node": "DC",
  "battery_voltage": 24.5,
  "soc": 80
}
```

### 6c. Normalised → `room/sensors` (published by hw_bridge.py)

```json
{
  "timestamp": "2026-05-21T21:00:00+00:00",
  "temperature_c": 28.5,
  "temperature": 28.5,
  "humidity": 65.0,
  "lux": 450.0,
  "occupancy": 1,
  "battery_level": 80.0,
  "voltage": 220.4,
  "current": 1.45,
  "energy_kw": 12.345,
  "power_w": 319.5,
  "radar_motion": 0,
  "battery_voltage": 24.5,
  "source": "group1_hardware"
}
```

---

## 7. Field Mapping (hw_bridge.py)

The bridge renames Group 1's fields to match our internal schema. Downstream services (logger, rule engine, ML) only see the normalised `room/sensors` payload.

| Group 1 field | → Our field | Transform |
|---|---|---|
| `temperature` | `temperature`, `temperature_c` | Duplicated for compat |
| `humidity` | `humidity` | Direct |
| `voltage` | `voltage` | Direct |
| `current` | `current` | Direct |
| `power` | `power_w` | Renamed |
| `energy` | `energy_kw` | Renamed |
| `lux` | `lux` | Direct |
| `ultrasonic_occupancy` | `occupancy` | Renamed + int cast |
| `radar_motion` | `radar_motion` | Pass-through |
| `soc` (UNO) | `battery_level` | Renamed |
| `battery_voltage` (UNO) | `battery_voltage` | Pass-through |
| — | `source` | Added: `"group1_hardware"` |
| — | `timestamp` | Added: UTC ISO 8601 |

---

## 8. Verifying The Integration

### From any terminal on the same network:

```bash
# Watch what Group 1's ESP32 publishes (raw)
mosquitto_sub -h <PI_IP> -t "room/hardware/#" -v

# Watch what hw_bridge.py outputs (normalised)
mosquitto_sub -h <PI_IP> -t "room/sensors" -v
```

### Simulate Group 1 data without their hardware:

```bash
# Fake a NANO reading
mosquitto_pub -h <PI_IP> -t "room/hardware/nano" -m \
  '{"temperature":28.5,"humidity":65.0,"voltage":220.4,"current":1.45,"power":319.5,"energy":12.345,"lux":450.0,"ultrasonic_occupancy":1,"radar_motion":0}'

# Fake a UNO battery reading
mosquitto_pub -h <PI_IP> -t "room/hardware/uno" -m \
  '{"node":"DC","battery_voltage":24.5,"soc":80}'
```

### On the ESP32 Serial Monitor (115200 baud):

```
[RECEIVED FROM NANO]:
{"temperature": 28.5, "humidity": 65.0, ...}
Broadcasting to Pi (room/hardware/nano)... Success
Updating Telemetry Live... Success
Pushing to Telemetry Logs... Success
-------------------------

[RECEIVED FROM UNO]:
{"node":"DC","battery_voltage":24.5,"soc":80}
Broadcasting to Pi (room/hardware/uno)... Success
Updating Telemetry Live... Success
Pushing to Telemetry Logs... Success
-------------------------
```

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Failed, rc=-2. Will retry in 5s.` | Pi/Mosquitto not running or wrong IP | Start Mosquitto on Pi, verify `mqtt_server` IP in the `.ino` |
| `Skipping MQTT: Not connected to Pi.` | ESP32 hasn't connected yet | Normal on startup — will auto-connect within 5s |
| MQTT connected but `room/sensors` empty | `hw_bridge.py` not running | Start it: `python workers/hw_bridge.py` |
| Firebase still works, MQTT doesn't | Network issue between ESP32 and Pi | Ensure both are on the same WiFi network |
| Data appears on `room/hardware/*` but not `room/sensors` | Bridge crashed or wrong topic | Check bridge logs, verify topics match |