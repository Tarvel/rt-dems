#include <WiFi.h>
#include <HTTPClient.h>
#include <PubSubClient.h> // --- ADDED: For talking to the Raspberry Pi

// --- 1. YOUR NETWORK CREDENTIALS ---
const char* ssid = "MTN_4G_1114DA"; // MUST MATCH THE RASPBERRY PI'S NETWORK
const char* password = "260ABB62";

// --- 2. UPDATED FIREBASE PATHS ---
String liveUrl = "https://daq-system-rig-default-rtdb.firebaseio.com/telemetry/live.json";
String logsUrl = "https://daq-system-rig-default-rtdb.firebaseio.com/telemetry/logs.json";

// --- 3. MQTT SETTINGS (Group 3's Raspberry Pi) ---
const char* mqtt_server = "192.168.9.53";  // <-- CHANGE to Pi's IP if it changes
const int mqtt_port = 1883;
WiFiClient espClient;
PubSubClient mqttClient(espClient);

// MQTT topics that Group 3's hw_bridge.py listens on
#define TOPIC_NANO  "room/hardware/nano"
#define TOPIC_UNO   "room/hardware/uno"

// Rate-limit reconnection attempts (milliseconds)
unsigned long lastMqttAttempt = 0;
const unsigned long MQTT_RETRY_MS = 5000;

// --- Hardware Bridge Pins ---
// NANO (AC Data)
#define RX_FROM_NANO 16
#define TX_TO_NANO 17

// UNO (DC Data)
#define RX_FROM_UNO 25
#define TX_TO_UNO 26

// Global variables for the stabilized Uno packet assembler
String unoBuffer = "";
bool unoCapture = false;

void setup() {
  Serial.begin(115200);
  
  // Start listening to the Arduino Nano
  Serial2.begin(9600, SERIAL_8N1, RX_FROM_NANO, TX_TO_NANO);

  // Start listening to the Arduino Uno
  Serial1.begin(9600, SERIAL_8N1, RX_FROM_UNO, TX_TO_UNO);
  
  // Start connecting to the Wi-Fi router
  Serial.println();
  Serial.print("Connecting to Wi-Fi: ");
  Serial.println(ssid);
  WiFi.begin(ssid, password);
  
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  Serial.println("\n>>> WI-FI CONNECTED!");

  // --- Tell the ESP32 where the Raspberry Pi broker is ---
  mqttClient.setServer(mqtt_server, mqtt_port);
  mqttClient.setBufferSize(512);  // Payloads can be ~300 bytes

  Serial.println(">>> ESP32 GATEWAY: DUAL-NODE + MQTT ONLINE");
  Serial.println("=========================================");
}

// --- MQTT Background Connector ---
// Keeps the connection to the Pi alive without freezing your board.
// Only retries every 5 seconds to avoid spamming when the Pi is offline.
void maintainMQTT() {
  if (WiFi.status() != WL_CONNECTED) return;
  if (mqttClient.connected()) {
    mqttClient.loop();
    return;
  }

  // Rate-limit: only try once every 5 seconds
  unsigned long now = millis();
  if (now - lastMqttAttempt < MQTT_RETRY_MS) return;
  lastMqttAttempt = now;

  Serial.print("Connecting to Raspberry Pi (MQTT)... ");
  String clientId = "ESP32Gateway-" + String(random(0xffff), HEX);

  if (mqttClient.connect(clientId.c_str())) {
    Serial.println("Connected!");
  } else {
    Serial.print("Failed, rc=");
    Serial.print(mqttClient.state());
    Serial.println(". Will retry in 5s.");
  }
}

// --- CLOUD HELPER FUNCTION ---
// Delivers the clean, stabilized JSON string to both the Pi and Firebase
void broadcastData(String incomingJson, String sourceBoard) {
  Serial.print("\n[RECEIVED FROM ");
  Serial.print(sourceBoard);
  Serial.println("]:");
  Serial.println(incomingJson);

  // --- TASK 1: SEND TO RASPBERRY PI (via MQTT) ---
  if (mqttClient.connected()) {
    // Route to the correct topic based on which board sent the data
    const char* topic;
    if (sourceBoard == "NANO") {
      topic = TOPIC_NANO;   // "room/hardware/nano"
    } else {
      topic = TOPIC_UNO;    // "room/hardware/uno"
    }

    Serial.print("Broadcasting to Pi (");
    Serial.print(topic);
    Serial.print(")... ");
    if (mqttClient.publish(topic, incomingJson.c_str())) {
        Serial.println("Success");
    } else {
        Serial.println("Failed");
    }
  } else {
    Serial.println("Skipping MQTT: Not connected to Pi.");
  }

  // --- TASK 2 (UNTOUCHED): SEND TO FIREBASE ---
  if(WiFi.status() == WL_CONNECTED){
    HTTPClient http;

    Serial.print("Updating Telemetry Live... ");
    http.begin(liveUrl);
    http.addHeader("Content-Type", "application/json");
    
    int liveResponseCode = http.sendRequest("PATCH", incomingJson);
    if(liveResponseCode > 0) Serial.println("Success");
    else Serial.printf("Failed, error: %s\n", http.errorToString(liveResponseCode).c_str());
    http.end(); 

    Serial.print("Pushing to Telemetry Logs... ");
    http.begin(logsUrl);
    http.addHeader("Content-Type", "application/json");
    
    int logResponseCode = http.POST(incomingJson);
    if(logResponseCode > 0) Serial.println("Success");
    else Serial.printf("Failed, error: %s\n", http.errorToString(logResponseCode).c_str());
    http.end(); 
    
  } else {
    Serial.println("Error: Wi-Fi Disconnected!");
  }
  Serial.println("-------------------------");
}

void loop() {
  // --- Keep MQTT alive in the background ---
  maintainMQTT();

  // --- 1. UNTOUCHED: CHECK NANO (AC DATA) ---
  if (Serial2.available()) {
    String nanoJson = Serial2.readStringUntil('}');
    nanoJson += "}"; 

    while(Serial2.available()) {
      Serial2.read(); 
    }
    
    broadcastData(nanoJson, "NANO");
  }

  // --- 2. CORRECTED & STABILIZED: CHECK UNO (DC DATA) ---
  while (Serial1.available()) {
    char inChar = (char)Serial1.read();
    
    if (inChar == '<') {
      unoBuffer = "";
      unoCapture = true;
      continue;
    }
    
    if (inChar == '>') {
      unoCapture = false;
      unoBuffer.trim();
      if (unoBuffer.length() > 0) {
        // Sends the perfectly framed, typo-free string to BOTH locations
        broadcastData(unoBuffer, "UNO");
      }
      break;
    }
    
    if (unoCapture) {
      unoBuffer += inChar;
    }
  }
}
