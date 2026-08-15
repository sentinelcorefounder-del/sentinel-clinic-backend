from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage, storages


class BulkStagingConfigurationError(ImproperlyConfigured):
    pass


class PrivateClinicalStorageConfigurationError(ImproperlyConfigured):
    pass


def get_bulk_staging_storage():
    """Return isolated staging storage; never fall back to public/default media."""
    if not settings.BULK_STAGING_USE_PRIVATE_OBJECT_STORAGE:
        root = Path(settings.BULK_STAGING_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        return FileSystemStorage(location=root, base_url=None)

    required = getattr(settings, "BULK_STAGING_REQUIRED_SETTINGS", ())
    missing = [name for name in required if not getattr(settings, name, None)]
    if missing:
        raise BulkStagingConfigurationError(
            "Private bulk-import staging storage is not configured."
        )

    try:
        storage = storages["bulk_staging"]
    except (KeyError, InvalidStorageError) as exc:
        raise BulkStagingConfigurationError(
            "Private bulk-import staging storage is unavailable."
        ) from exc

    if getattr(storage, "custom_domain", None) or not getattr(
        storage, "querystring_auth", False
    ):
        raise BulkStagingConfigurationError(
            "Bulk-import staging storage must use private signed access."
        )
    return storage


def get_private_clinical_storage():
    """Return permanent private clinical storage; never use default/public media."""
    if not settings.CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE:
        root = Path(settings.PRIVATE_CLINICAL_ASSETS_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        return FileSystemStorage(location=root, base_url=None)

    required = getattr(settings, "CLINICAL_ASSETS_REQUIRED_SETTINGS", ())
    if any(not getattr(settings, name, None) for name in required):
        raise PrivateClinicalStorageConfigurationError(
            "Private clinical asset storage is not configured."
        )
    try:
        storage = storages["private_clinical_assets"]
    except (KeyError, InvalidStorageError) as exc:
        raise PrivateClinicalStorageConfigurationError(
            "Private clinical asset storage is unavailable."
        ) from exc
    if getattr(storage, "custom_domain", None) or not getattr(
        storage, "querystring_auth", False
    ):
        raise PrivateClinicalStorageConfigurationError(
            "Clinical asset storage must use private signed access."
        )
    return storage


try:
    from django.core.files.storage.handler import InvalidStorageError
except ImportError:  # pragma: no cover - compatibility with older Django
    InvalidStorageError = KeyError
