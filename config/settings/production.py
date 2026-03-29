from .base import *
import dj_database_url

DEBUG = False

ALLOWED_HOSTS = [
    "your-backend-name.onrender.com",
]

DATABASES = {
    "default": dj_database_url.config(conn_max_age=600, ssl_require=True)
}

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_CONTENT_TYPE_NOSNIFF = True

CORS_ALLOWED_ORIGINS = [
    "https://your-frontend.vercel.app",
]

CSRF_TRUSTED_ORIGINS = [
    "https://your-backend-name.onrender.com",
    "https://your-frontend.vercel.app",
]

MIDDLEWARE.insert(2, "whitenoise.middleware.WhiteNoiseMiddleware")

STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}