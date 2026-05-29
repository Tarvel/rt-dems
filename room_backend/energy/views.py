"""
API views for the Smart Room Energy Management System.

All endpoints are GET-only. The frontend team consumes these to display
historical 5-minute data and the current relay state.
"""

from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters

from .models import SensorLog, MLPrediction, RelayState
from .serializers import (
    SensorLogSerializer,
    MLPredictionSerializer,
    RelayStateSerializer,
)


# ── Sensor Logs ─────────────────────────────────────────────────────────────

class SensorLogListView(generics.ListAPIView):
    """Paginated list of 5-minute averaged sensor readings (newest first).

    Query params:
      • ?page=N            — pagination
      • ?ordering=timestamp — sort ascending
      • ?search=...        — (not very useful here, but available)
    """

    queryset = SensorLog.objects.all()
    serializer_class = SensorLogSerializer
    filter_backends = [filters.OrderingFilter]
    ordering_fields = ["timestamp", "temperature", "battery_level"]
    ordering = ["-timestamp"]


class LatestSensorView(APIView):
    """Return the single most recent sensor log entry."""

    def get(self, request):
        entry = SensorLog.objects.order_by("-timestamp").first()
        if entry is None:
            return Response({"detail": "No sensor data recorded yet."}, status=404)
        return Response(SensorLogSerializer(entry).data)


# ── ML Predictions ──────────────────────────────────────────────────────────

class MLPredictionListView(generics.ListAPIView):
    """Paginated list of ML predictions (newest first)."""

    queryset = MLPrediction.objects.all()
    serializer_class = MLPredictionSerializer
    ordering = ["-timestamp"]


class LatestMLPredictionView(APIView):
    """Return the single most recent ML prediction."""

    def get(self, request):
        entry = MLPrediction.objects.order_by("-timestamp").first()
        if entry is None:
            return Response({"detail": "No ML predictions recorded yet."}, status=404)
        return Response(MLPredictionSerializer(entry).data)


# ── Relay State ─────────────────────────────────────────────────────────────

class RelayStateListView(generics.ListAPIView):
    """Paginated audit trail of relay-mode decisions (newest first)."""

    queryset = RelayState.objects.all()
    serializer_class = RelayStateSerializer
    ordering = ["-timestamp"]


class CurrentRelayStateView(APIView):
    """Return the current (most recent) relay state."""

    def get(self, request):
        entry = RelayState.objects.order_by("-timestamp").first()
        if entry is None:
            return Response({"detail": "No relay decisions recorded yet."}, status=404)
        return Response(RelayStateSerializer(entry).data)


# ── CSV Download ────────────────────────────────────────────────────────────

import csv
from datetime import datetime, timedelta
from django.http import HttpResponse
from django.utils import timezone as tz


class CSVDownloadView(APIView):
    """Download historical data as CSV.

    Query params:
      • ?start=2026-05-28T00:00   — start of range (ISO 8601)
      • ?end=2026-05-29T23:59     — end of range (ISO 8601)
      • ?days=7                   — alternative: last N days (ignored if start/end given)

    Returns a CSV with columns:
      Timestamp, Avg Temp (°C), Avg Humidity (%), Avg Lux, Avg Occupancy,
      Battery (%), Predicted Energy (Wh), Upper Bound (Wh), Lower Bound (Wh),
      Mode, R1, R2, R3, Reason
    """

    def get(self, request):
        # Parse date range
        start_str = request.query_params.get("start")
        end_str = request.query_params.get("end")
        days = request.query_params.get("days")

        now = tz.now()

        if start_str and end_str:
            try:
                start = datetime.fromisoformat(start_str)
                end = datetime.fromisoformat(end_str)
                if tz.is_naive(start):
                    start = tz.make_aware(start)
                if tz.is_naive(end):
                    end = tz.make_aware(end)
            except ValueError:
                return Response(
                    {"error": "Invalid date format. Use ISO 8601 (e.g. 2026-05-28T00:00)"},
                    status=400,
                )
        elif days:
            try:
                start = now - timedelta(days=int(days))
                end = now
            except ValueError:
                return Response({"error": "days must be an integer"}, status=400)
        else:
            # Default: last 24 hours
            start = now - timedelta(days=1)
            end = now

        # Query all three tables for the time range
        sensors = list(
            SensorLog.objects.filter(timestamp__gte=start, timestamp__lte=end)
            .order_by("timestamp")
            .values(
                "timestamp", "temperature", "humidity", "lux",
                "occupancy", "battery_level",
            )
        )
        predictions = list(
            MLPrediction.objects.filter(timestamp__gte=start, timestamp__lte=end)
            .order_by("timestamp")
            .values(
                "timestamp", "predicted_energy_wh",
                "upper_bound_wh", "lower_bound_wh",
            )
        )
        relays = list(
            RelayState.objects.filter(timestamp__gte=start, timestamp__lte=end)
            .order_by("timestamp")
            .values(
                "timestamp", "mode", "relay_1", "relay_2", "relay_3", "reason",
            )
        )

        # Build rows by aligning on relay decisions (one row per decision)
        # Each decision row gets the closest sensor and prediction data
        rows = []

        for relay in relays:
            rt = relay["timestamp"]

            # Find closest sensor reading (within 10 minutes)
            closest_sensor = None
            for s in sensors:
                if abs((s["timestamp"] - rt).total_seconds()) <= 600:
                    if closest_sensor is None or abs((s["timestamp"] - rt).total_seconds()) < abs((closest_sensor["timestamp"] - rt).total_seconds()):
                        closest_sensor = s

            # Find closest prediction (within 10 minutes)
            closest_pred = None
            for p in predictions:
                if abs((p["timestamp"] - rt).total_seconds()) <= 600:
                    if closest_pred is None or abs((p["timestamp"] - rt).total_seconds()) < abs((closest_pred["timestamp"] - rt).total_seconds()):
                        closest_pred = p

            rows.append({
                "Timestamp": rt.strftime("%Y-%m-%d %H:%M:%S"),
                "Avg Temp (°C)": closest_sensor["temperature"] if closest_sensor else "",
                "Avg Humidity (%)": closest_sensor["humidity"] if closest_sensor else "",
                "Avg Lux": closest_sensor["lux"] if closest_sensor else "",
                "Avg Occupancy": closest_sensor["occupancy"] if closest_sensor else "",
                "Battery (%)": closest_sensor["battery_level"] if closest_sensor else "",
                "Predicted Energy (Wh)": closest_pred["predicted_energy_wh"] if closest_pred else "",
                "Upper Bound (Wh)": closest_pred["upper_bound_wh"] if closest_pred else "",
                "Lower Bound (Wh)": closest_pred["lower_bound_wh"] if closest_pred else "",
                "Mode": relay["mode"],
                "R1": "ON" if relay["relay_1"] else "OFF",
                "R2": "ON" if relay["relay_2"] else "OFF",
                "R3": "ON" if relay["relay_3"] else "OFF",
                "Reason": relay["reason"],
            })

        # Generate CSV response
        response = HttpResponse(content_type="text/csv")
        filename = f"smartroom_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        if rows:
            writer = csv.DictWriter(response, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        else:
            writer = csv.writer(response)
            writer.writerow(["No data found for the specified time range."])

        return response
