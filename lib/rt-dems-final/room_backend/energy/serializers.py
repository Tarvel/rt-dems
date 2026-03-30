"""DRF serializers — read-only representations of energy data."""

from rest_framework import serializers
from .models import SensorLog, MLPrediction, RelayState


class SensorLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SensorLog
        fields = "__all__"


class MLPredictionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MLPrediction
        fields = "__all__"


class RelayStateSerializer(serializers.ModelSerializer):
    mode_display = serializers.CharField(source="get_mode_display", read_only=True)

    class Meta:
        model = RelayState
        fields = [
            "id",
            "timestamp",
            "mode",
            "mode_display",
            "relay_1",
            "relay_2",
            "relay_3",
            "reason",
        ]
