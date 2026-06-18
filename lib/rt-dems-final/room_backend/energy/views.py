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
      • ?start=2026-05-28T00:00   — start of range (ISO 8601, or "today")
      • ?end=2026-05-29T23:59     — end of range (ISO 8601)
      • ?days=7                   — alternative: last N days (ignored if start/end given)
      • ?type=sensors             — "sensors", "predictions", "relays", or "all" (default)

    If start is "today", it resolves to the current date at 00:00.
    If only start is given without end, end defaults to now.
    If only end is given without start, start defaults to 7 days before end.

    Returns a CSV with sensor readings, ML predictions, and relay decisions
    aligned by timestamp.
    """

    def get(self, request):
        start_str = request.query_params.get("start")
        end_str = request.query_params.get("end")
        days = request.query_params.get("days")
        data_type = request.query_params.get("type", "all")

        now = tz.now()

        # Parse start/end
        start, end = None, None

        if start_str:
            if start_str.lower() == "today":
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                try:
                    start = datetime.fromisoformat(start_str)
                    if tz.is_naive(start):
                        start = tz.make_aware(start)
                except ValueError:
                    return Response(
                        {"error": "Invalid start date. Use ISO 8601 or 'today'."},
                        status=400,
                    )

        if end_str:
            if end_str.lower() == "today":
                end = now
            else:
                try:
                    end = datetime.fromisoformat(end_str)
                    if tz.is_naive(end):
                        end = tz.make_aware(end)
                except ValueError:
                    return Response(
                        {"error": "Invalid end date. Use ISO 8601 or 'today'."},
                        status=400,
                    )

        # Apply defaults
        if start and not end:
            end = now
        elif end and not start:
            start = end - timedelta(days=7)
        elif not start and not end:
            if days:
                try:
                    start = now - timedelta(days=int(days))
                    end = now
                except ValueError:
                    return Response({"error": "days must be an integer"}, status=400)
            else:
                start = now - timedelta(days=1)
                end = now

        # Ensure start <= end (swap if reversed)
        if start > end:
            start, end = end, start

        # Query data
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

        # Build lookup dicts for predictions and relays (keyed by minute)
        def _minute_key(ts):
            return ts.strftime("%Y-%m-%d %H:%M")

        pred_by_minute = {}
        for p in predictions:
            pred_by_minute[_minute_key(p["timestamp"])] = p

        relay_by_minute = {}
        for r in relays:
            relay_by_minute[_minute_key(r["timestamp"])] = r

        # Build rows — one per sensor reading, enriched with prediction/relay
        fieldnames = [
            "Timestamp", "Temperature (°C)", "Humidity (%)", "Lux",
            "Occupancy", "Battery (%)",
            "Predicted Energy (Wh)", "Upper Bound (Wh)", "Lower Bound (Wh)",
            "Mode", "R1", "R2", "R3", "Reason",
        ]

        rows = []
        last_relay = None

        for s in sensors:
            ts_key = _minute_key(s["timestamp"])
            pred = pred_by_minute.get(ts_key)
            relay = relay_by_minute.get(ts_key, last_relay)
            if relay:
                last_relay = relay

            rows.append({
                "Timestamp": tz.localtime(s["timestamp"]).strftime("%Y-%m-%d %H:%M:%S") if s.get("timestamp") and tz.is_aware(s["timestamp"]) else s["timestamp"].strftime("%Y-%m-%d %H:%M:%S"),
                "Temperature (°C)": round(s["temperature"], 2) if s["temperature"] is not None else "",
                "Humidity (%)": round(s["humidity"], 2) if s["humidity"] is not None else "",
                "Lux": round(s["lux"], 2) if s["lux"] is not None else "",
                "Occupancy": s["occupancy"] if s["occupancy"] is not None else "",
                "Battery (%)": round(s["battery_level"], 1) if s["battery_level"] is not None else "",
                "Predicted Energy (Wh)": round(pred["predicted_energy_wh"], 4) if pred else "",
                "Upper Bound (Wh)": round(pred["upper_bound_wh"], 4) if pred else "",
                "Lower Bound (Wh)": round(pred["lower_bound_wh"], 4) if pred else "",
                "Mode": relay["mode"] if relay else "",
                "R1": ("ON" if relay["relay_1"] else "OFF") if relay else "",
                "R2": ("ON" if relay["relay_2"] else "OFF") if relay else "",
                "R3": ("ON" if relay["relay_3"] else "OFF") if relay else "",
                "Reason": relay["reason"] if relay else "",
            })

        # Generate CSV response
        response = HttpResponse(content_type="text/csv")
        filename = f"smartroom_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Access-Control-Expose-Headers"] = "Content-Disposition"

        if rows:
            writer = csv.DictWriter(response, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        else:
            writer = csv.writer(response)
            writer.writerow(["No data found for the specified time range."])

        return response
