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
