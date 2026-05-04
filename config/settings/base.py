from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-local-dev-key")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",

    # Project apps
    "users",
    "organizations",
    "patients",
    "referrals",
    "appointments",
    "encounters",
    "uploads",
    "reports",
    "consents",
    "audit",
    "dashboard",
    "payments",
    "ops",
]

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
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# -------------------------------
# COOKIES / CROSS-SUBDOMAIN AUTH
# -------------------------------
SESSION_COOKIE_DOMAIN = os.environ.get("SESSION_COOKIE_DOMAIN") or None
CSRF_COOKIE_DOMAIN = os.environ.get("CSRF_COOKIE_DOMAIN") or None

SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "False") == "True"
CSRF_COOKIE_SECURE = os.environ.get("CSRF_COOKIE_SECURE", "False") == "True"

SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
CSRF_COOKIE_SAMESITE = os.environ.get("CSRF_COOKIE_SAMESITE", "Lax")

# CORS
CORS_ALLOW_CREDENTIALS = True

# CORS
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = [
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "https://usesentinelhealth.com",
    "https://www.usesentinelhealth.com",
    "https://clinic.usesentinelhealth.com",
    "https://ops.usesentinelhealth.com",
]

CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://usesentinelhealth\.com$",
    r"^https://www\.usesentinelhealth\.com$",
    r"^https://clinic\.usesentinelhealth\.com$",
    r"^https://ops\.usesentinelhealth\.com$",
]

CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "https://usesentinelhealth.com",
    "https://www.usesentinelhealth.com",
    "https://clinic.usesentinelhealth.com",
    "https://ops.usesentinelhealth.com",
]

# DRF
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -------------------------------
# PAYSTACK
# -------------------------------
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYSTACK_BASE_URL = os.environ.get("PAYSTACK_BASE_URL", "https://api.paystack.co")

# -------------------------------
# BASEROW
# -------------------------------
BASEROW_API_TOKEN = os.environ.get("BASEROW_API_TOKEN", "")
BASEROW_BASE_URL = os.environ.get("BASEROW_BASE_URL", "https://api.baserow.io")

BASEROW_PAYMENTS_TABLE_ID = os.environ.get("BASEROW_PAYMENTS_TABLE_ID", "")
BASEROW_REFERRALS_TABLE_ID = os.environ.get("BASEROW_REFERRALS_TABLE_ID", "")
BASEROW_HOSPITAL_INTAKE_TABLE_ID = os.environ.get("BASEROW_HOSPITAL_INTAKE_TABLE_ID", "")
BASEROW_HOSPITALS_TABLE_ID = os.environ.get("BASEROW_HOSPITALS_TABLE_ID", "")

# -------------------------------
# FRONTEND
# -------------------------------
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

# -------------------------------
# EMAIL CONFIG
# -------------------------------
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "no-reply@example.com")

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "sentinelhealthops@gmail.com")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
# -------------------------------
# AI INTEGRATION
# -------------------------------
AI_PROVIDER = os.environ.get("AI_PROVIDER", "sentinel")

# Sentinel AI Flask backend
SENTINEL_AI_BASE_URL = os.environ.get(
    "SENTINEL_AI_BASE_URL",
    "https://sentinel-ai1.onrender.com"
)
SENTINEL_AI_ANALYZE_PATH = os.environ.get(
    "SENTINEL_AI_ANALYZE_PATH",
    "/analyze"
)

# OpenAI Vision support
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_VISION_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4.1-mini")
