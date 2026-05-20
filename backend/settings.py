"""Django settings for the S3 Bucket Manager backend."""

import os
from pathlib import Path
from datetime import timedelta
from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent

# Security
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-in-production")
DEBUG = os.getenv("DJANGO_DEBUG", "False") == "True"
ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost").split(",")

if not DEBUG and SECRET_KEY == "dev-secret-key-change-in-production":
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be set in non-debug environments"
    )


def require_non_debug_env(var_name: str, value: str) -> str:
    if not DEBUG and not value:
        raise ImproperlyConfigured(f"{var_name} must be set in non-debug environments")
    return value


# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # REST Framework
    "rest_framework",
    "rest_framework_simplejwt",
    # OAuth2/Social Auth (for Authentik OIDC)
    "social_django",
    # Our application
    "storage",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Social auth middleware for OAuth2 state management
    "social_django.middleware.SocialAuthExceptionMiddleware",
]

ROOT_URLCONF = "urls"

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
                "social_django.context_processors.backends",
                "social_django.context_processors.login_redirect",
            ],
        },
    },
]

WSGI_APPLICATION = "wsgi.application"

# Database
if os.getenv("DATABASE_HOST"):
    database_password = os.getenv("DATABASE_PASSWORD", "")
    require_non_debug_env("DATABASE_PASSWORD", database_password)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DATABASE_NAME", "djangodb"),
            "USER": os.getenv("DATABASE_USER", "djangouser"),
            "PASSWORD": database_password,
            "HOST": os.getenv("DATABASE_HOST", "localhost"),
            "PORT": os.getenv("DATABASE_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

# Custom user model
AUTH_USER_MODEL = "storage.User"

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        # Session auth is required during OAuth2 callback/token exchange.
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_RENDERER_CLASSES": ("rest_framework.renderers.JSONRenderer",),
    "DEFAULT_PARSER_CLASSES": (
        "rest_framework.parsers.JSONParser",
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
    ),
}

# JWT

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=60),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# OAuth2/OIDC (Authentik)

AUTHENTICATION_BACKENDS = (
    "storage.backends.AuthentikOAuth2",
    "django.contrib.auth.backends.ModelBackend",
)

AUTHENTIK_URL = os.getenv("AUTHENTIK_URL", "http://authentik-service:9000")
# OIDC_APPLICATION_SLUG is the Authentik application slug — the path component in the OIDC
# discovery URL: {AUTHENTIK_URL}/application/o/{slug}/.well-known/openid-configuration
# Dev: "bucket-explorer"  |  Prod: the slug the admin used (e.g. "buckets-explorer")
OIDC_APPLICATION_SLUG = os.getenv("OIDC_APPLICATION_SLUG", "bucket-explorer")
SOCIAL_AUTH_AUTHENTIK_OIDC_ENDPOINT = f"{AUTHENTIK_URL}/application/o/{OIDC_APPLICATION_SLUG}"

SOCIAL_AUTH_AUTHENTIK_KEY = os.getenv("OIDC_CLIENT_ID", "bucket-explorer")
SOCIAL_AUTH_AUTHENTIK_SECRET = require_non_debug_env(
    "OIDC_CLIENT_SECRET",
    os.getenv("OIDC_CLIENT_SECRET", ""),
)

# Browser-facing URL used for OAuth2 authorize redirects.
AUTHENTIK_EXTERNAL_URL = os.getenv("AUTHENTIK_EXTERNAL_URL", "http://localhost:9000")
SOCIAL_AUTH_AUTHENTIK_AUTHORIZATION_URL = (
    f"{AUTHENTIK_EXTERNAL_URL}/application/o/authorize/"
)

SOCIAL_AUTH_AUTHENTIK_SCOPE = ["openid", "profile", "email", "groups"]

# Keep auth extra args empty to avoid provider-specific login loops.
SOCIAL_AUTH_AUTHENTIK_AUTH_EXTRA_ARGUMENTS = {}

# After OAuth2, hand off to the frontend callback route.
LOGIN_REDIRECT_URL = "/auth/callback"
LOGOUT_REDIRECT_URL = "/"
LOGIN_URL = "/api/oauth/login/authentik/"

# User provisioning pipeline
SOCIAL_AUTH_PIPELINE = (
    "social_core.pipeline.social_auth.social_details",
    "storage.pipeline.validate_required_claims",
    "storage.pipeline.extract_external_id",
    "storage.pipeline.extract_federation_fields",
    "social_core.pipeline.social_auth.social_uid",
    "social_core.pipeline.social_auth.social_user",
    "storage.pipeline.associate_by_ceph_username",
    "storage.pipeline.associate_by_email",
    "storage.pipeline.generate_username_with_fallback",
    "social_core.pipeline.user.create_user",
    "storage.pipeline.create_or_update_user",
    "social_core.pipeline.social_auth.associate_user",
    "social_core.pipeline.social_auth.load_extra_data",
    "social_core.pipeline.user.user_details",
    "storage.pipeline.extract_tenant_info",
    "storage.pipeline.log_user_login",
)

SOCIAL_AUTH_AUTHENTIK_USER_FIELDS = ["email", "username", "external_id", "idp_source"]

# Session configuration (OAuth2 handshake only)

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "bucket_manager_oauth_session"
SESSION_COOKIE_AGE = 300  # 5 minutes - only needed during OAuth2 handshake
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

# S3-compatible storage (Ceph RGW)

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "https://192.168.132.110")
S3_ACCESS_KEY = require_non_debug_env("S3_ACCESS_KEY", os.getenv("S3_ACCESS_KEY", ""))
S3_SECRET_KEY = require_non_debug_env("S3_SECRET_KEY", os.getenv("S3_SECRET_KEY", ""))
S3_VERIFY_SSL = os.getenv("S3_VERIFY_SSL", "True") == "True"
S3_REGION = os.getenv("S3_REGION", "us-east-1")

# RGWSquared service

RGWSQUARED_URL = os.getenv("RGWSQUARED_URL", "http://localhost:3001")
RGWSQUARED_USERNAME = require_non_debug_env(
    "RGWSQUARED_USERNAME", os.getenv("RGWSQUARED_USERNAME", "")
)
RGWSQUARED_PASSWORD = require_non_debug_env(
    "RGWSQUARED_PASSWORD", os.getenv("RGWSQUARED_PASSWORD", "")
)

# Logging

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
    },
    "loggers": {
        "social_core": {
            "handlers": ["console"],
            "level": os.getenv("OAUTH_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
    },
}
