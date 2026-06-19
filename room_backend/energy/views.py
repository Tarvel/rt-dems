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
from datetime import timedelta
from django.utils import timezone as tz

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

    serializer_class = RelayStateSerializer
    ordering = ["-timestamp"]

    def get_queryset(self):
        queryset = RelayState.objects.all()
        days = self.request.query_params.get("days")
        if days:
            try:
                days_int = int(days)
                start_date = tz.now() - timedelta(days=days_int)
                queryset = queryset.filter(timestamp__gte=start_date)
            except ValueError:
                pass
        return queryset

    def paginate_queryset(self, queryset):
        # Disable pagination if filtering by days (analytics) or explicitly requested
        if self.request.query_params.get("paginate", "").lower() == "false":
            return None
        if self.request.query_params.get("days"):
            return None
        return super().paginate_queryset(queryset)


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
                    # If date-only format (e.g., YYYY-MM-DD), make it inclusive of the entire day
                    if len(end_str.strip()) == 10:
                        end = end.replace(hour=23, minute=59, second=59, microsecond=999999)
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

        # Query data — RelayState is now the primary source for sensor
        # snapshots (mqtt_logger no longer populates SensorLog).
        relays = list(
            RelayState.objects.filter(timestamp__gte=start, timestamp__lte=end)
            .order_by("timestamp")
            .values(
                "timestamp", "mode", "relay_1", "relay_2", "relay_3", "reason",
                "temperature", "humidity", "lux", "occupancy",
                "energy_kw", "battery_level", "battery_voltage",
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

        # Build lookup dict for predictions (keyed by minute)
        def _minute_key(ts):
            return ts.strftime("%Y-%m-%d %H:%M")

        pred_by_minute = {}
        for p in predictions:
            pred_by_minute[_minute_key(p["timestamp"])] = p

        # Build rows — one per relay decision (which contains the sensor
        # snapshot), enriched with the closest ML prediction
        fieldnames = [
            "timestamp", "temperature", "humidity", "lux", "occupancy",
            "real time energy", "real time energy (5-min)", "predicted_energy_8lags", 
            "predicted_energy_lower_8lags", "predicted_energy_upper_8lags",
            "Battery Voltage", "Battery Percentage", "System Mode A,B,C",
        ]

        rows = []
        for r in relays:
            ts_key = _minute_key(r["timestamp"])
            pred = pred_by_minute.get(ts_key)

            rows.append({
                "timestamp": tz.localtime(r["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                "temperature": round(r["temperature"], 2) if r["temperature"] is not None else "",
                "humidity": round(r["humidity"], 2) if r["humidity"] is not None else "",
                "lux": round(r["lux"], 2) if r["lux"] is not None else "",
                "occupancy": r["occupancy"] if r["occupancy"] is not None else "",
                "real time energy": round(r["energy_kw"], 4) if r["energy_kw"] is not None else "",
                "real time energy (5-min)": round(r["energy_kw"] * 5, 4) if r["energy_kw"] is not None else "",
                "predicted_energy_8lags": round(pred["predicted_energy_wh"], 4) if pred else "",
                "predicted_energy_lower_8lags": round(pred["lower_bound_wh"], 4) if pred else "",
                "predicted_energy_upper_8lags": round(pred["upper_bound_wh"], 4) if pred else "",
                "Battery Voltage": round(r["battery_voltage"], 2) if r["battery_voltage"] is not None else "",
                "Battery Percentage": round(r["battery_level"], 1) if r["battery_level"] is not None else "",
                "System Mode A,B,C": r["mode"] if r["mode"] else "",
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


class AnalyticsView(APIView):
    """Retrieve historical sensor readings, relay decisions, and ML predictions 
    aligned by timestamp in JSON format for charting.

    Query params:
      • ?days=N  — number of days of history to retrieve (default: 7)
    """

    def get(self, request):
        days = request.query_params.get("days", "7")
        try:
            days_int = int(days)
        except ValueError:
            days_int = 7

        now = tz.now()
        start = now - timedelta(days=days_int)

        # Query RelayState (primary sensor snapshot source)
        relays = list(
            RelayState.objects.filter(timestamp__gte=start)
            .order_by("timestamp")
            .values(
                "timestamp", "mode", "relay_1", "relay_2", "relay_3", "reason",
                "temperature", "humidity", "lux", "occupancy",
                "energy_kw", "battery_level", "battery_voltage",
            )
        )

        # Query MLPrediction
        predictions = list(
            MLPrediction.objects.filter(timestamp__gte=start)
            .order_by("timestamp")
            .values(
                "timestamp", "predicted_energy_wh",
                "upper_bound_wh", "lower_bound_wh",
            )
        )

        # Match by minute key
        def _minute_key(ts):
            return ts.strftime("%Y-%m-%d %H:%M")

        pred_by_minute = {}
        for p in predictions:
            pred_by_minute[_minute_key(p["timestamp"])] = p

        data = []
        for r in relays:
            ts_key = _minute_key(r["timestamp"])
            pred = pred_by_minute.get(ts_key)

            data.append({
                "timestamp": tz.localtime(r["timestamp"]).strftime("%Y-%m-%d %H:%M:%S"),
                "temperature": round(r["temperature"], 2) if r["temperature"] is not None else None,
                "humidity": round(r["humidity"], 2) if r["humidity"] is not None else None,
                "lux": round(r["lux"], 2) if r["lux"] is not None else None,
                "occupancy": r["occupancy"] if r["occupancy"] is not None else None,
                "real time energy": round(r["energy_kw"], 4) if r["energy_kw"] is not None else 0.0,
                "real time energy (5-min)": round(r["energy_kw"] * 5, 4) if r["energy_kw"] is not None else 0.0,
                "predicted_energy_8lags": round(pred["predicted_energy_wh"], 4) if (pred and pred["predicted_energy_wh"] is not None) else 0.0,
                "predicted_energy_lower_8lags": round(pred["lower_bound_wh"], 4) if (pred and pred["lower_bound_wh"] is not None) else 0.0,
                "predicted_energy_upper_8lags": round(pred["upper_bound_wh"], 4) if (pred and pred["upper_bound_wh"] is not None) else 0.0,
                "Battery Voltage": round(r["battery_voltage"], 2) if r["battery_voltage"] is not None else None,
                "Battery Percentage": round(r["battery_level"], 1) if r["battery_level"] is not None else None,
                "System Mode A,B,C": r["mode"] if r["mode"] else None,
            })

        return Response(data)
