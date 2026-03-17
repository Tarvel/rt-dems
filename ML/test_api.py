import requests
import json

# The local URL where your FastAPI server is listening
API_URL = "http://127.0.0.1:5000/predict"

# Mock sensor data matching exactly what the ESP32 and SQLite would provide
mock_payload = {
    "timestamp": "2026-03-21 03:00:00",
  "temperature_c":23.34,
  "humidity": 23.3,
  "lux": 0.0,
  "occupancy": 0,
  "lag_1h": 1.1,
  "lag_2h": 1.05,
  "lag_3h": 0.95,
    "lag_24h": 1.2
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
    
    """
    Sending test data to http://127.0.0.1:5000/predict...

[+]Payload: {
  "timestamp": "2026-03-21 03:00:00",
  "temperature_c":23.34,
  "humidity": 23.3,
  "lux": 0.0,
  "occupancy": 0,
    "energy_kwh": 0.0227,
  "lag_1h": 1.1,
  "lag_2h": 1.05,
  "lag_3h": 0.95,
  "lag_24h": 1.2
}

[+] SUCCESS! HYBRIDIZED MMODEL response:
{
    "mean_prediction_kw": 4.436,
    "upper_bound_kw": 4.586,
    "predicted_energy_kw": 4.586,
    "predicted_energy_kwh": 0.2293,
    "predicted_energy_range": 4.586,
    "peak_demand": 2.4,
    "timestamp": "2026-03-17T12:15:56.520527+00:00",
    "source": "hybridized-model"
}

2020-03-21 03:00:00,0.3853,23.34,93.3,0.0,0
    """
    