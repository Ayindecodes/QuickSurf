from pathlib import Path
from datetime import timedelta
from decouple import config, Csv
from django.core.exceptions import ImproperlyConfigured
import dj_database_url
import os
import logging  # NEW

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name: str, default: bool = False) -> bool:
    """
    Parse booleans safely, including common deployment values like "release".
    """
    raw = config(name, default=None)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw

    value = str(raw).strip().lower()
    if value in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "f", "no", "n", "off", "release", "prod", "production"}:
        return False
    return default


def normalize_env(value: str | None) -> str:
    raw = (value or "development").strip().lower()
    aliases = {
        "prod": "production",
        "production": "production",
        "release": "production",
        "stage": "staging",
        "staging": "staging",
        "dev": "development",
        "development": "development",
        "local": "development",
    }
    return aliases.get(raw, raw)

# -------------------------------------------------------------------
# Core / Environment
# -------------------------------------------------------------------
ENV = normalize_env(config("ENV", default="development"))  # development | staging | production
IS_PRODUCTION = ENV == "production"
DEBUG = env_bool("DEBUG", default=(not IS_PRODUCTION))

SECRET_KEY = config("SECRET_KEY", default="")
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise ImproperlyConfigured("SECRET_KEY is required when ENV is production.")
    SECRET_KEY = "dev-only-insecure-secret-key"

# include Render defaults in case env var not set
ALLOWED_HOSTS = config("ALLOWED_HOSTS", cast=Csv(), default="localhost,127.0.0.1,.onrender.com,quicksurf.onrender.com")
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    cast=Csv(),
    default="http://localhost:3000,http://127.0.0.1:3000,https://quicksurf.onrender.com",
)

SITE_ID = 1

# -------------------------------------------------------------------
# Installed Apps
# -------------------------------------------------------------------
INSTALLED_APPS = [
    # Django core
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",

    # Third-party
    "allauth",
    "allauth.account",
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "drf_spectacular",
    "corsheaders",
    "django_filters",

    # Project apps
    "users.apps.UsersConfig",
    "wallets",
    "services.apps.ServicesConfig",
    "payments",
    "rewards",
    "notifications",
    "core",
]

# -------------------------------------------------------------------
# Middleware
# -------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",

    "corsheaders.middleware.CorsMiddleware",

    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# -------------------------------------------------------------------
# URLs / Templates / WSGI
# -------------------------------------------------------------------
ROOT_URLCONF = "quicksurf.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "quicksurf.wsgi.application"

# -------------------------------------------------------------------
# Database (Render/Heroku-style via DATABASE_URL)
# -------------------------------------------------------------------
DATABASES = {
    "default": dj_database_url.config(
        default=config("DATABASE_URL", default=f"sqlite:///{BASE_DIR/'db.sqlite3'}"),
        conn_max_age=600,
        ssl_require=env_bool("DB_SSL_REQUIRE", default=IS_PRODUCTION),
    )
}

# -------------------------------------------------------------------
# Caches (for single-flight locks, rate limits, etc.)
# -------------------------------------------------------------------
REDIS_URL = config("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {"ssl_cert_reqs": None} if REDIS_URL.startswith("rediss://") else {},
            "TIMEOUT": None,  # per-key TTLs still respected
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "quicksurf-locmem",
            "TIMEOUT": None,
        }
    }

# -------------------------------------------------------------------
# Password Validators
# -------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# -------------------------------------------------------------------
# I18N / TZ
# -------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

# -------------------------------------------------------------------
# Static & Media (WhiteNoise)
# -------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
WHITENOISE_MAX_AGE = 60 if DEBUG else 60 * 60 * 24 * 30  # 30 days in prod
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    }
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -------------------------------------------------------------------
# Auth / Allauth
# -------------------------------------------------------------------
AUTH_USER_MODEL = "users.User"
ACCOUNT_USER_MODEL_USERNAME_FIELD = "email"
ACCOUNT_ADAPTER = "users.adapters.NoUsernameAccountAdapter"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
ACCOUNT_LOGIN_METHODS = ["email"]
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = config("ACCOUNT_EMAIL_VERIFICATION", default="mandatory")

# -------------------------------------------------------------------
# Email
# -------------------------------------------------------------------
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="no-reply@quicksurf.app")

# -------------------------------------------------------------------
# DRF / Schema / Throttling
# -------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": config("DRF_USER_THROTTLE", default="1000/day"),
        "anon": config("DRF_ANON_THROTTLE", default="100/day"),
    },
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Quicksurf API",
    "DESCRIPTION": "Airtime/Data purchases with wallet + providers",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# -------------------------------------------------------------------
# JWT
# -------------------------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=4),  # 4 hours
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_DAYS", default=1, cast=int)),
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# -------------------------------------------------------------------
# CORS / CSRF
# -------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS",
    cast=Csv(),
    default="http://localhost:3000,http://127.0.0.1:3000,https://quicksurf.onrender.com",
)
# Optionally pin your active frontend origin in one place.
FRONTEND_URL = config("FRONTEND_URL", default="").strip()
if FRONTEND_URL:
    if FRONTEND_URL not in CORS_ALLOWED_ORIGINS:
        CORS_ALLOWED_ORIGINS = [*CORS_ALLOWED_ORIGINS, FRONTEND_URL]
    if FRONTEND_URL not in CSRF_TRUSTED_ORIGINS:
        CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, FRONTEND_URL]

# Helpful for Render preview/alias domains (https only).
CORS_ALLOWED_ORIGIN_REGEXES = [r"^https://.*\.onrender\.com$"]

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = env_bool("CORS_ALLOW_ALL_ORIGINS", default=DEBUG)

# -------------------------------------------------------------------
# Security (good defaults for HTTPS)
# -------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=IS_PRODUCTION)

SESSION_COOKIE_SECURE = IS_PRODUCTION
CSRF_COOKIE_SECURE = IS_PRODUCTION
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True

SECURE_HSTS_SECONDS = 60 * 60 * 24 * 7 if IS_PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = IS_PRODUCTION
SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", default=False)

X_FRAME_OPTIONS = "DENY"
REFERRER_POLICY = "same-origin"

# -------------------------------------------------------------------
# Login redirects (only used if you serve Django templates)
# -------------------------------------------------------------------
LOGIN_REDIRECT_URL = "/dashboard/"
ACCOUNT_LOGOUT_REDIRECT_URL = "/accounts/login/"

# -------------------------------------------------------------------
# Providers config (read from .env)
# -------------------------------------------------------------------
PROVIDER_MODE = config("PROVIDER_MODE", default="LIVE").upper()  # LIVE | MOCK

# VTpass
# keep without trailing slash; code will append endpoints like /balance, /pay
VTPASS_BASE_URL = config("VTPASS_BASE_URL", default="https://vtpass.com/api").rstrip("/")
VTPASS_EMAIL = config("VTPASS_EMAIL", default="")
VTPASS_API_KEY = config("VTPASS_API_KEY", default="")
VTPASS_PUBLIC_KEY = config("VTPASS_PUBLIC_KEY", default="")
VTPASS_SECRET_KEY = config("VTPASS_SECRET_KEY", default="")
VTPASS_IP_WHITELISTED = env_bool("VTPASS_IP_WHITELISTED", default=False)

# Paystack (for wallet funding)
PAYSTACK_SECRET_KEY = config("PAYSTACK_SECRET_KEY", default="")
PAYSTACK_PUBLIC_KEY = config("PAYSTACK_PUBLIC_KEY", default="")
PAYSTACK_BASE_URL = config("PAYSTACK_BASE_URL", default="https://api.paystack.co")
PAYSTACK_WEBHOOK_SECRET = config("PAYSTACK_WEBHOOK_SECRET", default="")  # HMAC verification

# -------------------------------------------------------------------
# Feature toggles
# -------------------------------------------------------------------
POINTS_PER_NAIRA = config("POINTS_PER_NAIRA", default="0.01")
REWARDS_ENABLED = env_bool("REWARDS_ENABLED", default=True)
RECEIPT_EMAILS_ENABLED = env_bool("RECEIPT_EMAILS_ENABLED", default=True)

# >>> NEW: allow LIVE tests even if internal wallet is 0.00
ALLOW_PROVIDER_DIRECT_CHARGE = env_bool("ALLOW_PROVIDER_DIRECT_CHARGE", default=False)

# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------
LOG_LEVEL = "DEBUG" if DEBUG else "INFO"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "app": {"format": "[{levelname}] {asctime} {name} - {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "app"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING" if not DEBUG else "INFO"},
        "django.request": {"level": "WARNING"},
    },
}

# >>> NEW: print a clear boot line so you can verify LIVE mode in Render logs
logger = logging.getLogger(__name__)
logger.warning(f"BOOT ENV={ENV} DEBUG={DEBUG} PROVIDER_MODE={PROVIDER_MODE} VTPASS_BASE_URL={VTPASS_BASE_URL} DIRECT_CHARGE={ALLOW_PROVIDER_DIRECT_CHARGE}")
