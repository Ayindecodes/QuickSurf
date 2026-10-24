import logging
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import dj_database_url
from corsheaders.defaults import default_headers as default_cors_headers
from decouple import Csv, config
from django.core.exceptions import ImproperlyConfigured

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
    SECRET_KEY = "dev-only-insecure-secret-key-never-use-in-production-0123456789"
# It signs login tokens and keys PIN/code hashes, so it must be long and random.
if IS_PRODUCTION and len(SECRET_KEY) < 50:
    raise ImproperlyConfigured("SECRET_KEY must be at least 50 characters when ENV is production.")

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
    "allauth.mfa",  # authenticator-app 2FA for staff signing in to the admin
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
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
    "core.request_id.RequestIdMiddleware",  # first, so every log line in the request carries the ID
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "core.staff_mfa.StaffMFAMiddleware",
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
if IS_PRODUCTION and not config("DATABASE_URL", default=""):
    raise ImproperlyConfigured("DATABASE_URL is required when ENV is production (it would fall back to SQLite).")

DATABASES = {
    "default": dj_database_url.config(
        default=config("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
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
# allauth's pages are only for staff signing in to the admin; customers use the JWT API.
ACCOUNT_ADAPTER = "users.adapters.StaffOnlyAccountAdapter"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
ACCOUNT_LOGIN_METHODS = ["email"]
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "none"  # customer emails are verified by the API's code flow
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes"]
MFA_TOTP_ISSUER = "QuickSurf"

# Admin lives at /<ADMIN_URL>; use something unguessable in production. Staff must set up an
# authenticator app before using it when STAFF_MFA_REQUIRED (on by default in production).
ADMIN_URL = config("ADMIN_URL", default="admin/").strip("/") + "/"
STAFF_MFA_REQUIRED = env_bool("STAFF_MFA_REQUIRED", default=IS_PRODUCTION)

# -------------------------------------------------------------------
# Email
# -------------------------------------------------------------------
EMAIL_BACKEND = config("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="no-reply@quicksurf.app")
EMAIL_HOST = config("EMAIL_HOST", default="localhost")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", default=True)
EMAIL_TIMEOUT = 10

# -------------------------------------------------------------------
# DRF / Schema / Throttling
# -------------------------------------------------------------------
REST_FRAMEWORK = {
    # Bearer tokens only: no session cookies, so no CSRF exposure from other sites.
    "DEFAULT_AUTHENTICATION_CLASSES": ("rest_framework_simplejwt.authentication.JWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_RENDERER_CLASSES": (
        ("core.renderers.ApiJSONRenderer", "rest_framework.renderers.BrowsableAPIRenderer")
        if DEBUG
        else ("core.renderers.ApiJSONRenderer",)
    ),
    "EXCEPTION_HANDLER": "core.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "core.throttling.SafeUserRateThrottle",
        "core.throttling.SafeAnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": config("DRF_USER_THROTTLE", default="1000/day"),
        "anon": config("DRF_ANON_THROTTLE", default="100/day"),
        # Per IP (core.net.client_ip) unless noted
        "login": "20/minute",
        "login_email": "10/hour",  # per email address
        "register": "20/hour",
        "password_reset": "10/hour",
        "verify_email": "10/hour",
        "pin_change": "10/hour",
        "password_change": "10/hour",
        "phone_verify": "5/hour",  # each one sends an SMS, which costs money
        "redeem": "10/hour",
        "purchase": config("PURCHASE_RATE", default="30/hour"),  # per user
        "topup": config("TOPUP_RATE", default="20/hour"),  # per user
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
    "PREPROCESSING_HOOKS": ["core.schema.canonical_paths_only"],
}

# -------------------------------------------------------------------
# JWT
# -------------------------------------------------------------------
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=config("JWT_ACCESS_MINUTES", default=60, cast=int)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=config("JWT_REFRESH_DAYS", default=1, cast=int)),
    "AUTH_HEADER_TYPES": ("Bearer",),
    # Each refresh returns a new refresh token and revokes the old one, so a stolen one stops working.
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
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

# Exact origins only: a pattern like *.onrender.com would trust anyone's Render app.
# Auth is a Bearer header, so browsers never need to send cookies cross-origin.
CORS_ALLOW_CREDENTIALS = False
CORS_ALLOW_ALL_ORIGINS = env_bool("CORS_ALLOW_ALL_ORIGINS", default=DEBUG)
# The app sends its device ID (fraud checks, users/risk.py) and may read the request ID from errors.
CORS_ALLOW_HEADERS = (*default_cors_headers, "x-device-id")
CORS_EXPOSE_HEADERS = ["X-Request-ID"]

# -------------------------------------------------------------------
# Security (good defaults for HTTPS)
# -------------------------------------------------------------------
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# The header the proxy in front of us sets to the real client IP (core/net.py): CF-Connecting-IP behind
# Cloudflare (Render), X-Real-IP behind our own Caddy (deploy/). Empty: use the connection's address.
CLIENT_IP_HEADER = config("CLIENT_IP_HEADER", default="CF-Connecting-IP").strip()
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
# Login redirects (staff signing in through allauth go to the admin)
# -------------------------------------------------------------------
LOGIN_REDIRECT_URL = f"/{ADMIN_URL}"
ACCOUNT_LOGOUT_REDIRECT_URL = "/accounts/login/"

# -------------------------------------------------------------------
# Providers config (read from .env)
# -------------------------------------------------------------------
# LIVE sells real airtime/data through vtpass.com; SANDBOX (old name: MOCK) uses sandbox.vtpass.com.
# Production must choose explicitly; everywhere else it defaults to SANDBOX so no one spends real money by accident.
PROVIDER_MODE = config("PROVIDER_MODE", default="" if IS_PRODUCTION else "SANDBOX").strip().upper()
if PROVIDER_MODE == "MOCK":
    PROVIDER_MODE = "SANDBOX"
if PROVIDER_MODE not in {"LIVE", "SANDBOX"}:
    raise ImproperlyConfigured("PROVIDER_MODE must be LIVE or SANDBOX (it is required when ENV is production).")

# VTpass
# keep without trailing slash; code will append endpoints like /balance, /pay
VTPASS_BASE_URL = config(
    "VTPASS_BASE_URL",
    default="https://vtpass.com/api" if PROVIDER_MODE == "LIVE" else "https://sandbox.vtpass.com/api",
).rstrip("/")
VTPASS_API_KEY = config("VTPASS_API_KEY", default="")
VTPASS_PUBLIC_KEY = config("VTPASS_PUBLIC_KEY", default="")
VTPASS_SECRET_KEY = config("VTPASS_SECRET_KEY", default="")

if IS_PRODUCTION and PROVIDER_MODE == "LIVE":
    _missing = [
        name
        for name, value in (
            ("VTPASS_API_KEY", VTPASS_API_KEY),
            ("VTPASS_PUBLIC_KEY", VTPASS_PUBLIC_KEY),
            ("VTPASS_SECRET_KEY", VTPASS_SECRET_KEY),
        )
        if not value
    ]
    if _missing:
        raise ImproperlyConfigured(f"PROVIDER_MODE is LIVE but these are missing: {', '.join(_missing)}.")
    if "sandbox" in VTPASS_BASE_URL:
        raise ImproperlyConfigured("PROVIDER_MODE is LIVE but VTPASS_BASE_URL points at the VTpass sandbox.")

# Paystack (for wallet funding)
PAYSTACK_SECRET_KEY = config("PAYSTACK_SECRET_KEY", default="")
PAYSTACK_PUBLIC_KEY = config("PAYSTACK_PUBLIC_KEY", default="")
PAYSTACK_BASE_URL = config("PAYSTACK_BASE_URL", default="https://api.paystack.co")
PAYSTACK_WEBHOOK_SECRET = config("PAYSTACK_WEBHOOK_SECRET", default="")  # HMAC verification
# Where Paystack sends the user after checkout (a frontend page that calls /api/payments/verify/).
# Empty: use the callback URL set in the Paystack dashboard.
PAYSTACK_CALLBACK_URL = config("PAYSTACK_CALLBACK_URL", default="").strip()

if IS_PRODUCTION and not PAYSTACK_SECRET_KEY.startswith("sk_live_"):
    raise ImproperlyConfigured("PAYSTACK_SECRET_KEY must be a live key (sk_live_...) when ENV is production.")

# -------------------------------------------------------------------
# Feature toggles
# -------------------------------------------------------------------
POINTS_PER_NAIRA = config("POINTS_PER_NAIRA", default="0.01")
REWARDS_ENABLED = env_bool("REWARDS_ENABLED", default=True)
# Loyalty points: earned at POINTS_PER_NAIRA, each worth REWARD_POINT_VALUE naira when redeemed into the wallet.
REWARD_POINT_VALUE = config("REWARD_POINT_VALUE", default="1.00", cast=Decimal)
REWARDS_MIN_REDEMPTION = config("REWARDS_MIN_REDEMPTION", default=100, cast=int)
RECEIPT_EMAILS_ENABLED = env_bool("RECEIPT_EMAILS_ENABLED", default=True)

# Kill switches: set to False on Render to stop new purchases or top-ups without a code change.
# Payment verify and the Paystack webhook stay on, so payments already made are still credited.
PURCHASES_ENABLED = env_bool("PURCHASES_ENABLED", default=True)
TOPUPS_ENABLED = env_bool("TOPUPS_ENABLED", default=True)

# Purchase limits in naira. DAILY_PURCHASE_LIMIT is per user per day (Lagos time); 0 turns it off.
AIRTIME_MIN_AMOUNT = config("AIRTIME_MIN_AMOUNT", default=50, cast=int)
AIRTIME_MAX_AMOUNT = config("AIRTIME_MAX_AMOUNT", default=50000, cast=int)
TOPUP_MAX_AMOUNT = config("TOPUP_MAX_AMOUNT", default=500000, cast=int)  # per top-up; 0 = no limit
DAILY_TOPUP_LIMIT = config("DAILY_TOPUP_LIMIT", default=500000, cast=int)  # per user per day; 0 = none

# Verification levels (users/limits.py). "basic" = email confirmed; "verified" = phone confirmed by SMS.
# Basic accounts (and flagged ones) get these daily limits instead of the ones above.
BASIC_DAILY_PURCHASE_LIMIT = config("BASIC_DAILY_PURCHASE_LIMIT", default=10000, cast=int)
BASIC_DAILY_TOPUP_LIMIT = config("BASIC_DAILY_TOPUP_LIMIT", default=10000, cast=int)
# Require a verified phone before the first top-up. Turn on once SMS works (SMS_BACKEND=termii).
TOPUP_REQUIRES_PHONE = env_bool("TOPUP_REQUIRES_PHONE", default=False)
# Fraud checks (users/risk.py): flag an account when the card that paid has funded more than
# CARD_MAX_ACCOUNTS accounts, or its device signed in to more than DEVICE_MAX_ACCOUNTS in 30 days.
CARD_MAX_ACCOUNTS = config("CARD_MAX_ACCOUNTS", default=2, cast=int)
DEVICE_MAX_ACCOUNTS = config("DEVICE_MAX_ACCOUNTS", default=3, cast=int)

# Text messages (notifications/sms.py): "termii" in production, "console" for local development.
SMS_BACKEND = config("SMS_BACKEND", default="console").strip().lower()
TERMII_API_KEY = config("TERMII_API_KEY", default="")
TERMII_SENDER_ID = config("TERMII_SENDER_ID", default="")
TERMII_BASE_URL = config("TERMII_BASE_URL", default="https://api.ng.termii.com").rstrip("/")
TERMII_CHANNEL = config("TERMII_CHANNEL", default="dnd")  # "dnd" reaches numbers on Do-Not-Disturb
if IS_PRODUCTION and TOPUP_REQUIRES_PHONE and SMS_BACKEND != "termii":
    raise ImproperlyConfigured("TOPUP_REQUIRES_PHONE needs SMS_BACKEND=termii, or nobody could top up.")
DAILY_PURCHASE_LIMIT = config("DAILY_PURCHASE_LIMIT", default=200000, cast=int)
# Accounts less than a day old get a lower daily limit (0 = same as everyone).
NEW_ACCOUNT_DAILY_LIMIT = config("NEW_ACCOUNT_DAILY_LIMIT", default=20000, cast=int)

# Purchases need the user's 4-digit transaction PIN. Turn off only while a frontend without a
# PIN screen is still live.
TRANSACTION_PIN_REQUIRED = env_bool("TRANSACTION_PIN_REQUIRED", default=True)

# Data plans are sold at VTpass's price plus this percentage (0 = sell at VTpass's price).
DATA_PRICE_MARKUP_PERCENT = config("DATA_PRICE_MARKUP_PERCENT", default="0", cast=Decimal)

# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------
LOG_LEVEL = "DEBUG" if DEBUG else "INFO"
# json: one JSON object per line (production default, for log search); text: readable lines.
LOG_FORMAT = config("LOG_FORMAT", default="json" if IS_PRODUCTION else "text")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "core.request_id.RequestIdFilter"},
    },
    "formatters": {
        "text": {"format": "[{levelname}] {asctime} {name} [{request_id}] - {message}", "style": "{"},
        "json": {"()": "core.request_id.JsonFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": LOG_FORMAT, "filters": ["request_id"]},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.db.backends": {"level": "WARNING" if not DEBUG else "INFO"},
        "django.request": {"level": "WARNING"},
    },
}

# -------------------------------------------------------------------
# Monitoring
# -------------------------------------------------------------------
# Who gets ops alerts and the daily report (comma-separated emails). Empty: logged only.
OPS_ALERT_EMAILS = config("OPS_ALERT_EMAILS", cast=Csv(), default="")

# Error tracking: set SENTRY_DSN to send errors (and ERROR log lines) to Sentry. Request bodies
# are never sent, so PINs, passwords and codes stay on the server.
SENTRY_DSN = config("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=ENV,
        release=config("APP_VERSION", default="") or config("RENDER_GIT_COMMIT", default="") or None,
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=config("SENTRY_TRACES_SAMPLE_RATE", default=0.0, cast=float),
    )

# >>> NEW: print a clear boot line so you can verify LIVE mode in Render logs
logger = logging.getLogger(__name__)
logger.warning(
    f"BOOT ENV={ENV} DEBUG={DEBUG} PROVIDER_MODE={PROVIDER_MODE} VTPASS_BASE_URL={VTPASS_BASE_URL} PURCHASES_ENABLED={PURCHASES_ENABLED} TOPUPS_ENABLED={TOPUPS_ENABLED}"
)
