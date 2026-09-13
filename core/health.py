"""Minimal operational health checks that do not expose sensitive data."""

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def healthz(request):
    """Return application, database, and cache status without secrets."""
    checks = {}
    overall = "healthy"

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "healthy"
    except Exception:
        checks["database"] = "unavailable"
        overall = "unavailable"

    try:
        cache_key = "healthz:probe"
        cache.set(cache_key, "ok", timeout=10)
        checks["cache"] = "healthy" if cache.get(cache_key) == "ok" else "degraded"
        if checks["cache"] != "healthy" and overall == "healthy":
            overall = "degraded"
    except Exception:
        checks["cache"] = "degraded"
        if overall == "healthy":
            overall = "degraded"

    status = 200 if overall == "healthy" else 503
    return JsonResponse({"status": overall, "checks": checks}, status=status)