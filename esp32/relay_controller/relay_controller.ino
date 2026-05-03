/*
 * ============================================================================
 *  ESP32 MQTT Relay Controller
 *  Smart Room Energy Management System
 * ============================================================================
 *
 *  This firmware connects to a WiFi network and an MQTT broker, subscribes
 *  to the `room/relays/state` topic, and drives three relay GPIO pins
 *  based on the `relay_1`, `relay_2`, `relay_3` boolean values in the
 *  incoming JSON payload.
 *
 *  Architecture:
 *    Rule Engine (Pi) ──MQTT──▶ room/relays/state ──MQTT──▶ This ESP32
 *
 *  The rule engine publishes payloads like:
 *    {
 *      "mode": "B",
 *      "relay_1": true,
 *      "relay_2": true,
 *      "relay_3": false,
 *      "reason": "...",
 *      "timestamp": "..."
 *    }
 *
 *  Dependencies (install via Arduino Library Manager):
 *    1. PubSubClient  by Nick O'Leary  (MQTT client)
 *    2. ArduinoJson   by Benoît Blanchon  (JSON parser, v7+)
 *
 *  Board:
 *    ESP32 Dev Module (or any ESP32 variant)
 *    Select under Tools > Board > ESP32 Arduino > "ESP32 Dev Module"
 *
 * ============================================================================
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ============================================================================
//  USER CONFIGURATION — Edit these values for your local setup
// ============================================================================

// WiFi credentials
#define WIFI_SSID          "YOUR_WIFI_SSID"
#define WIFI_PASSWORD      "YOUR_WIFI_PASSWORD"

// MQTT broker (the Raspberry Pi's IP address on your local network)
#define MQTT_SERVER        "192.168.1.100"
#define MQTT_PORT          1883
#define MQTT_CLIENT_ID     "esp32-relay-controller"

// GPIO pins connected to the relay module inputs (active HIGH)
// Change these to match your wiring.
#define RELAY_PIN_1        26    // Priority 1 — Critical loads
#define RELAY_PIN_2        27    // Priority 2 — Comfort loads
#define RELAY_PIN_3        14    // Priority 3 — Luxury loads

// MQTT topic published by the rule engine
#define MQTT_TOPIC         "room/relays/state"

// Status LED (built-in on most ESP32 dev boards)
#define STATUS_LED_PIN     2

// Reconnection intervals (milliseconds)
#define WIFI_RETRY_MS      5000
#define MQTT_RETRY_MS      5000

// ============================================================================
//  GLOBALS
// ============================================================================

WiFiClient   wifiClient;
PubSubClient mqttClient(wifiClient);

unsigned long lastWifiAttempt = 0;
unsigned long lastMqttAttempt = 0;

// Track current relay states for status reporting
bool relayState1 = false;
bool relayState2 = false;
bool relayState3 = false;

// Track current mode letter for serial output
String currentMode = "C";

// ============================================================================
//  FORWARD DECLARATIONS
// ============================================================================

void setupRelays();
void setRelay(uint8_t pin, bool state);
void applyRelayStates(bool r1, bool r2, bool r3);
void applySafeMode();
void connectWifi();
void connectMqtt();
void mqttCallback(char* topic, byte* payload, unsigned int length);
void printRelayStatus();

// ============================================================================
//  RELAY CONTROL
// ============================================================================

/**
 * Configure all relay GPIO pins as outputs and default to safe state (Mode C).
 */
void setupRelays() {
    pinMode(RELAY_PIN_1, OUTPUT);
    pinMode(RELAY_PIN_2, OUTPUT);
    pinMode(RELAY_PIN_3, OUTPUT);

    // Boot into Mode C (baseline — only critical load ON)
    applySafeMode();
    Serial.println("[RELAY] GPIO pins initialized. Default: Mode C (safe).");
}

/**
 * Drive a single relay pin HIGH (ON) or LOW (OFF).
 */
void setRelay(uint8_t pin, bool state) {
    digitalWrite(pin, state ? HIGH : LOW);
}

/**
 * Apply a full set of relay states and update tracking variables.
 */
void applyRelayStates(bool r1, bool r2, bool r3) {
    setRelay(RELAY_PIN_1, r1);
    setRelay(RELAY_PIN_2, r2);
    setRelay(RELAY_PIN_3, r3);

    relayState1 = r1;
    relayState2 = r2;
    relayState3 = r3;
}

/**
 * Force Mode C (safest mode) — only critical load stays ON.
 * Called on boot and when MQTT connection is lost.
 */
void applySafeMode() {
    applyRelayStates(true, false, false);
    currentMode = "C";
}

// ============================================================================
//  WIFI CONNECTION (non-blocking)
// ============================================================================

/**
 * Attempt to connect to WiFi. Non-blocking — returns immediately if already
 * connected or if the retry interval hasn't elapsed.
 */
void connectWifi() {
    if (WiFi.status() == WL_CONNECTED) return;

    unsigned long now = millis();
    if (now - lastWifiAttempt < WIFI_RETRY_MS) return;
    lastWifiAttempt = now;

    Serial.printf("[WIFI] Connecting to \"%s\" ...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    // Brief blocking wait (max 10s) for initial connection only
    int retries = 20;
    while (WiFi.status() != WL_CONNECTED && retries-- > 0) {
        delay(500);
        Serial.print(".");
    }
    Serial.println();

    if (WiFi.status() == WL_CONNECTED) {
        Serial.printf("[WIFI] Connected! IP: %s\n", WiFi.localIP().toString().c_str());
        digitalWrite(STATUS_LED_PIN, HIGH);
    } else {
        Serial.println("[WIFI] Connection failed. Will retry...");
        digitalWrite(STATUS_LED_PIN, LOW);
    }
}

// ============================================================================
//  MQTT CONNECTION (non-blocking)
// ============================================================================

/**
 * Attempt to connect to the MQTT broker. Non-blocking — returns immediately
 * if already connected or if the retry interval hasn't elapsed.
 *
 * On successful connection, subscribes to the relay state topic.
 * On disconnection, forces Mode C for safety.
 */
void connectMqtt() {
    if (mqttClient.connected()) return;
    if (WiFi.status() != WL_CONNECTED) return;

    unsigned long now = millis();
    if (now - lastMqttAttempt < MQTT_RETRY_MS) return;
    lastMqttAttempt = now;

    Serial.printf("[MQTT] Connecting to %s:%d ...\n", MQTT_SERVER, MQTT_PORT);

    if (mqttClient.connect(MQTT_CLIENT_ID)) {
        Serial.println("[MQTT] Connected to broker.");

        // Subscribe to the relay state topic with QoS 1
        mqttClient.subscribe(MQTT_TOPIC, 1);
        Serial.printf("[MQTT] Subscribed to: %s\n", MQTT_TOPIC);

        // Publish a "connected" status so the dashboard/rule engine can see us
        String statusPayload = "{\"status\":\"online\",\"client\":\"" MQTT_CLIENT_ID "\"}";
        mqttClient.publish("room/relays/esp32_status", statusPayload.c_str(), true);

    } else {
        Serial.printf("[MQTT] Connection failed (rc=%d). Will retry in %ds...\n",
                       mqttClient.state(), MQTT_RETRY_MS / 1000);

        // Safety: drop to Mode C while disconnected
        applySafeMode();
        Serial.println("[RELAY] Forced Mode C (MQTT disconnected — safe fallback).");
    }
}

// ============================================================================
//  MQTT MESSAGE HANDLER
// ============================================================================

/**
 * Callback fired when a message arrives on a subscribed topic.
 *
 * Expected JSON payload (from rule_engine.py):
 *   {
 *     "mode": "B",
 *     "relay_1": true,
 *     "relay_2": true,
 *     "relay_3": false,
 *     "reason": "...",
 *     "timestamp": "..."
 *   }
 *
 * We only care about relay_1, relay_2, relay_3 (booleans).
 * The "type": "battery_lag_update" messages are silently ignored
 * (they don't contain relay fields).
 */
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    // ── Guard: only process our topic ──
    if (String(topic) != MQTT_TOPIC) return;

    // ── Copy payload into a null-terminated string ──
    char json[512];
    unsigned int copyLen = (length < sizeof(json) - 1) ? length : sizeof(json) - 1;
    memcpy(json, payload, copyLen);
    json[copyLen] = '\0';

    Serial.printf("\n[MQTT] Message on %s (%u bytes):\n  %s\n", topic, length, json);

    // ── Parse JSON ──
    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, json);

    if (err) {
        Serial.printf("[JSON] Parse error: %s — ignoring message.\n", err.c_str());
        return;
    }

    // ── Skip battery_lag_update messages (no relay data) ──
    if (doc.containsKey("type")) {
        const char* msgType = doc["type"];
        if (msgType && strcmp(msgType, "battery_lag_update") == 0) {
            Serial.println("[MQTT] Battery lag update — no relay action needed.");
            return;
        }
    }

    // ── Extract relay booleans ──
    if (!doc.containsKey("relay_1") || !doc.containsKey("relay_2") || !doc.containsKey("relay_3")) {
        Serial.println("[JSON] Missing relay_1/relay_2/relay_3 keys — ignoring.");
        return;
    }

    bool r1 = doc["relay_1"].as<bool>();
    bool r2 = doc["relay_2"].as<bool>();
    bool r3 = doc["relay_3"].as<bool>();

    // ── Extract mode (optional, for logging) ──
    if (doc.containsKey("mode")) {
        currentMode = doc["mode"].as<String>();
    }

    // ── Apply relay states ──
    applyRelayStates(r1, r2, r3);

    Serial.printf("[RELAY] Mode %s applied:\n", currentMode.c_str());
    printRelayStatus();
}

// ============================================================================
//  STATUS DISPLAY
// ============================================================================

/**
 * Print a formatted table of current relay states to Serial.
 */
void printRelayStatus() {
    Serial.println("  ┌─────────┬──────┬───────┐");
    Serial.println("  │  Relay  │ Pin  │ State │");
    Serial.println("  ├─────────┼──────┼───────┤");
    Serial.printf( "  │ Relay 1 │  %2d  │  %s  │  (Critical)\n",
                   RELAY_PIN_1, relayState1 ? " ON" : "OFF");
    Serial.printf( "  │ Relay 2 │  %2d  │  %s  │  (Comfort)\n",
                   RELAY_PIN_2, relayState2 ? " ON" : "OFF");
    Serial.printf( "  │ Relay 3 │  %2d  │  %s  │  (Luxury)\n",
                   RELAY_PIN_3, relayState3 ? " ON" : "OFF");
    Serial.println("  └─────────┴──────┴───────┘");
}

// ============================================================================
//  ARDUINO SETUP
// ============================================================================

void setup() {
    // ── Serial monitor ──
    Serial.begin(115200);
    delay(100);

    Serial.println();
    Serial.println("============================================");
    Serial.println("  ESP32 MQTT Relay Controller");
    Serial.println("  Smart Room Energy Management System");
    Serial.println("============================================");
    Serial.printf("  Firmware compiled: %s %s\n", __DATE__, __TIME__);
    Serial.printf("  Relay pins: P1=%d, P2=%d, P3=%d\n",
                   RELAY_PIN_1, RELAY_PIN_2, RELAY_PIN_3);
    Serial.printf("  MQTT topic: %s\n", MQTT_TOPIC);
    Serial.println("============================================\n");

    // ── Status LED ──
    pinMode(STATUS_LED_PIN, OUTPUT);
    digitalWrite(STATUS_LED_PIN, LOW);

    // ── Relay GPIOs ──
    setupRelays();

    // ── MQTT client config ──
    mqttClient.setServer(MQTT_SERVER, MQTT_PORT);
    mqttClient.setCallback(mqttCallback);

    // PubSubClient default buffer is 256 bytes — our payloads can be larger
    mqttClient.setBufferSize(1024);

    // ── Initial WiFi connection ──
    connectWifi();
}

// ============================================================================
//  ARDUINO MAIN LOOP
// ============================================================================

void loop() {
    // ── Maintain WiFi connection ──
    if (WiFi.status() != WL_CONNECTED) {
        connectWifi();
        return;  // Skip MQTT processing until WiFi is back
    }

    // ── Maintain MQTT connection ──
    if (!mqttClient.connected()) {
        connectMqtt();
    }

    // ── Process incoming MQTT messages ──
    mqttClient.loop();
}
