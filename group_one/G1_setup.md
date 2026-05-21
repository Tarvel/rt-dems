# Group 1 Hardware Integration Guide

This document explains how Group 1's sensor hardware (NANO + UNO) connects to our MQTT-based energy management system.

## Architecture

```
                      Group 1 (your existing setup)
                      ─────────────────────────────
                      NANO ──serial──▶ ESP32 ──▶ Firebase  (existing, unchanged)
                                         │
                                         ├──MQTT──▶ room/hardware/nano ─┐
                      UNO  ──serial──▶ ESP32 ──▶ Firebase  (existing)   │
                                         │                              │
                                         └──MQTT──▶ room/hardware/uno ──┤
                                                                        │
                      Our system (Raspberry Pi)                         │
                      ─────────────────────────                         ▼
                                                                  hw_bridge.py
                                                                  (normalise)
                                                                        │
                                                                        ▼
                                                                  room/sensors
                                                                        │
                                                   ┌────────┬──────────┼──────────┐
                                                   ▼        ▼          ▼          ▼
                                             mqtt_logger rule_engine ML service dashboard
```

Your existing Firebase code stays exactly as-is. You are only **adding** MQTT
publishing alongside it — same data, extra destination.

---

## What You Need To Install (Arduino Library Manager)

Go to **Sketch → Include Library → Manage Libraries** and install:

| Library | Author | Purpose |
|---------|--------|---------|
| **PubSubClient** | Nick O'Leary | MQTT client (you probably already have this) |

`WiFi.h` is already included with the ESP32 board package.

---

## Complete Code To Add To Your ESP32 Firmware

> **Important:** Do NOT replace your existing code. You are only adding new
> functions and calling them **after** your existing Firebase push. Your
> Firebase logic remains untouched.

### Step 1 — Add these includes and config at the TOP of your `.ino` file

Add these right below your existing `#include` lines and WiFi/Firebase
config. If you already have `#include <WiFi.h>`, don't add it again.

```cpp
// =====================================================================
//  GROUP 3 MQTT INTEGRATION — Add these below your existing includes
// =====================================================================
#include <PubSubClient.h>         // MQTT client (install via Library Manager)

// ── MQTT broker config (Group 3's Raspberry Pi) ──
// >>>>> CHANGE THIS to the Pi's actual IP on your shared network <<<<<
#define MQTT_BROKER_IP   "192.168.137.1"
#define MQTT_BROKER_PORT 1883

// Topics we publish to (Group 3's hw_bridge.py listens on these)
#define TOPIC_NANO       "room/hardware/nano"
#define TOPIC_UNO        "room/hardware/uno"

// ── MQTT client objects ──
WiFiClient       mqttWifiClient;
PubSubClient     mqttClient(mqttWifiClient);

// Tracks when we last tried to reconnect (non-blocking)
unsigned long lastMqttReconnectAttempt = 0;
```

### Step 2 — Add these functions BEFORE your `setup()` function

Paste these anywhere above `setup()`. They handle connecting to the
broker and publishing your data. They are completely independent of
your Firebase code.

```cpp
// =====================================================================
//  GROUP 3 MQTT — Connection + Publishing Functions
// =====================================================================

/**
 * Non-blocking MQTT reconnection.
 * Called from loop() — will NOT freeze your main code.
 * Retries every 5 seconds if the broker is unreachable.
 */
void mqttReconnect() {
    unsigned long now = millis();
    // Only try once every 5 seconds
    if (now - lastMqttReconnectAttempt < 5000) return;
    lastMqttReconnectAttempt = now;

    Serial.print("[MQTT] Connecting to broker at ");
    Serial.print(MQTT_BROKER_IP);
    Serial.print(":");
    Serial.print(MQTT_BROKER_PORT);
    Serial.print(" ... ");

    if (mqttClient.connect("group1-esp32")) {
        Serial.println("connected!");
    } else {
        Serial.print("failed (rc=");
        Serial.print(mqttClient.state());
        Serial.println("). Will retry in 5s.");
    }
}

/**
 * Publish NANO environmental sensor data to Group 3's MQTT broker.
 *
 * Call this with the SAME variables you already pass to sendDataToESP32()
 * or your Firebase push. The JSON structure matches your existing payload
 * exactly — no changes to your data format.
 *
 * THIS DOES NOT AFFECT YOUR FIREBASE UPLOAD.
 */
void mqttPublishNano(float temp, float hum, float volt, float curr,
                     float pwr, float eng, float lx, int occ, int radar) {
    if (!mqttClient.connected()) return;  // Skip silently if not connected

    String json = "{\n";
    json += "  \"temperature\": " + String(temp, 1) + ",\n";
    json += "  \"humidity\": " + String(hum, 1) + ",\n";
    json += "  \"voltage\": " + String(volt, 1) + ",\n";
    json += "  \"current\": " + String(curr, 2) + ",\n";
    json += "  \"power\": " + String(pwr, 1) + ",\n";
    json += "  \"energy\": " + String(eng, 3) + ",\n";
    json += "  \"lux\": " + String(lx, 2) + ",\n";
    json += "  \"ultrasonic_occupancy\": " + String(occ) + ",\n";
    json += "  \"radar_motion\": " + String(radar) + "\n";
    json += "}";

    if (mqttClient.publish(TOPIC_NANO, json.c_str())) {
        Serial.println("[MQTT] Published NANO data to " TOPIC_NANO);
    } else {
        Serial.println("[MQTT] Failed to publish NANO data");
    }
}

/**
 * Publish UNO battery/SoC data to Group 3's MQTT broker.
 *
 * Call this wherever you currently read and send battery data.
 *
 * THIS DOES NOT AFFECT YOUR FIREBASE UPLOAD.
 */
void mqttPublishUno(float batteryVoltage, float soc) {
    if (!mqttClient.connected()) return;  // Skip silently if not connected

    String json = "{\n";
    json += "  \"node\": \"DC\",\n";
    json += "  \"battery_voltage\": " + String(batteryVoltage, 1) + ",\n";
    json += "  \"soc\": " + String(soc, 0) + "\n";
    json += "}";

    if (mqttClient.publish(TOPIC_UNO, json.c_str())) {
        Serial.println("[MQTT] Published UNO data to " TOPIC_UNO);
    } else {
        Serial.println("[MQTT] Failed to publish UNO data");
    }
}
```

### Step 3 — Add two lines inside your `setup()` function

Find your `setup()` function and add these two lines **after** your
WiFi connection is established (after `WiFi.begin()` succeeds):

```cpp
void setup() {
    // ... your existing setup code (Serial, WiFi, Firebase, etc.) ...

    // ── GROUP 3 MQTT — Add these two lines after WiFi is connected ──
    mqttClient.setServer(MQTT_BROKER_IP, MQTT_BROKER_PORT);
    mqttClient.setBufferSize(512);  // Our payloads can be up to ~300 bytes

    // ... rest of your existing setup ...
}
```

### Step 4 — Add two lines inside your `loop()` function

Find your `loop()` function and add these two lines **at the top**, before
your existing sensor reading code:

```cpp
void loop() {
    // ── GROUP 3 MQTT — Add these two lines at the top of loop() ──
    if (!mqttClient.connected()) mqttReconnect();
    mqttClient.loop();

    // ... your existing loop code continues below, unchanged ...
}
```

### Step 5 — Call the publish functions where you already send data

Find the place in your code where you call `sendDataToESP32()` (or wherever
you push NANO data to Firebase) and add **one line** right after it:

```cpp
// YOUR EXISTING CODE (unchanged):
sendDataToESP32(temp, hum, volt, curr, pwr, eng, lx, occ, radar);

// ── GROUP 3 MQTT — Add this line right after ──
mqttPublishNano(temp, hum, volt, curr, pwr, eng, lx, occ, radar);
```

Do the same wherever you send UNO battery data:

```cpp
// YOUR EXISTING CODE (unchanged):
// ... Firebase push for battery ...

// ── GROUP 3 MQTT — Add this line right after ──
mqttPublishUno(trueVoltage, soc);
```

That's it. **Five additions, zero changes to your existing code.**

---

## Quick Checklist

```
✅ 1. Install PubSubClient library (Library Manager)
✅ 2. Add #include and config defines at top of file
✅ 3. Add the three functions (mqttReconnect, mqttPublishNano, mqttPublishUno)
✅ 4. Add mqttClient.setServer() in setup()
✅ 5. Add mqttClient.connected() check + mqttClient.loop() in loop()
✅ 6. Add mqttPublishNano() call after your existing sendDataToESP32()
✅ 7. Add mqttPublishUno() call after your existing battery push
✅ 8. Change MQTT_BROKER_IP to the Pi's actual IP address
```

---

## What Happens If The Broker Is Down?

**Nothing breaks.** The MQTT functions are designed to fail silently:

- `mqttReconnect()` retries every 5 seconds, non-blocking — your
  `loop()` keeps running normally.
- `mqttPublishNano()` / `mqttPublishUno()` check `mqttClient.connected()`
  first — if the broker is unreachable, they `return` immediately.
- Your Firebase uploads continue as normal regardless of MQTT status.

---

## What Happens On Our Side

1. Our `hw_bridge.py` worker subscribes to `room/hardware/nano` and `room/hardware/uno`
2. When a NANO message arrives, it merges it with the latest UNO battery reading
3. It renames fields to match our internal schema (e.g. `soc` → `battery_level`)
4. It publishes the merged payload to `room/sensors`
5. All our services (logger, rule engine, ML, dashboard) consume it as normal

**You don't need to change your JSON structure at all.** The bridge handles the mapping.

---

## Broker Details (for reference)

| Setting | Value |
|---------|-------|
| Broker IP | `<PI_IP>` — ask Group 3 for the current IP |
| Port | `1883` |
| Protocol | MQTT v3.1.1 (standard) |
| QoS | `0` or `1` — either works |
| Authentication | None (anonymous allowed on LAN) |
| NANO topic | `room/hardware/nano` |
| UNO topic | `room/hardware/uno` |

---

## Field Mapping Reference

| Your field | Our internal field | Notes |
|---|---|---|
| `temperature` | `temperature`, `temperature_c` | Duplicated for compatibility |
| `humidity` | `humidity` | Direct |
| `voltage` | `voltage` | Direct |
| `current` | `current` | Direct |
| `power` | `power_w` | Renamed, passed through |
| `energy` | `energy_kw` | Renamed |
| `lux` | `lux` | Direct |
| `ultrasonic_occupancy` | `occupancy` | Renamed |
| `radar_motion` | `radar_motion` | Passed through |
| `soc` | `battery_level` | Renamed |
| `battery_voltage` | `battery_voltage` | Passed through |

---

## Testing Without Hardware

To verify the MQTT bridge is working from any machine on the network:

```bash
# Simulate a NANO reading
mosquitto_pub -h <PI_IP> -t "room/hardware/nano" -m \
  '{"temperature":28.5,"humidity":65.0,"voltage":220.4,"current":1.45,"power":319.5,"energy":12.345,"lux":450.0,"ultrasonic_occupancy":1,"radar_motion":0}'

# Simulate a UNO battery reading
mosquitto_pub -h <PI_IP> -t "room/hardware/uno" -m \
  '{"node":"DC","battery_voltage":24.5,"soc":80}'

# Watch the normalised output on room/sensors
mosquitto_sub -h <PI_IP> -t "room/sensors" -v
```