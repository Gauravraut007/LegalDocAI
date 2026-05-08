"""Base settings shared by development and production.

Phase 1 = skeleton only. No business apps installed yet.
"""
from __future__ import annotations

import logging
import sys
from datetime import timedelta
from pathlib import Path

import structlog
from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# --------------------------------------------------------------- core ----
SECRET_KEY = config("DJANGO_SECRET_KEY", default="dev-secret-change-me")
DEBUG = config("DJANGO_DEBUG", default=True, cast=bool)
ALLOWED_HOSTS = config(
    "DJANGO_ALLOWED_HOSTS",
    default="localhost,127.0.0.1,django,fastapi",
    cast=Csv(),
)

# --------------------------------------------------------------- apps ----
INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "channels",
    # business apps
    "apps.users",
    "apps.workspaces",
    "apps.documents",
    "apps.chat",
    "apps.web",
]

AUTH_USER_MODEL = "users.User"

LOGIN_URL = "web:login"
LOGIN_REDIRECT_URL = "web:dashboard"
LOGOUT_REDIRECT_URL = "web:login"

# Map Django messages levels onto Bootstrap alert variants.
from django.contrib.messages import constants as _msg  # noqa: E402

MESSAGE_TAGS = {
    _msg.DEBUG: "secondary",
    _msg.INFO: "info",
    _msg.SUCCESS: "success",
    _msg.WARNING: "warning",
    _msg.ERROR: "danger",
}

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "common.middleware.RequestIDMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.web.context_processors.workspace_context",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"

# --------------------------------------------------------- database ------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DJANGO_DB_NAME", default="django_db"),
        "USER": config("DJANGO_DB_USER", default="postgres"),
        "PASSWORD": config("DJANGO_DB_PASSWORD", default="postgres"),
        "HOST": config("DJANGO_DB_HOST", default="postgres"),
        "PORT": config("DJANGO_DB_PORT", default="5432"),
        "CONN_MAX_AGE": 300,
    }
}

# --------------------------------------------------------- auth ---------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "apps.users.validators.ComplexityValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------- static/media -
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "apps" / "web" / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = config("MEDIA_ROOT", default="/data/uploads")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------- DRF ----------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "120/minute",
        "anon": "20/minute",
        "login": "5/minute",
        "register": "10/minute",
        "password_reset": "5/minute",
    },
    "DEFAULT_PAGINATION_CLASS": "common.pagination.StandardResultsPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "common.exceptions.custom_exception_handler",
}

# --------------------------------------------------------- JWT ----------
# JWT_SIGNING_KEY is shared with the FastAPI service for cross-service auth.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=config("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", default=15, cast=int)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=config("JWT_REFRESH_TOKEN_EXPIRE_DAYS", default=7, cast=int)
    ),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
    "ALGORITHM": config("JWT_ALGORITHM", default="HS256"),
    "SIGNING_KEY": config("JWT_SIGNING_KEY", default=SECRET_KEY),
    "ISSUER": config("JWT_ISSUER", default="django-service"),
    "TOKEN_OBTAIN_SERIALIZER": "apps.users.serializers.LDIPTokenObtainPairSerializer",
}

# --------------------------------------------------------- spectacular --
SPECTACULAR_SETTINGS = {
    "TITLE": "Legal Document Intelligence - Django API",
    "DESCRIPTION": "Auth, workspaces, documents, chat orchestration.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "TAGS": [
        {"name": "auth", "description": "Registration, login, JWT lifecycle, profile."},
        {"name": "workspaces", "description": "Workspaces, members, invitations."},
        {"name": "documents", "description": "Document upload + status (gateway to FastAPI)."},
        {"name": "chat", "description": "Chat sessions + streaming RAG (gateway to FastAPI)."},
    ],
    "COMPONENT_SPLIT_REQUEST": True,
}

EMAIL_BACKEND = config(
    "DJANGO_EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="no-reply@ldip.local")
FRONTEND_BASE_URL = config("FRONTEND_BASE_URL", default="http://localhost:3000")

# --------------------------------------------------------- redis --------
REDIS_URL = config("REDIS_URL", default="redis://redis:6379/0")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
        "TIMEOUT": 300,
    }
}

# --------------------------------------------------------- CORS ---------
CORS_ALLOWED_ORIGINS = config(
    "CORS_ORIGINS",
    default="http://localhost:3000,http://localhost:8000",
    cast=Csv(),
)
CORS_ALLOW_CREDENTIALS = True

# --------------------------------------------------------- FastAPI link -
FASTAPI_BASE_URL = config("FASTAPI_BASE_URL", default="http://fastapi:8001")
FASTAPI_TIMEOUT_SECONDS = config("FASTAPI_TIMEOUT_SECONDS", default=120, cast=int)

# --------------------------------------------------------- LOGGING ------
LOG_LEVEL = config("LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "structlog.stdlib.ProcessorFormatter",
            "processor": structlog.processors.JSONRenderer(),
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "stream": sys.stdout,
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

# --------------------------------------------------------- Sentry -------
SENTRY_DSN = config("SENTRY_DSN", default="")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
