# ESP32 MQTT Relay Controller

Firmware for the ESP32 that subscribes to `room/relays/state` and drives three physical relay modules based on the rule engine's mode decisions.

## Architecture

```
Rule Engine (Pi)             ESP32
─────────────────           ─────────────────
evaluate_rules()            mqttCallback()
      │                           │
      ▼                           ▼
apply_mode("B")             parse JSON payload
      │                           │
      ▼                           ▼
Publish to MQTT ──────────▶ Drive GPIO pins
room/relays/state           relay_1 → Pin 26
                            relay_2 → Pin 27
                            relay_3 → Pin 14
```

## Dependencies

Install these via the **Arduino Library Manager** (Sketch → Include Library → Manage Libraries):

| Library | Author | Version | Purpose |
|---------|--------|---------|---------|
| **PubSubClient** | Nick O'Leary | 2.8+ | MQTT client |
| **ArduinoJson** | Benoît Blanchon | 7.x | JSON parsing |

The `WiFi.h` library is included automatically with the ESP32 board package.

## Board Setup (Arduino IDE)

1. **Install ESP32 board package:**
   - Go to File → Preferences
   - Add to "Additional Board Manager URLs":
     ```
     https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
     ```
   - Go to Tools → Board → Board Manager → search "esp32" → Install

2. **Select board:** Tools → Board → ESP32 Arduino → **ESP32 Dev Module**

3. **Select port:** Tools → Port → (your USB serial port)

## Configuration

Edit the `#define` macros at the top of `relay_controller.ino`:

```cpp
#define WIFI_SSID          "YOUR_WIFI_SSID"
#define WIFI_PASSWORD      "YOUR_WIFI_PASSWORD"
#define MQTT_SERVER        "192.168.1.100"    // Pi's IP address
#define MQTT_PORT          1883

#define RELAY_PIN_1        26    // Priority 1 — Critical loads
#define RELAY_PIN_2        27    // Priority 2 — Comfort loads
#define RELAY_PIN_3        14    // Priority 3 — Luxury loads
```

> **Important:** `MQTT_SERVER` must be the IP address of the Raspberry Pi running Mosquitto. Find it with `hostname -I` on the Pi.

## Wiring

### ESP32 → 3-Channel Relay Module

| ESP32 Pin | Relay Module | Load Priority |
|-----------|-------------|---------------|
| GPIO 26 | IN1 | P1 — Critical (always ON except full shutdown) |
| GPIO 27 | IN2 | P2 — Comfort (ON in Mode A and B) |
| GPIO 14 | IN3 | P3 — Luxury (ON in Mode A only) |
| 5V (VIN) | VCC | Power the relay module |
| GND | GND | Common ground |

### Pin selection rationale

GPIO 26, 27, and 14 are safe output pins on most ESP32 dev boards:
- They have no special boot-mode functions
- They are not used by the onboard flash
- They support digital output without restrictions

**Avoid these pins:** GPIO 0, 2, 5, 12, 15 (boot strapping), GPIO 6–11 (flash), GPIO 34–39 (input-only).

### Relay module wiring to loads

```
    ESP32                Relay Module             Electrical Load
  ┌────────┐           ┌────────────┐           ┌─────────────┐
  │ GPIO26 ├──────────▶│ IN1    COM1├───────────│ Live wire   │
  │ GPIO27 ├──────────▶│ IN2    COM2├───────────│ Live wire   │
  │ GPIO14 ├──────────▶│ IN3    COM3├───────────│ Live wire   │
  │   5V   ├──────────▶│ VCC       │           │             │
  │   GND  ├──────────▶│ GND       │           │             │
  └────────┘           └────────────┘           └─────────────┘
```

Use the **COM** (common) and **NO** (normally open) terminals on each relay channel. When the relay is activated (HIGH signal), the COM and NO terminals connect, completing the circuit.

## Expected MQTT Payload

The rule engine publishes to `room/relays/state`:

```json
{
    "mode": "B",
    "relay_1": true,
    "relay_2": true,
    "relay_3": false,
    "battery_t_now": 77.3,
    "battery_t1": 77.9,
    "battery_t2": 78.4,
    "battery_lag_drop": 1.1,
    "battery_lag_interval_seconds": 30,
    "reason": "Step 1 — Battery 77.3% >= 50%, lag stable → Mode B",
    "timestamp": "2026-05-03T21:00:00+00:00"
}
```

The firmware only reads `relay_1`, `relay_2`, `relay_3`. All other fields are ignored.

**Battery lag updates** (published every 30s) contain `"type": "battery_lag_update"` and are skipped automatically.

## Behaviour

| Scenario | ESP32 Action |
|----------|-------------|
| Normal operation | Drives relays per MQTT payload |
| WiFi drops | Keeps retrying every 5s. Relays stay in last known state. |
| MQTT disconnects | Forces **Mode C** (safe). Retries every 5s. |
| Boot / power cycle | Starts in **Mode C** (only critical load ON). |
| Malformed JSON | Logs error to Serial, ignores message. |
| Missing relay keys | Logs warning, ignores message. |

## Serial Monitor Output

Open Serial Monitor at **115200 baud** to see:

```
============================================
  ESP32 MQTT Relay Controller
  Smart Room Energy Management System
============================================
  Firmware compiled: May 03 2026 22:25:00
  Relay pins: P1=26, P2=27, P3=14
  MQTT topic: room/relays/state
============================================

[RELAY] GPIO pins initialized. Default: Mode C (safe).
[WIFI] Connecting to "MyNetwork" ...
..
[WIFI] Connected! IP: 192.168.1.42
[MQTT] Connecting to 192.168.1.100:1883 ...
[MQTT] Connected to broker.
[MQTT] Subscribed to: room/relays/state

[MQTT] Message on room/relays/state (245 bytes):
  {"mode":"B","relay_1":true,"relay_2":true,"relay_3":false,...}
[RELAY] Mode B applied:
  ┌─────────┬──────┬───────┐
  │  Relay  │ Pin  │ State │
  ├─────────┼──────┼───────┤
  │ Relay 1 │  26  │   ON  │  (Critical)
  │ Relay 2 │  27  │   ON  │  (Comfort)
  │ Relay 3 │  14  │  OFF  │  (Luxury)
  └─────────┴──────┴───────┘
```

## Uploading

1. Connect the ESP32 to your computer via USB
2. Open `relay_controller.ino` in Arduino IDE
3. Edit the configuration macros at the top
4. Click **Upload** (→ button)
5. Open Serial Monitor (Tools → Serial Monitor, 115200 baud)
6. Verify WiFi and MQTT connection in the output

## Testing Without Hardware

You can test the ESP32's MQTT subscription from any machine:

```bash
# Simulate a Mode A command
mosquitto_pub -t "room/relays/state" -m '{"mode":"A","relay_1":true,"relay_2":true,"relay_3":true,"reason":"test","timestamp":"2026-01-01T00:00:00Z"}'

# Simulate a Mode C command
mosquitto_pub -t "room/relays/state" -m '{"mode":"C","relay_1":true,"relay_2":false,"relay_3":false,"reason":"test","timestamp":"2026-01-01T00:00:00Z"}'

# Watch the ESP32's Serial Monitor for relay state changes
```
