import hashlib
import io
import mimetypes
import os
import re
import stat
import uuid
import warnings
import zipfile
from datetime import datetime, timedelta

from PIL import Image
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from encounters.models import ScreeningEncounter
from ops.models import OpsAuditLog
from organizations.services.branches import user_can_access_branch
from referrals.models import HospitalReferral
from uploads.models import (
    BulkImageAttachment,
    BulkImageImport,
    BulkImageImportGroup,
    BulkImageImportItem,
    ImageUpload,
)
from uploads.storage import get_bulk_staging_storage
from uploads.clinical_assets import save_private_copy
from uploads.storage import get_private_clinical_storage


ROOT_PATTERN = re.compile(
    r"^.+_(?P<mrn>[^_/]+)_(?P<date>\d{2}-\d{2}-\d{4})$"
)
IMAGE_EXTENSIONS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}
MAGIC = {"JPEG": lambda b: b.startswith(b"\xff\xd8\xff"), "PNG": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")}


def _safe_audit(actor, action, bulk_import, **metadata):
    OpsAuditLog.objects.create(
        actor=actor,
        action=action,
        entity_type="bulk_image_import",
        entity_id=str(bulk_import.import_id),
        entity_label=str(bulk_import.import_id),
        message=f"Bulk image import {action.replace('_', ' ')}.",
        metadata=metadata,
    )


def _organization_for(user):
    try:
        return user.organization_link.organization
    except Exception:
        return None


def assert_import_scope(user, bulk_import, write=False):
    roles = set(user.groups.values_list("name", flat=True))
    internal = user.is_superuser or bool(roles & {"ops_admin", "sentinel_ops", "super_admin"})
    allowed = internal or bool(roles & {"clinic_screener", "clinic_admin"})
    if not allowed:
        raise ValidationError("This import is not available.")
    if not internal and _organization_for(user) != bulk_import.organization:
        raise ValidationError("This import is not available.")
    if not user_can_access_branch(user, bulk_import.branch):
        raise ValidationError("This import is not available.")
    if write and not (user.is_superuser or roles & {"clinic_screener", "clinic_admin", "super_admin", "ops_admin", "sentinel_ops"}):
        raise ValidationError("You do not have permission to manage image imports.")


def _safe_member(info):
    name = info.filename.replace("\\", "/")
    if "\x00" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return False
    parts = [part for part in name.split("/") if part not in {"", "."}]
    if ".." in parts:
        return False
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if stat.S_ISLNK(mode) or (file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
        return False
    return True


def _validate_archive_infos(infos, compressed_total=None):
    total = 0
    compressed = 0
    for info in infos:
        if not _safe_member(info) or info.flag_bits & 0x1:
            raise ValidationError("Archive contains an unsafe entry.")
        total += info.file_size
        compressed += info.compress_size
        ratio = info.file_size / max(info.compress_size, 1)
        if ratio > settings.BULK_IMPORT_MAX_COMPRESSION_RATIO:
            raise ValidationError("Archive compression ratio exceeds the safe limit.")
    if total > settings.BULK_IMPORT_MAX_UNCOMPRESSED_BYTES:
        raise ValidationError("Archive expanded size exceeds the safe limit.")
    base = compressed_total if compressed_total is not None else compressed
    if total / max(base, 1) > settings.BULK_IMPORT_MAX_COMPRESSION_RATIO:
        raise ValidationError("Archive compression ratio exceeds the safe limit.")
    return total


def parse_remidio_root(root):
    match = ROOT_PATTERN.fullmatch(root)
    if not match:
        return None, None
    mrn = match.group("mrn")
    if not mrn or len(mrn) > 100:
        return None, None
    try:
        assessment_date = datetime.strptime(match.group("date"), "%d-%m-%Y").date()
    except ValueError:
        return None, None
    return mrn, assessment_date


def _validate_image(data, extension):
    if len(data) > settings.BULK_IMPORT_MAX_IMAGE_BYTES:
        raise ValidationError("An image exceeds the safe size limit.")
    expected = IMAGE_EXTENSIONS.get(extension.lower())
    if not expected or not MAGIC[expected](data):
        raise ValidationError("Image extension and content do not agree.")
    guessed, _ = mimetypes.guess_type(f"image{extension.lower()}")
    if guessed not in {"image/jpeg", "image/png"}:
        raise ValidationError("Image media type is unsupported.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                if image.format != expected or getattr(image, "n_frames", 1) != 1:
                    raise ValidationError("Image format is unsupported.")
                if width > settings.BULK_IMPORT_MAX_IMAGE_WIDTH or height > settings.BULK_IMPORT_MAX_IMAGE_HEIGHT or width * height > settings.BULK_IMPORT_MAX_IMAGE_PIXELS:
                    raise ValidationError("Image dimensions exceed the safe limit.")
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                if image.format != expected or getattr(image, "n_frames", 1) != 1:
                    raise ValidationError("Image format is unsupported.")
    except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError("Image is corrupt or unsafe.") from exc
    return expected, width, height


def _candidate_encounters(bulk_import, mrn, assessment_date):
    referrals = HospitalReferral.objects.filter(hospital_mrn=mrn, patient__isnull=False)
    if bulk_import.organization.organization_type == "hospital":
        referrals = referrals.filter(source_hospital=bulk_import.organization)
    else:
        referrals = referrals.filter(matched_clinic=bulk_import.organization)
    referral_ids = [row.id for row in referrals.only("id", "hospital_mrn") if row.hospital_mrn == mrn]
    return ScreeningEncounter.objects.filter(
        hospital_referral_id__in=referral_ids,
        service_session=bulk_import.service_session,
        service_branch=bulk_import.branch,
        encounter_date=assessment_date,
    ).distinct()


def create_bulk_import(*, user, service_session, branch, uploaded_file, idempotency_key):
    if uploaded_file.size > settings.BULK_IMPORT_MAX_COMPRESSED_BYTES:
        raise ValidationError("Archive exceeds the compressed upload limit.")
    organization = service_session.participating_organization
    draft = BulkImageImport(
        organization=organization, branch=branch, service_session=service_session,
        archive_checksum_sha256="0" * 64, idempotency_key=idempotency_key,
        created_by=user, expires_at=timezone.now() + timedelta(hours=settings.BULK_IMPORT_EXPIRY_HOURS),
    )
    assert_import_scope(user, draft, write=True)
    archive = uploaded_file.read()
    checksum = hashlib.sha256(archive).hexdigest()
    existing_archive = BulkImageImport.objects.filter(
        organization=organization,
        service_session=service_session,
        archive_checksum_sha256=checksum,
        status__in=["processing", "preview", "confirmed"],
    ).first()
    if existing_archive:
        return existing_archive
    existing = BulkImageImport.objects.filter(organization=organization, idempotency_key=idempotency_key).first()
    if existing:
        if existing.archive_checksum_sha256 != checksum:
            raise ValidationError("Idempotency key was already used for another archive.")
        return existing
    try:
        bulk_import = BulkImageImport.objects.create(
            organization=organization, branch=branch, service_session=service_session,
            archive_checksum_sha256=checksum, idempotency_key=idempotency_key,
            created_by=user, expires_at=draft.expires_at,
        )
    except IntegrityError:
        existing_archive = BulkImageImport.objects.filter(
            organization=organization,
            service_session=service_session,
            archive_checksum_sha256=checksum,
            status__in=["processing", "preview", "confirmed"],
        ).first()
        if existing_archive:
            return existing_archive
        existing = BulkImageImport.objects.filter(
            organization=organization, idempotency_key=idempotency_key
        ).first()
        if existing and existing.archive_checksum_sha256 == checksum:
            return existing
        raise ValidationError("Idempotency key was already used for another archive.")
    storage = get_bulk_staging_storage()
    staged = []
    try:
        outer = zipfile.ZipFile(io.BytesIO(archive))
        _validate_archive_infos(outer.infolist(), len(archive))
        inner_infos = [x for x in outer.infolist() if not x.is_dir() and x.filename.lower().endswith(".zip")]
        other_outer = [x for x in outer.infolist() if not x.is_dir() and not x.filename.lower().endswith(".zip")]
        if other_outer or not inner_infos:
            raise ValidationError("Archive does not match the supported Remidio package structure.")
        image_total = 0
        expanded_total = 0
        seen_checksums = set()
        for group_index, inner_info in enumerate(inner_infos, 1):
            inner_data = outer.read(inner_info)
            inner = zipfile.ZipFile(io.BytesIO(inner_data))
            infos = [x for x in inner.infolist() if not x.is_dir()]
            expanded_total += _validate_archive_infos(infos, len(inner_data))
            if expanded_total > settings.BULK_IMPORT_MAX_UNCOMPRESSED_BYTES or expanded_total / max(len(archive), 1) > settings.BULK_IMPORT_MAX_COMPRESSION_RATIO:
                raise ValidationError("Archive expanded size or compression ratio exceeds the safe limit.")
            if len(infos) > settings.BULK_IMPORT_MAX_IMAGES + 20:
                raise ValidationError("A package contains too many entries.")
            if any(x.filename.lower().endswith(".zip") for x in infos):
                raise ValidationError("Archives nested beyond one inner level are not supported.")
            roots = {x.filename.replace("\\", "/").split("/")[0] for x in infos}
            conflicting_roots = len(roots) != 1
            mrn, assessment_date = (None, None) if conflicting_roots else parse_remidio_root(next(iter(roots)))
            group = BulkImageImportGroup.objects.create(
                bulk_import=bulk_import, source_index=group_index, mrn=mrn or "",
                assessment_date=assessment_date,
                status="invalid" if conflicting_roots else ("unresolved" if not mrn else "proposed"),
                safe_issue_code="conflicting_patient_roots" if conflicting_roots else ("invalid_identity_boundary" if not mrn else ""),
            )
            if assessment_date and assessment_date != bulk_import.service_session.service_date:
                group.status = "unresolved"
                group.safe_issue_code = "session_date_mismatch"
            candidates = _candidate_encounters(bulk_import, mrn, assessment_date) if mrn else ScreeningEncounter.objects.none()
            if group.safe_issue_code != "session_date_mismatch" and candidates.count() == 1:
                group.proposed_encounter = candidates.first()
            elif mrn and group.safe_issue_code != "session_date_mismatch":
                group.status = "unresolved"
                group.safe_issue_code = "ambiguous_or_unmatched_mrn"
            group.save(update_fields=["proposed_encounter", "status", "safe_issue_code"])
            for item_index, info in enumerate(infos, 1):
                ext = os.path.splitext(info.filename)[1].lower()
                if info.file_size > settings.BULK_IMPORT_MAX_IMAGE_BYTES:
                    raise ValidationError("A package item exceeds the safe size limit.")
                if ext == ".pdf":
                    pdf_data = inner.read(info)
                    if len(pdf_data) > settings.BULK_IMPORT_MAX_IMAGE_BYTES or not pdf_data.startswith(b"%PDF-"):
                        raise ValidationError("Package contains a disguised or unsafe non-image file.")
                    BulkImageImportItem.objects.create(group=group, source_index=item_index, decision="skipped", safe_issue_code="unsupported_non_image")
                    bulk_import.skipped_count += 1
                    continue
                if ext not in IMAGE_EXTENSIONS:
                    raise ValidationError("Package contains an unsupported file type.")
                image_total += 1
                if image_total > settings.BULK_IMPORT_MAX_IMAGES:
                    raise ValidationError("Archive contains too many images.")
                data = inner.read(info)
                fmt, width, height = _validate_image(data, ext)
                digest = hashlib.sha256(data).hexdigest()
                if conflicting_roots:
                    BulkImageImportItem.objects.create(
                        group=group, source_index=item_index, detected_format=fmt,
                        width=width, height=height, decision="invalid",
                        safe_issue_code="conflicting_patient_roots",
                    )
                    continue
                if digest in seen_checksums or BulkImageAttachment.objects.filter(
                    organization=bulk_import.organization, checksum_sha256=digest
                ).exists():
                    BulkImageImportItem.objects.create(
                        group=group, source_index=item_index, decision="invalid",
                        safe_issue_code="duplicate_image",
                    )
                    continue
                seen_checksums.add(digest)
                item = BulkImageImportItem.objects.create(
                    group=group, source_index=item_index, checksum_sha256=digest,
                    detected_format=fmt, width=width, height=height,
                )
                key = f"bulk-staging/{bulk_import.import_id}/{item.item_id}{ext}"
                saved = storage.save(key, ContentFile(data))
                staged.append(saved)
                item.staged_object_key = saved
                item.save(update_fields=["staged_object_key"])
        with transaction.atomic():
            bulk_import.status = "preview"
            bulk_import.image_count = image_total
            bulk_import.save(update_fields=["status", "image_count", "skipped_count", "updated_at"])
            _safe_audit(user, "bulk_import_created", bulk_import, groups=len(inner_infos), images=image_total, skipped=bulk_import.skipped_count)
        return bulk_import
    except (zipfile.BadZipFile, OSError) as exc:
        for key in staged:
            try:
                storage.delete(key)
            except Exception:
                bulk_import.cleanup_pending = True
        with transaction.atomic():
            bulk_import.status = "failed"
            bulk_import.safe_error_code = "archive_validation_failed"
            bulk_import.save(update_fields=["status", "safe_error_code", "cleanup_pending", "updated_at"])
            _safe_audit(user, "bulk_import_failed", bulk_import, cleanup_pending=bulk_import.cleanup_pending)
        raise ValidationError("Archive is corrupt or unsupported.") from exc
    except Exception:
        for key in staged:
            try:
                storage.delete(key)
            except Exception:
                bulk_import.cleanup_pending = True
        with transaction.atomic():
            bulk_import.status = "failed"
            bulk_import.safe_error_code = "archive_validation_failed"
            bulk_import.save(update_fields=["status", "safe_error_code", "cleanup_pending", "updated_at"])
            _safe_audit(user, "bulk_import_failed", bulk_import, cleanup_pending=bulk_import.cleanup_pending)
        raise


def cleanup_import(bulk_import):
    storage = get_bulk_staging_storage()
    failed = False
    for key in bulk_import.groups.values_list("items__staged_object_key", flat=True):
        if key:
            try:
                storage.delete(key)
            except Exception:
                failed = True
    with transaction.atomic():
        bulk_import.cleanup_pending = failed
        bulk_import.save(update_fields=["cleanup_pending", "updated_at"])
        if failed:
            _safe_audit(
                bulk_import.confirmed_by or bulk_import.created_by,
                "bulk_import_cleanup_failed",
                bulk_import,
                retry_required=True,
            )
    return not failed


def cleanup_uncommitted_private_assets(bulk_import):
    """Remove only prepared objects that never became immutable attachments."""
    storage = get_private_clinical_storage()
    failed = False
    items = BulkImageImportItem.objects.filter(group__bulk_import=bulk_import).exclude(permanent_object_key="")
    for item in items:
        if hasattr(item, "attachment"):
            continue
        try:
            storage.delete(item.permanent_object_key)
            item.permanent_object_key = ""
            item.permanent_copy_status = "none"
            item.permanent_cleanup_pending = False
            item.save(update_fields=["permanent_object_key", "permanent_copy_status", "permanent_cleanup_pending"])
        except Exception:
            failed = True
            item.permanent_cleanup_pending = True
            item.save(update_fields=["permanent_cleanup_pending"])
    return not failed


def _confirmation_plan(bulk_import):
    groups = list(bulk_import.groups.select_for_update().prefetch_related("items"))
    encounter_ids = []
    for group in groups:
        included = [x for x in group.items.all() if x.decision not in {"rejected", "skipped", "invalid"}]
        if not included:
            continue
        encounter = group.resolved_encounter or group.proposed_encounter
        if not encounter:
            raise ValidationError("Every included group requires an encounter.")
        encounter_ids.append(encounter.id)
    encounters = {x.id: x for x in ScreeningEncounter.objects.select_for_update().select_related("patient").filter(id__in=encounter_ids)}
    planned = []
    for group in groups:
        decisions = [x for x in group.items.all() if x.decision not in {"rejected", "skipped", "invalid"}]
        if not decisions:
            continue
        encounter = encounters[(group.resolved_encounter or group.proposed_encounter).id]
        if encounter.service_session_id != bulk_import.service_session_id or encounter.service_branch_id != bulk_import.branch_id:
            raise ValidationError("Encounter is outside this import scope.")
        if any(x.decision not in {"left", "right"} for x in decisions):
            raise ValidationError("Every included image requires an explicit eye decision.")
        eyes = [x.decision for x in decisions]
        if len(eyes) != len(set(eyes)):
            raise ValidationError("Only one image per eye may be confirmed.")
        if ImageUpload.objects.filter(encounter=encounter, eye_laterality__in=eyes).exists():
            raise ValidationError("An encounter already has an image for a selected eye.")
        planned.extend((encounter, item) for item in decisions)
    return planned


def confirm_bulk_import(*, user, import_id):
    token = uuid.uuid4()
    with transaction.atomic():
        bulk_import = BulkImageImport.objects.select_for_update().select_related("service_session", "branch", "organization").get(import_id=import_id)
        assert_import_scope(user, bulk_import, write=True)
        if bulk_import.status == "confirmed":
            return bulk_import
        if bulk_import.status == "confirming":
            raise ValidationError("This import confirmation is already in progress.")
        if bulk_import.status != "preview" or bulk_import.expires_at <= timezone.now():
            raise ValidationError("This import cannot be confirmed.")
        planned = _confirmation_plan(bulk_import)
        bulk_import.status = "confirming"
        bulk_import.confirmation_token = token
        bulk_import.confirmation_started_at = timezone.now()
        bulk_import.save(update_fields=["status", "confirmation_token", "confirmation_started_at", "updated_at"])

    staging = get_bulk_staging_storage()
    try:
        for encounter, item in planned:
            extension = ".jpg" if item.detected_format == "JPEG" else ".png"
            target = item.permanent_object_key or f"clinical-assets/{bulk_import.organization_id}/{item.item_id}{extension}"
            if item.permanent_copy_status != "copied" or not get_private_clinical_storage().exists(target):
                BulkImageImportItem.objects.filter(pk=item.pk).update(
                    permanent_object_key=target, permanent_copy_status="copying"
                )
                with staging.open(item.staged_object_key, "rb") as source:
                    save_private_copy(key=target, source=source)
                BulkImageImportItem.objects.filter(pk=item.pk).update(permanent_copy_status="copied")
            item.permanent_object_key = target
            item.permanent_copy_status = "copied"
    except Exception as exc:
        BulkImageImport.objects.filter(pk=bulk_import.pk, confirmation_token=token).update(
            status="preview", confirmation_token=None, confirmation_started_at=None,
            safe_error_code="permanent_copy_failed",
        )
        raise ValidationError("Private clinical image storage is temporarily unavailable.") from exc

    try:
        with transaction.atomic():
            bulk_import = BulkImageImport.objects.select_for_update().select_related("service_session", "branch", "organization").get(import_id=import_id)
            assert_import_scope(user, bulk_import, write=True)
            if bulk_import.status == "confirmed":
                return bulk_import
            else:
                if bulk_import.status != "confirming" or bulk_import.confirmation_token != token or bulk_import.expires_at <= timezone.now():
                    raise ValidationError("This import cannot be confirmed.")
                current_plan = _confirmation_plan(bulk_import)
                if {item.id for _, item in current_plan} != {item.id for _, item in planned}:
                    raise ValidationError("Import decisions changed during confirmation. Please retry.")
                for encounter, item in current_plan:
                    item.refresh_from_db(fields=["permanent_object_key", "permanent_copy_status"])
                    if item.permanent_copy_status != "copied" or not item.permanent_object_key:
                        raise ValidationError("A selected clinical image is not safely stored.")
                    upload = ImageUpload(
                        image_upload_id=f"IMG-{uuid.uuid4().hex[:12].upper()}", encounter=encounter,
                        patient=encounter.patient, eye_laterality=item.decision, image_type="fundus",
                        storage_kind="private_clinical", private_object_key=item.permanent_object_key,
                        content_sha256=item.checksum_sha256, source_format=item.detected_format,
                        pixel_width=item.width, pixel_height=item.height, import_source="remidio_bulk",
                        asset_organization=bulk_import.organization, asset_branch=bulk_import.branch,
                        assessment_date=item.group.assessment_date, confirmed_by=user,
                        confirmed_at=timezone.now(), dataset_eligibility="excluded",
                    )
                    upload.image_file.name = ""
                    upload.save()
                    BulkImageAttachment.objects.create(
                        item=item, image_upload=upload, encounter=encounter, organization=bulk_import.organization,
                        eye_laterality=item.decision,
                        checksum_sha256=item.checksum_sha256,
                    )
                bulk_import.status = "confirmed"
                bulk_import.confirmed_by = user
                bulk_import.confirmed_at = timezone.now()
                bulk_import.confirmation_token = None
                bulk_import.confirmation_started_at = None
                bulk_import.cleanup_pending = True
                bulk_import.save(update_fields=["status", "confirmed_by", "confirmed_at", "confirmation_token", "confirmation_started_at", "cleanup_pending", "updated_at"])
                _safe_audit(user, "bulk_import_confirmed", bulk_import, attachments=len(current_plan))
                transaction.on_commit(lambda: cleanup_import(bulk_import), robust=True)
        return bulk_import
    except Exception:
        BulkImageImport.objects.filter(pk=bulk_import.pk, confirmation_token=token).update(
            status="preview", confirmation_token=None, confirmation_started_at=None,
            safe_error_code="confirmation_database_failed",
        )
        raise
