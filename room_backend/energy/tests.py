from django.test import TestCase
from django.utils import timezone as tz
from datetime import timedelta
from rest_framework.test import APIClient
from .models import RelayState, MLPrediction

class EnergyAlignmentTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_csv_and_analytics_alignment(self):
        # 1. Create a series of RelayState decisions (every 3 minutes, starting at 12:00)
        # 2. Create the corresponding MLPredictions flushed at the next 5-minute intervals.
        #
        # Decision T1: 12:00:10 -> prediction 45.0 Wh is generated.
        # Decision T2: 12:03:10 -> prediction 55.0 Wh is generated.
        # Flush 1: 12:05:00 -> flushes predictions from 12:00:10 and 12:03:10.
        #                      Value: average of 45.0 and 55.0 = 50.0 Wh.
        #
        # Decision T3: 12:06:10 -> prediction 65.0 Wh is generated.
        # Decision T4: 12:09:10 -> prediction 75.0 Wh is generated.
        # Flush 2: 12:10:00 -> flushes predictions from 12:06:10 and 12:09:10.
        #                      Value: average of 65.0 and 75.0 = 70.0 Wh.
        
        base_time = tz.now().replace(hour=12, minute=0, second=0, microsecond=0)

        # Relay states
        r1 = RelayState.objects.create(
            mode="B",
            relay_1=1, relay_2=1, relay_3=0,
            temperature=25.0, humidity=50.0, lux=100.0, occupancy=2,
            energy_kw=1.0, battery_level=80.0, battery_voltage=24.0
        )
        RelayState.objects.filter(pk=r1.pk).update(timestamp=base_time + timedelta(seconds=10))
        
        r2 = RelayState.objects.create(
            mode="B",
            relay_1=1, relay_2=1, relay_3=0,
            temperature=25.5, humidity=51.0, lux=95.0, occupancy=2,
            energy_kw=1.1, battery_level=79.5, battery_voltage=24.0
        )
        RelayState.objects.filter(pk=r2.pk).update(timestamp=base_time + timedelta(minutes=3, seconds=10))
        
        r3 = RelayState.objects.create(
            mode="A",
            relay_1=1, relay_2=1, relay_3=1,
            temperature=26.0, humidity=52.0, lux=90.0, occupancy=3,
            energy_kw=1.2, battery_level=79.0, battery_voltage=23.9
        )
        RelayState.objects.filter(pk=r3.pk).update(timestamp=base_time + timedelta(minutes=6, seconds=10))
        
        r4 = RelayState.objects.create(
            mode="A",
            relay_1=1, relay_2=1, relay_3=1,
            temperature=26.5, humidity=53.0, lux=85.0, occupancy=3,
            energy_kw=1.3, battery_level=78.5, battery_voltage=23.9
        )
        RelayState.objects.filter(pk=r4.pk).update(timestamp=base_time + timedelta(minutes=9, seconds=10))

        # ML Predictions (flushed at 12:05:00 and 12:10:00)
        p1 = MLPrediction.objects.create(
            predicted_energy_wh=50.0,
            upper_bound_wh=60.0,
            lower_bound_wh=40.0
        )
        MLPrediction.objects.filter(pk=p1.pk).update(timestamp=base_time + timedelta(minutes=5))
        
        p2 = MLPrediction.objects.create(
            predicted_energy_wh=70.0,
            upper_bound_wh=80.0,
            lower_bound_wh=60.0
        )
        MLPrediction.objects.filter(pk=p2.pk).update(timestamp=base_time + timedelta(minutes=10))

        # Query CSV view
        # We pass start and end query parameters to bound the range
        import urllib.parse
        start_str = (base_time - timedelta(minutes=1)).isoformat()
        end_str = (base_time + timedelta(minutes=12)).isoformat()
        
        start_param = urllib.parse.quote(start_str)
        end_param = urllib.parse.quote(end_str)
        response = self.client.get(f"/api/v1/download/csv/?start={start_param}&end={end_param}")
        self.assertEqual(response.status_code, 200)
        
        # Parse CSV output
        content = response.content.decode("utf-8")
        lines = content.strip().split("\n")
        self.assertEqual(len(lines), 5)  # Header + 4 data rows
        
        # Check matching results
        # Rows should correspond to r1, r2, r3, r4 in order.
        # r1 and r2 (logged at 12:00:10 and 12:03:10) should match prediction p1 (50.0 Wh) flushed at 12:05:00.
        # r3 and r4 (logged at 12:06:10 and 12:09:10) should match prediction p2 (70.0 Wh) flushed at 12:10:00.
        
        # Row format: timestamp, temperature, humidity, lux, occupancy, real time energy, real time energy (5-min), predicted_energy_8lags, ...
        # Columns:
        # 0: timestamp
        # 1: temperature
        # ...
        # 7: predicted_energy_8lags
        # 8: predicted_energy_lower_8lags
        # 9: predicted_energy_upper_8lags
        
        row1 = lines[1].split(",")
        row2 = lines[2].split(",")
        row3 = lines[3].split(",")
        row4 = lines[4].split(",")
        
        # Check prediction Wh alignment
        self.assertEqual(float(row1[7]), 50.0)
        self.assertEqual(float(row2[7]), 50.0)
        self.assertEqual(float(row3[7]), 70.0)
        self.assertEqual(float(row4[7]), 70.0)

        # Check upper bounds
        self.assertEqual(float(row1[9]), 60.0)
        self.assertEqual(float(row2[9]), 60.0)
        self.assertEqual(float(row3[9]), 80.0)
        self.assertEqual(float(row4[9]), 80.0)

        # Query Analytics view
        response_analytics = self.client.get("/api/v1/analytics/?days=1")
        self.assertEqual(response_analytics.status_code, 200)
        data = response_analytics.json()
        
        r1_local = tz.localtime(base_time + timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
        r2_local = tz.localtime(base_time + timedelta(minutes=3, seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
        r3_local = tz.localtime(base_time + timedelta(minutes=6, seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
        r4_local = tz.localtime(base_time + timedelta(minutes=9, seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
        
        # We need to find our 4 records and verify their predictions
        # (Filter out any other records in DB if there are any from test seed)
        matched_data = [item for item in data if item["timestamp"] in (r1_local, r2_local, r3_local, r4_local)]
        self.assertEqual(len(matched_data), 4)
        
        # Sort matched_data by timestamp to ensure order
        matched_data.sort(key=lambda x: x["timestamp"])
        
        self.assertEqual(matched_data[0]["predicted_energy_8lags"], 50.0)
        self.assertEqual(matched_data[1]["predicted_energy_8lags"], 50.0)
        self.assertEqual(matched_data[2]["predicted_energy_8lags"], 70.0)
        self.assertEqual(matched_data[3]["predicted_energy_8lags"], 70.0)
