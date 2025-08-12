# services/apps.py
from django.apps import AppConfig
from django.core.checks import register, Warning
from importlib import import_module
from decouple import config


class ServicesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "services"
    verbose_name = "Quicksurf Services"

    def ready(self):
        """
        - Import signals safely so they register when Django starts.
        - Register system checks for provider configuration.
        """
        # --- Import signals (safe) ---
        try:
            import_module("services.signals")
        except ModuleNotFoundError:
            # No signals module yet; that's fine.
            pass
        except Exception as e:
            # Don't crash app if signals import fails; surface a readable warning instead.
            from django.core.checks import Error
            @register()  # register immediately with the specific error
            def _signals_import_error_check(app_configs, **kwargs):
                return [
                    Error(
                        f"services.signals import failed: {e}",
                        id="services.E001",
                        hint="Check for import loops or syntax errors in services/signals.py",
                    )
                ]
            # still return to avoid re-raising


# -------------------------
# System checks (VTpass keys)
# -------------------------
@register()
def provider_config_check(app_configs, **kwargs):
    """
    Warn if PROVIDER_MODE is LIVE but VTpass keys are missing.
    This runs at startup and with `python manage.py check`.
    """
    messages = []

    provider_mode = config("PROVIDER_MODE", default="MOCK").upper()
    if provider_mode == "LIVE":
        required = {
            "VTPASS_EMAIL": config("VTPASS_EMAIL", default=""),
            "VTPASS_API_KEY": config("VTPASS_API_KEY", default=""),
            "VTPASS_PUBLIC_KEY": config("VTPASS_PUBLIC_KEY", default=""),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            messages.append(
                Warning(
                    "VTpass LIVE mode is enabled but some credentials are missing.",
                    id="services.W001",
                    hint=f"Missing .env keys: {', '.join(missing)}",
                )
            )
    return messages
