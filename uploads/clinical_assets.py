from pathlib import PurePosixPath

from django.core.files.base import ContentFile

from uploads.storage import get_private_clinical_storage


def private_asset_key(image_upload):
    if image_upload.storage_kind != "private_clinical" or not image_upload.private_object_key:
        raise ValueError("Image upload is not a private clinical asset.")
    return image_upload.private_object_key


def open_image_upload(image_upload, mode="rb"):
    if image_upload.storage_kind == "private_clinical":
        return get_private_clinical_storage().open(private_asset_key(image_upload), mode)
    return image_upload.image_file.open(mode)


def read_image_upload(image_upload):
    with open_image_upload(image_upload, "rb") as source:
        return source.read()


def image_upload_name(image_upload):
    if image_upload.storage_kind == "private_clinical":
        return PurePosixPath(private_asset_key(image_upload)).name
    return image_upload.image_file.name


def save_private_copy(*, key, source):
    storage = get_private_clinical_storage()
    if storage.exists(key):
        return key
    saved = storage.save(key, ContentFile(source.read()))
    if saved != key:
        try:
            storage.delete(saved)
        finally:
            if not storage.exists(key):
                raise OSError("Private object key collision.")
    if not storage.exists(key):
        raise OSError("Private object write could not be verified.")
    return key
