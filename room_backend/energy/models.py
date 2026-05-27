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
    lux = models.FloatField(help_text="Average ambient light (lx)", default=0.0)
    energy_kw = models.FloatField(help_text="Average energy (kWh)", default=0.0)
    power_w = models.FloatField(help_text="Average power (W)", default=0.0)
    radar_motion = models.IntegerField(help_text="Dominant radar motion state", default=0)
    battery_voltage = models.FloatField(help_text="Average battery voltage (V)", default=0.0)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Sensor Log"
        verbose_name_plural = "Sensor Logs"

    def __str__(self):
        return f"SensorLog @ {self.timestamp:%Y-%m-%d %H:%M}"


class MLPrediction(models.Model):
    """Predictions published by the ML team."""

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    predicted_energy_wh = models.FloatField(
        help_text="Predicted energy consumption (Wh)", default=0.0
    )
    upper_bound_wh = models.FloatField(
        help_text="Upper bound of prediction (Wh)", default=0.0
    )
    lower_bound_wh = models.FloatField(
        help_text="Lower bound of prediction (Wh)", default=0.0
    )

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "ML Prediction"
        verbose_name_plural = "ML Predictions"

    def __str__(self):
        return f"MLPrediction @ {self.timestamp:%Y-%m-%d %H:%M}"


class RelayState(models.Model):
    """Audit log of every relay-mode decision made by the rule engine."""

    MODE_CHOICES = [
        ("A", "Smart A — Peak Load (All ON)"),
        ("B", "Smart B — Moderate Load (R1+R2 ON)"),
        ("C", "Smart C — Baseline Load (R1 ON only)"),
        ("MANUAL", "Manual Override"),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    mode = models.CharField(max_length=6, choices=MODE_CHOICES)
    relay_1 = models.BooleanField(help_text="Priority 1 relay state")
    relay_2 = models.BooleanField(help_text="Priority 2 relay state")
    relay_3 = models.BooleanField(help_text="Priority 3 relay state")
    reason = models.TextField(help_text="Human-readable reason for this decision")

    # Sensor snapshot at the time of the decision
    temperature = models.FloatField(help_text="Temperature (°C) at decision time", default=0.0)
    humidity = models.FloatField(help_text="Humidity (%) at decision time", default=0.0)
    lux = models.FloatField(help_text="Ambient light (lx) at decision time", default=0.0)
    occupancy = models.IntegerField(help_text="Occupancy state at decision time", default=0)
    energy_kw = models.FloatField(help_text="Energy (kWh) at decision time", default=0.0)
    battery_level = models.FloatField(help_text="Battery SoC (%) at decision time", default=0.0)
    battery_voltage = models.FloatField(help_text="Battery voltage (V) at decision time", default=0.0)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Relay State"
        verbose_name_plural = "Relay States"

    def __str__(self):
        return f"RelayState {self.mode} @ {self.timestamp:%Y-%m-%d %H:%M}"
