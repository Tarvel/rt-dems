from django.contrib import admin
from .models import SensorLog, MLPrediction, RelayState


@admin.register(SensorLog)
class SensorLogAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "temperature",
        "humidity",
        "occupancy",
        "voltage",
        "current",
        "battery_level",
    )
    list_filter = ("occupancy",)
    readonly_fields = ("timestamp",)


@admin.register(MLPrediction)
class MLPredictionAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "predicted_energy_range", "peak_demand")
    readonly_fields = ("timestamp",)


@admin.register(RelayState)
class RelayStateAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "mode", "relay_1", "relay_2", "relay_3", "reason")
    list_filter = ("mode",)
    readonly_fields = ("timestamp",)
