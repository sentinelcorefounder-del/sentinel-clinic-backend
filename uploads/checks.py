from django.conf import settings
from django.core.checks import Error, register


@register()
def private_bulk_staging_check(app_configs, **kwargs):
    if not settings.BULK_STAGING_USE_PRIVATE_OBJECT_STORAGE:
        return []
    missing = [
        name
        for name in getattr(settings, "BULK_STAGING_REQUIRED_SETTINGS", ())
        if not getattr(settings, name, None)
    ]
    if missing:
        return [
            Error(
                "Private bulk-import staging storage is not configured.",
                hint="Configure the dedicated private staging bucket variables.",
                id="uploads.E001",
            )
        ]
    config = settings.STORAGES.get("bulk_staging", {})
    options = config.get("OPTIONS", {})
    expected_bucket = getattr(settings, "BULK_STAGING_R2_BUCKET_NAME", None)
    expected_access_key = getattr(settings, "BULK_STAGING_R2_ACCESS_KEY_ID", None)
    if (
        options.get("custom_domain")
        or options.get("querystring_auth") is not True
        or options.get("default_acl") != "private"
        or (expected_bucket and options.get("bucket_name") != expected_bucket)
        or (expected_access_key and options.get("access_key") != expected_access_key)
    ):
        return [
            Error(
                "Bulk-import staging storage is not private.",
                hint="Remove custom_domain and enable querystring_auth.",
                id="uploads.E002",
            )
        ]
    return []


@register()
def private_clinical_assets_check(app_configs, **kwargs):
    if not settings.CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE:
        return []
    missing = [name for name in settings.CLINICAL_ASSETS_REQUIRED_SETTINGS if not getattr(settings, name, None)]
    if missing:
        return [Error("Private clinical asset storage is not configured.", hint="Configure the dedicated clinical asset bucket variables.", id="uploads.E003")]
    options = settings.STORAGES.get("private_clinical_assets", {}).get("OPTIONS", {})
    expected_bucket = getattr(settings, "CLINICAL_ASSETS_R2_BUCKET_NAME", None)
    expected_access_key = getattr(settings, "CLINICAL_ASSETS_R2_ACCESS_KEY_ID", None)
    if (
        options.get("custom_domain")
        or options.get("querystring_auth") is not True
        or options.get("default_acl") != "private"
        or (expected_bucket and options.get("bucket_name") != expected_bucket)
        or (expected_access_key and options.get("access_key") != expected_access_key)
    ):
        return [Error("Clinical asset storage is not private.", hint="Remove custom_domain and enable querystring_auth.", id="uploads.E004")]
    return []


@register()
def private_storage_separation_check(app_configs, **kwargs):
    if not (
        settings.BULK_STAGING_USE_PRIVATE_OBJECT_STORAGE
        and settings.CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE
    ):
        return []

    staging_bucket = getattr(settings, "BULK_STAGING_R2_BUCKET_NAME", None)
    clinical_bucket = getattr(settings, "CLINICAL_ASSETS_R2_BUCKET_NAME", None)
    default_bucket = getattr(settings, "R2_BUCKET_NAME", None)
    staging_access_key = getattr(settings, "BULK_STAGING_R2_ACCESS_KEY_ID", None)
    clinical_access_key = getattr(settings, "CLINICAL_ASSETS_R2_ACCESS_KEY_ID", None)

    if not all((staging_bucket, clinical_bucket, staging_access_key, clinical_access_key)):
        return []  # The per-storage checks report missing required configuration.
    if staging_bucket == clinical_bucket:
        return [Error(
            "Private staging and clinical assets must use different buckets.",
            hint="Configure separate dedicated private bucket names.",
            id="uploads.E005",
        )]
    if default_bucket and staging_bucket == default_bucket:
        return [Error(
            "Private staging must not use the default media bucket.",
            hint="Configure a dedicated private staging bucket.",
            id="uploads.E006",
        )]
    if default_bucket and clinical_bucket == default_bucket:
        return [Error(
            "Private clinical assets must not use the default media bucket.",
            hint="Configure a dedicated private clinical-assets bucket.",
            id="uploads.E007",
        )]
    if staging_access_key == clinical_access_key:
        return [Error(
            "Private staging and clinical assets must use separate credentials.",
            hint="Configure separate bucket-scoped access key IDs.",
            id="uploads.E008",
        )]
    return []
