"""Energy app URL routing — mounted at /api/v1/ by the project urls.py."""

from django.urls import path
from . import views

app_name = "energy"

urlpatterns = [
    # Sensor logs
    path("sensors/", views.SensorLogListView.as_view(), name="sensor-list"),
    path("sensors/latest/", views.LatestSensorView.as_view(), name="sensor-latest"),
    # ML predictions
    path(
        "predictions/",
        views.MLPredictionListView.as_view(),
        name="prediction-list",
    ),
    path(
        "predictions/latest/",
        views.LatestMLPredictionView.as_view(),
        name="prediction-latest",
    ),
    # Relay state
    path("relays/", views.RelayStateListView.as_view(), name="relay-list"),
    path(
        "relays/current/",
        views.CurrentRelayStateView.as_view(),
        name="relay-current",
    ),
]
