from pathlib import PurePosixPath

from uploads.storage import get_private_clinical_storage


def open_ocular_investigation(investigation, mode="rb"):
    if investigation.storage_kind == "private_clinical":
        file_obj = get_private_clinical_storage().open(
            investigation.private_object_key, mode
        )
        file_obj.name = PurePosixPath(investigation.private_object_key).name
        return file_obj
    return investigation.file.open(mode)
