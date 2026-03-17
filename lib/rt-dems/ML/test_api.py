import requests
import json

# The local URL where your FastAPI server is listening
API_URL = "http://127.0.0.1:5000/predict"

# Mock sensor data matching exactly what the ESP32 and SQLite would provide
mock_payload = {
    "timestamp": "2026-03-15 14:00:00",
    "temperature_c": 32.5,
    "humidity": 60.0,
    "lux": 450.0,
    "occupancy": 1,
    "power_w": 1364.0,
    "lag_1h": 1.10,
    "lag_2h": 1.05,
    "lag_3h": 0.95,
    "lag_24h": 1.20,
}

print(f"Sending test data to {API_URL}...\n")
print("[+]Payload:", json.dumps(mock_payload, indent=2))

try:
    # Fire the POST request to the local server
    response = requests.post(API_URL, json=mock_payload)

    # Check if the server accepted it (Status Code 200 means OK)
    if response.status_code == 200:
        print("\n[+] SUCCESS! HYBRIDIZED MMODEL response:")

        # Parse the JSON response
        model_answer = response.json()
        print(json.dumps(model_answer, indent=4))

        print(
            f"\nPredicted power for rule logic: "
            f"{model_answer['predicted_power_kw']} kW "
            f"({model_answer['predicted_power_w']} W)."
        )
    else:
        print(
            f"\n❌ FAILED. Server returned status code: "
            f"{response.status_code}"
        )
        print("Error details:", response.text)

except requests.exceptions.ConnectionError:
    print("\n⚠️ CONNECTION ERROR: Could not find the API.")
    print(
        "Did you forget to start the FastAPI server? "
        "Run 'python app.py' in another terminal first!"
    )
