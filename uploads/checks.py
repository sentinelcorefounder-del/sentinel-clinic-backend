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
    if options.get("custom_domain") or options.get("querystring_auth") is not True:
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
    if options.get("custom_domain") or options.get("querystring_auth") is not True:
        return [Error("Clinical asset storage is not private.", hint="Remove custom_domain and enable querystring_auth.", id="uploads.E004")]
    return []
