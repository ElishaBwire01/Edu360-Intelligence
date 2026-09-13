# Deployment Readiness Findings

Status: Read-only production configuration audit performed on 2026-09-13.
No application or database data was changed.

## Command executed

```text
python manage.py check --deploy
```

## Findings

The host currently reports six Django deployment warnings:

| Check | Finding | Production action |
|---|---|---|
| `security.W004` | `SECURE_HSTS_SECONDS` is not set | Set HSTS only after the entire production domain is HTTPS-only and verified |
| `security.W008` | `SECURE_SSL_REDIRECT` is not enabled | Enable it or enforce equivalent redirect at the trusted reverse proxy |
| `security.W009` | Development/insecure secret fallback is active in this environment | Provide a long random `SECRET_KEY` through secret management; fail closed in production |
| `security.W012` | `SESSION_COOKIE_SECURE` is not enabled | Enable for HTTPS production |
| `security.W016` | `CSRF_COOKIE_SECURE` is not enabled | Enable for HTTPS production |
| `security.W018` | `DEBUG=True` is active | Set `DEBUG=False` in production and verify error pages/logging |

## Why these were not changed now

The current command ran in the local development environment. Enabling HSTS,
SSL redirects, and secure-only cookies unconditionally could break local HTTP
development and can be unsafe before the production proxy/domain is verified.
The correct implementation is environment-specific production configuration,
not a blanket local setting change.

## Required production configuration gate

Before deployment, production environment configuration must provide:

```text
DEBUG=False
SECRET_KEY=<long random secret from secret management>
ALLOWED_HOSTS=<explicit production hosts>
CSRF_TRUSTED_ORIGINS=<explicit HTTPS origins>
SECURE_SSL_REDIRECT=True or equivalent trusted proxy redirect
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_HSTS_SECONDS=<approved value after HTTPS verification>
SECURE_HSTS_INCLUDE_SUBDOMAINS=True where appropriate
SECURE_HSTS_PRELOAD=True only after domain policy review
```

The deployment check must be rerun with production settings, not merely the
local development settings, and all warnings must be reviewed before Phase 9
or final production acceptance.

## Additional inventory-linked risks

- EduGrade still contains `DEBUG=True` and a hard-coded development secret;
  it must not be deployed or merged as-is.
- Host development fallback values are acceptable only for local development;
  production must fail closed when required secrets/configuration are absent.
- Bootstrap scripts with example/admin credentials must not be used for
  production administration.

## Status

This is a remediation queue, not a claim of production readiness. The next
safe action is to create and test a dedicated production settings path or
deployment environment, while Phase 3 continues to wait for a valid EduGrade
snapshot.

## Validation command

The host now provides a read-only validator:

```text
python manage.py validate_production_config
python manage.py validate_production_config --strict
```

The non-strict form reports each setting. The strict form exits with failure
when a production requirement is missing. Running it under local development
settings is expected to report failures; it does not modify settings.