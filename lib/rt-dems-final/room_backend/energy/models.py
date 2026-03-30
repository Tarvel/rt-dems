"""
Data models for the Smart Room Energy Management System.

Three tables are maintained:
  • SensorLog   — 5-minute averaged sensor readings (written by mqtt_logger.py)
  • MLPrediction — ML team predictions (written by mqtt_logger.py)
  • RelayState   — Audit trail of every relay-mode decision (written by rule_engine.py)
"""

from django.db import models


class SensorLog(models.Model):
    """Five-minute averaged sensor data from the hardware team."""

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    temperature = models.FloatField(help_text="Average temperature (°C)")
    humidity = models.FloatField(help_text="Average humidity (%)")
    occupancy = models.IntegerField(
        help_text="Dominant occupancy state: 1 = occupied, 0 = empty"
    )
    voltage = models.FloatField(help_text="Average voltage (V)")
    current = models.FloatField(help_text="Average current (A)")
    battery_level = models.FloatField(help_text="Average battery level (%)")

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Sensor Log"
        verbose_name_plural = "Sensor Logs"

    def __str__(self):
        return f"SensorLog @ {self.timestamp:%Y-%m-%d %H:%M}"


class MLPrediction(models.Model):
    """Predictions published by the ML team."""

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    predicted_energy_range = models.FloatField(
        help_text="Predicted energy consumption range (kWh)"
    )
    peak_demand = models.FloatField(help_text="Peak demand threshold (kWh)")

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "ML Prediction"
        verbose_name_plural = "ML Predictions"

    def __str__(self):
        return f"MLPrediction @ {self.timestamp:%Y-%m-%d %H:%M}"


class RelayState(models.Model):
    """Audit log of every relay-mode decision made by the rule engine."""

    MODE_CHOICES = [
        ("A", "Mode A — Peak Demand (All ON)"),
        ("B", "Mode B — Average Load (P1+P2 ON)"),
        ("C", "Mode C — Baseline Load (P1 ON only)"),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    mode = models.CharField(max_length=1, choices=MODE_CHOICES)
    relay_1 = models.BooleanField(help_text="Priority 1 relay state")
    relay_2 = models.BooleanField(help_text="Priority 2 relay state")
    relay_3 = models.BooleanField(help_text="Priority 3 relay state")
    reason = models.TextField(help_text="Human-readable reason for this decision")

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Relay State"
        verbose_name_plural = "Relay States"

    def __str__(self):
        return f"RelayState {self.mode} @ {self.timestamp:%Y-%m-%d %H:%M}"
