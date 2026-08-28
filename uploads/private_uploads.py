import hashlib
import os
import uuid

from PIL import Image

from uploads.storage import get_private_clinical_storage


def generated_clinical_key(category, uploaded_file):
    extension = os.path.splitext(uploaded_file.name or "")[1].lower()
    if extension not in {".jpg", ".jpeg", ".png", ".pdf"}:
        extension = ""
    return f"clinical-assets/{category}/{uuid.uuid4().hex}{extension}"


def save_private_upload(uploaded_file, *, category):
    uploaded_file.seek(0)
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)

    source_format = ""
    width = height = None
    try:
        with Image.open(uploaded_file) as image:
            source_format = image.format or ""
            width, height = image.size
    except Exception:
        pass
    uploaded_file.seek(0)

    storage = get_private_clinical_storage()
    key = generated_clinical_key(category, uploaded_file)
    saved = storage.save(key, uploaded_file)
    if saved != key:
        storage.delete(saved)
        raise OSError("The private clinical object key could not be reserved safely.")
    if not storage.exists(key):
        try:
            storage.delete(key)
        except Exception:
            pass
        raise OSError("The private clinical object write could not be verified.")
    return {
        "key": key,
        "sha256": digest.hexdigest(),
        "source_format": source_format,
        "width": width,
        "height": height,
    }


def delete_private_object(key):
    if key:
        get_private_clinical_storage().delete(key)
