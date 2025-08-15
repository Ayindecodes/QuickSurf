# services/apps.py
from django.apps import AppConfig
from django.core.checks import register, Warning, Error
from django.conf import settings
from importlib import import_module
import logging

logger = logging.getLogger(__name__)

_last_signals_import_error = None  # module-level flag for the system check


class ServicesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services"
    verbose_name = "Quicksurf Services"

    def ready(self):
        """
        - Import signals so they register when Django starts.
        - Never crash app on import error; record it and surface via system checks.
        """
        global _last_signals_import_error
        try:
            import_module("services.signals")
        except ModuleNotFoundError:
            # No signals module yet; that's fine.
            logger.info("services.signals not found; skipping.")
        except Exception as e:
            _last_signals_import_error = e
            logger.exception("services.signals import failed")


# -------------------------
# System checks (signals + VTpass keys)
# -------------------------
@register()
def services_system_checks(app_configs, **kwargs):
    """
    - Report if signals failed to import.
    - Warn if LIVE mode is set but VTpass keys are missing.
    """
    messages = []

    # 1) Signals import health
    if _last_signals_import_error is not None:
        messages.append(
            Error(
                f"services.signals import failed: {_last_signals_import_error}",
                id="services.E001",
                hint="Check for import loops or syntax errors in services/signals.py",
            )
        )

    # 2) Provider config sanity (use Django settings, not env directly)
    provider_mode = str(getattr(settings, "PROVIDER_MODE", "MOCK")).upper()
    if provider_mode == "LIVE":
        required = {
            "VTPASS_EMAIL": getattr(settings, "VTPASS_EMAIL", ""),
            "VTPASS_API_KEY": getattr(settings, "VTPASS_API_KEY", ""),
            "VTPASS_PUBLIC_KEY": getattr(settings, "VTPASS_PUBLIC_KEY", ""),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            messages.append(
                Warning(
                    "VTpass LIVE mode is enabled but some credentials are missing.",
                    id="services.W001",
                    hint=f"Missing settings: {', '.join(missing)}",
                )
            )

    return messages
