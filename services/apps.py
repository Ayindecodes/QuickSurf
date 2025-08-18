from __future__ import annotations

import logging
from importlib import import_module

from django.apps import AppConfig
from django.conf import settings
from django.core.cache import caches
from django.core.checks import register, Warning, Error

logger = logging.getLogger(__name__)

_last_signals_import_error: Exception | None = None


class ServicesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services"
    verbose_name = "Quicksurf Services"

    def ready(self):
        """
        Import signals on app startup. Do not crash the app if signals fail; record the
        exception so our system checks can surface it in `manage.py check`.
        """
        global _last_signals_import_error
        try:
            import_module("services.signals")
            logger.debug("services.signals imported successfully")
        except ModuleNotFoundError:
            # Optional; skip quietly in early migrations or if intentionally absent
            logger.info("services.signals not found; skipping import")
        except Exception as e:  # pragma: no cover
            _last_signals_import_error = e
            logger.exception("services.signals import failed")


# ---------------------------------------------------------------------------
# System checks: surface config issues early with `manage.py check`
# ---------------------------------------------------------------------------
@register()
def services_system_checks(app_configs, **kwargs):
    messages = []

    # 1) Signals import health
    if _last_signals_import_error is not None:
        messages.append(
            Error(
                f"services.signals import failed: {_last_signals_import_error}",
                id="services.E001",
                hint="Check for syntax errors or circular imports in services/signals.py",
            )
        )

    # 2) Provider mode + VTpass credentials sanity
    provider_mode = str(getattr(settings, "PROVIDER_MODE", "MOCK")).upper()
    vt_email = getattr(settings, "VTPASS_EMAIL", "")
    vt_api = getattr(settings, "VTPASS_API_KEY", "")
    vt_pub = getattr(settings, "VTPASS_PUBLIC_KEY", "")

    if provider_mode == "LIVE":
        missing = [k for k, v in {
            "VTPASS_EMAIL": vt_email,
            "VTPASS_API_KEY": vt_api,
            "VTPASS_PUBLIC_KEY": vt_pub,
        }.items() if not v]
        if missing:
            messages.append(
                Warning(
                    "VTpass LIVE mode is enabled but some credentials are missing.",
                    id="services.W001",
                    hint=f"Missing settings: {', '.join(missing)}",
                )
            )

    # 3) Cache backend suitability for single-flight locks (recommended Redis in prod)
    try:
        cache = caches["default"]
        backend_path = f"{cache.__class__.__module__}.{cache.__class__.__name__}"
        if not settings.DEBUG and provider_mode == "LIVE":
            if "django_redis" not in backend_path:
                messages.append(
                    Warning(
                        "Non-Redis cache backend detected while in LIVE mode.",
                        id="services.W002",
                        hint=(
                            "Single-flight locks use Django cache. Configure REDIS_URL and the "
                            "django-redis backend for reliability under load."
                        ),
                    )
                )
    except Exception as e:  # pragma: no cover
        messages.append(
            Warning(
                f"Could not inspect cache backend: {e}",
                id="services.W003",
                hint="Ensure CACHES['default'] is configured.",
            )
        )

    # 4) JWT lifetime guardrail (optional recommendation)
    try:
        from rest_framework_simplejwt.settings import api_settings as jwt_settings
        access_minutes = int(jwt_settings.ACCESS_TOKEN_LIFETIME.total_seconds() // 60)
        if access_minutes < 60:
            messages.append(
                Warning(
                    "JWT access token lifetime is less than 60 minutes.",
                    id="services.W004",
                    hint="Consider increasing for better UX or ensure refresh flow is solid.",
                )
            )
    except Exception:
        # SimpleJWT not installed or settings not ready; ignore
        pass

    return messages

