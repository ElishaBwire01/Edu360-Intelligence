from django.urls import path

from .health import healthz

urlpatterns = [
    path("", healthz, name="healthz"),
]