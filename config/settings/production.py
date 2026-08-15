from .base import *
import os
import dj_database_url

DEBUG = False

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        "ALLOWED_HOSTS",
        "sentinel-clinic-backend.onrender.com,api.usesentinelhealth.com",
    ).split(",")
    if host.strip()
]

DATABASES = {
    "default": dj_database_url.config(conn_max_age=600, ssl_require=True)
}

CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CORS_ALLOWED_ORIGINS",
        "https://sentinel-clinic-frontend.vercel.app,"
        "https://clinic.usesentinelhealth.com,"
        "https://usesentinelhealth.com,"
        "https://www.usesentinelhealth.com,"
        "https://ops.usesentinelhealth.com",
    ).split(",")
    if origin.strip()
]

CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://sentinel-clinic-frontend.*\.vercel\.app$",
    r"^https://usesentinelhealth\.com$",
    r"^https://www\.usesentinelhealth\.com$",
    r"^https://clinic\.usesentinelhealth\.com$",
    r"^https://ops\.usesentinelhealth\.com$",
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "CSRF_TRUSTED_ORIGINS",
        "https://sentinel-clinic-frontend.vercel.app,"
        "https://clinic.usesentinelhealth.com,"
        "https://usesentinelhealth.com,"
        "https://www.usesentinelhealth.com,"
        "https://ops.usesentinelhealth.com",
    ).split(",")
    if origin.strip()
]

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "None"
CSRF_COOKIE_SAMESITE = "None"

SESSION_COOKIE_DOMAIN = os.environ.get("SESSION_COOKIE_DOMAIN", ".usesentinelhealth.com")
CSRF_COOKIE_DOMAIN = os.environ.get("CSRF_COOKIE_DOMAIN", ".usesentinelhealth.com")

SECURE_HSTS_SECONDS = 31536000
SECURE_CONTENT_TYPE_NOSNIFF = True

if "whitenoise.middleware.WhiteNoiseMiddleware" not in MIDDLEWARE:
    MIDDLEWARE.insert(2, "whitenoise.middleware.WhiteNoiseMiddleware")

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL")

BULK_STAGING_R2_ACCOUNT_ID = os.environ.get("BULK_STAGING_R2_ACCOUNT_ID")
BULK_STAGING_R2_ACCESS_KEY_ID = os.environ.get("BULK_STAGING_R2_ACCESS_KEY_ID")
BULK_STAGING_R2_SECRET_ACCESS_KEY = os.environ.get("BULK_STAGING_R2_SECRET_ACCESS_KEY")
BULK_STAGING_R2_BUCKET_NAME = os.environ.get("BULK_STAGING_R2_BUCKET_NAME")
BULK_STAGING_USE_PRIVATE_OBJECT_STORAGE = True
BULK_STAGING_REQUIRED_SETTINGS = (
    "BULK_STAGING_R2_ACCOUNT_ID",
    "BULK_STAGING_R2_ACCESS_KEY_ID",
    "BULK_STAGING_R2_SECRET_ACCESS_KEY",
    "BULK_STAGING_R2_BUCKET_NAME",
)
CLINICAL_ASSETS_R2_ACCOUNT_ID = os.environ.get("CLINICAL_ASSETS_R2_ACCOUNT_ID")
CLINICAL_ASSETS_R2_ACCESS_KEY_ID = os.environ.get("CLINICAL_ASSETS_R2_ACCESS_KEY_ID")
CLINICAL_ASSETS_R2_SECRET_ACCESS_KEY = os.environ.get("CLINICAL_ASSETS_R2_SECRET_ACCESS_KEY")
CLINICAL_ASSETS_R2_BUCKET_NAME = os.environ.get("CLINICAL_ASSETS_R2_BUCKET_NAME")
CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE = True
CLINICAL_ASSETS_REQUIRED_SETTINGS = (
    "CLINICAL_ASSETS_R2_ACCOUNT_ID", "CLINICAL_ASSETS_R2_ACCESS_KEY_ID",
    "CLINICAL_ASSETS_R2_SECRET_ACCESS_KEY", "CLINICAL_ASSETS_R2_BUCKET_NAME",
)

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": R2_ACCESS_KEY_ID,
            "secret_key": R2_SECRET_ACCESS_KEY,
            "bucket_name": R2_BUCKET_NAME,
            "endpoint_url": f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            "region_name": "auto",
            "default_acl": None,
            "querystring_auth": False,
            "custom_domain": R2_PUBLIC_URL.replace("https://", "") if R2_PUBLIC_URL else None,
        },
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
    "bulk_staging": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": BULK_STAGING_R2_ACCESS_KEY_ID,
            "secret_key": BULK_STAGING_R2_SECRET_ACCESS_KEY,
            "bucket_name": BULK_STAGING_R2_BUCKET_NAME,
            "endpoint_url": (
                f"https://{BULK_STAGING_R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
            ),
            "region_name": "auto",
            "default_acl": "private",
            "querystring_auth": True,
            "custom_domain": None,
            "file_overwrite": False,
        },
    },
    "private_clinical_assets": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": CLINICAL_ASSETS_R2_ACCESS_KEY_ID,
            "secret_key": CLINICAL_ASSETS_R2_SECRET_ACCESS_KEY,
            "bucket_name": CLINICAL_ASSETS_R2_BUCKET_NAME,
            "endpoint_url": f"https://{CLINICAL_ASSETS_R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            "region_name": "auto", "default_acl": "private",
            "querystring_auth": True, "custom_domain": None, "file_overwrite": False,
        },
    },
}
