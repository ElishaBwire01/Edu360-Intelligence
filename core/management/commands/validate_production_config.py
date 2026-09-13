from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Validate production security settings without changing configuration."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true")

    def handle(self, *args, **options):
        checks = {
            "DEBUG=False": settings.DEBUG is False,
            "SECRET_KEY is strong": bool(settings.SECRET_KEY) and len(settings.SECRET_KEY) >= 50 and not settings.SECRET_KEY.startswith("django-insecure-"),
            "explicit ALLOWED_HOSTS": bool(settings.ALLOWED_HOSTS) and "*" not in settings.ALLOWED_HOSTS,
            "SECURE_SSL_REDIRECT=True": getattr(settings, "SECURE_SSL_REDIRECT", False) is True,
            "SESSION_COOKIE_SECURE=True": getattr(settings, "SESSION_COOKIE_SECURE", False) is True,
            "CSRF_COOKIE_SECURE=True": getattr(settings, "CSRF_COOKIE_SECURE", False) is True,
            "SECURE_HSTS_SECONDS configured": getattr(settings, "SECURE_HSTS_SECONDS", 0) > 0,
        }
        failures = [name for name, passed in checks.items() if not passed]
        for name, passed in checks.items():
            self.stdout.write(f"{'PASS' if passed else 'FAIL'}: {name}")
        if failures and options["strict"]:
            raise CommandError("Production configuration validation failed")