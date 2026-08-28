import csv
import hashlib
import os
import secrets
import uuid
from datetime import timedelta

from PIL import Image, UnidentifiedImageError
from django.db import IntegrityError, transaction
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.urls import reverse
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from .dataset_pipeline import has_ai_training_consent_granted
from .models import ImageUpload, DatasetLabel, MobileTransferSession, PendingMobileImage
from .serializers import ImageUploadSerializer
from .permissions import CanManageUploads
from .ai_services import run_ai_analysis
from .clinical_assets import save_private_copy
from .private_uploads import delete_private_object, save_private_upload
from .storage import get_bulk_staging_storage, get_private_clinical_storage


MOBILE_TRANSFER_MAX_BYTES = 15 * 1024 * 1024


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _delete_pending_object(item):
    failed = False
    try:
        if item.staged_object_key:
            get_bulk_staging_storage().delete(item.staged_object_key)
        elif item.image_file:
            item.image_file.delete(save=False)
    except Exception:
        failed = True
    try:
        if item.permanent_object_key:
            delete_private_object(item.permanent_object_key)
        elif item.staged_object_key:
            key = _mobile_permanent_key(item)
            storage = get_private_clinical_storage()
            if storage.exists(key):
                storage.delete(key)
    except Exception:
        failed = True
    if failed:
        raise OSError("Pending mobile image cleanup is incomplete.")


def _mobile_permanent_key(item):
    extension = os.path.splitext(item.original_filename or "")[1].lower()
    if extension == ".jpeg":
        extension = ".jpg"
    return (
        f"clinical-assets/images/"
        f"{uuid.uuid5(uuid.NAMESPACE_URL, f'mobile:{item.session.session_id}:{item.id}').hex}"
        f"{extension if extension in {'.jpg', '.png'} else '.jpg'}"
    )


def _cleanup_mobile_session(session):
    for item in session.pending_images.exclude(status="confirmed"):
        try:
            _delete_pending_object(item)
        except Exception:
            continue
        item.delete()


def _get_open_mobile_session(token):
    try:
        session = MobileTransferSession.objects.select_related(
            "encounter", "encounter__patient", "encounter__patient__assigned_clinic"
        ).get(token_hash=_token_hash(token))
    except MobileTransferSession.DoesNotExist:
        raise ValidationError("This transfer link is invalid.")
    if session.status != "open" or session.expires_at <= timezone.now():
        if session.status == "open":
            session.status = "expired"
            session.save(update_fields=["status"])
            _cleanup_mobile_session(session)
        raise ValidationError("This transfer link has expired or is no longer open.")
    return session


def _assert_session_access(user, session):
    if user.is_superuser:
        return

    roles = set(user.groups.values_list("name", flat=True))
    if roles.intersection({"ops_admin", "sentinel_ops", "super_admin"}):
        return

    org = get_user_organization(user)
    if (
        not org
        or not org.is_active
        or org.organization_type != "clinic"
        or session.encounter.patient.assigned_clinic_id != org.id
    ):
        raise PermissionDenied("You cannot access this encounter transfer.")


def _pending_payload(item, request, token=None):
    if token:
        image_path = reverse("mobile-transfer-public-image", args=[token, item.id])
    else:
        image_path = reverse(
            "mobile-transfer-review-image",
            args=[item.session.session_id, item.id],
        )
    return {
        "id": item.id,
        "original_filename": item.original_filename,
        "image_url": request.build_absolute_uri(image_path),
        "status": item.status,
        "uploaded_at": item.uploaded_at,
        "confirmed_upload_id": item.confirmed_upload_id,
    }


class MobileTransferCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, encounter_id):
        from encounters.models import ScreeningEncounter
        try:
            encounter = ScreeningEncounter.objects.select_related(
                "patient", "patient__assigned_clinic"
            ).get(pk=encounter_id)
        except ScreeningEncounter.DoesNotExist:
            return Response({"detail": "Encounter not found."}, status=status.HTTP_404_NOT_FOUND)
        _assert_session_access(request.user, MobileTransferSession(encounter=encounter))
        for previous in MobileTransferSession.objects.filter(encounter=encounter, status="open"):
            previous.status = "cancelled"
            previous.save(update_fields=["status"])
            _cleanup_mobile_session(previous)
        token = secrets.token_urlsafe(32)
        session = MobileTransferSession.objects.create(
            encounter=encounter,
            initiated_by=request.user,
            token_hash=_token_hash(token),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        patient = encounter.patient
        return Response({
            "session_id": str(session.session_id),
            "token": token,
            "expires_at": session.expires_at,
            "encounter_id": encounter.encounter_id,
            "patient_display": f"{patient.first_name} {patient.last_name}".strip(),
            "patient_date_of_birth": patient.date_of_birth,
        }, status=status.HTTP_201_CREATED)


class MobileTransferPublicView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request, token):
        session = _get_open_mobile_session(token)
        patient = session.encounter.patient
        return Response({
            "session_id": str(session.session_id),
            "encounter_id": session.encounter.encounter_id,
            "patient_display": f"{patient.first_name} {patient.last_name}".strip(),
            "patient_date_of_birth": patient.date_of_birth,
            "expires_at": session.expires_at,
            "uploaded_count": session.pending_images.count(),
        })

    def post(self, request, token):
        session = _get_open_mobile_session(token)
        files = request.FILES.getlist("images")
        if not files:
            raise ValidationError({"images": "Select at least one JPG or PNG image."})
        if len(files) > 10:
            raise ValidationError({"images": "A maximum of 10 images can be transferred at once."})
        created = []
        for uploaded in files:
            extension = os.path.splitext(uploaded.name)[1].lower()
            if extension not in {".jpg", ".jpeg", ".png"}:
                raise ValidationError({"images": f"{uploaded.name}: only JPG and PNG files are accepted."})
            if uploaded.size > MOBILE_TRANSFER_MAX_BYTES:
                raise ValidationError({"images": f"{uploaded.name}: file is larger than 15 MB."})
            try:
                Image.open(uploaded).verify()
            except (UnidentifiedImageError, OSError):
                raise ValidationError({"images": f"{uploaded.name}: file is not a valid image."})
            uploaded.seek(0)
            checksum = hashlib.sha256()
            for chunk in uploaded.chunks():
                checksum.update(chunk)
            uploaded.seek(0)
            try:
                extension = extension if extension != ".jpeg" else ".jpg"
                key = f"bulk-staging/mobile/{uuid.uuid4().hex}{extension}"
                saved = get_bulk_staging_storage().save(key, uploaded)
                if saved != key:
                    get_bulk_staging_storage().delete(saved)
                    raise OSError("Private pending-image storage key collision.")
                try:
                    item = PendingMobileImage.objects.create(
                        session=session,
                        image_file="",
                        staged_object_key=key,
                        original_filename=os.path.basename(uploaded.name)[:255],
                        checksum_sha256=checksum.hexdigest(),
                    )
                except Exception:
                    get_bulk_staging_storage().delete(key)
                    raise
            except IntegrityError:
                raise ValidationError({"images": f"{uploaded.name}: this image was already transferred."})
            created.append(_pending_payload(item, request, token=token))
        return Response({"images": created}, status=status.HTTP_201_CREATED)


def _open_pending_image(item):
    if item.staged_object_key:
        return get_bulk_staging_storage().open(item.staged_object_key, "rb")
    return item.image_file.open("rb")


class MobileTransferPublicImageView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token, image_id):
        session = _get_open_mobile_session(token)
        item = get_object_or_404(session.pending_images, pk=image_id, status="pending")
        response = FileResponse(_open_pending_image(item), content_type="image/jpeg")
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class MobileTransferReviewImageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id, image_id):
        item = get_object_or_404(
            PendingMobileImage.objects.select_related(
                "session", "session__encounter", "session__encounter__patient",
                "session__encounter__patient__assigned_clinic",
            ),
            session__session_id=session_id, pk=image_id,
        )
        _assert_session_access(request.user, item.session)
        response = FileResponse(_open_pending_image(item), content_type="image/jpeg")
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["X-Content-Type-Options"] = "nosniff"
        return response


class MobileTransferReviewView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        try:
            session = MobileTransferSession.objects.select_related(
                "encounter", "encounter__patient", "encounter__patient__assigned_clinic"
            ).get(session_id=session_id)
        except MobileTransferSession.DoesNotExist:
            return Response({"detail": "Transfer session not found."}, status=status.HTTP_404_NOT_FOUND)
        _assert_session_access(request.user, session)
        return Response({
            "session_id": str(session.session_id),
            "status": session.status,
            "expires_at": session.expires_at,
            "images": [_pending_payload(item, request) for item in session.pending_images.all()],
        })


class MobileTransferImageReviewView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, session_id, image_id):
        try:
            item = PendingMobileImage.objects.select_for_update(of=("self",)).select_related(
                "session", "session__encounter", "session__encounter__patient",
                "session__encounter__patient__assigned_clinic",
            ).get(id=image_id, session__session_id=session_id)
        except PendingMobileImage.DoesNotExist:
            return Response({"detail": "Pending image not found."}, status=status.HTTP_404_NOT_FOUND)
        _assert_session_access(request.user, item.session)
        if item.status != "pending":
            if item.status == "confirmed":
                return Response(_pending_payload(item, request))
            raise ValidationError("This image has already been reviewed.")
        action = request.data.get("action")
        if action == "reject":
            item.status = "rejected"
            item.reviewed_by = request.user
            item.reviewed_at = timezone.now()
            item.save(update_fields=["status", "reviewed_by", "reviewed_at"])
            transaction.on_commit(lambda: _delete_pending_object(item), robust=True)
            return Response(_pending_payload(item, request))
        if action != "confirm":
            raise ValidationError({"action": "Choose confirm or reject."})
        laterality = request.data.get("eye_laterality")
        quality = request.data.get("image_quality", "good")
        if laterality not in {"left", "right"}:
            raise ValidationError({"eye_laterality": "Choose left or right."})
        if quality not in {"good", "acceptable", "poor", "ungradable"}:
            raise ValidationError({"image_quality": "Choose a valid image quality."})
        encounter = item.session.encounter
        if ImageUpload.objects.filter(encounter=encounter, eye_laterality=laterality).exists():
            raise ValidationError(f"A {laterality} eye image already exists. Delete it before confirming a replacement.")
        organization = encounter.originating_organization or encounter.patient.assigned_clinic
        branch = encounter.service_branch or encounter.patient.assigned_branch
        if not organization or not branch:
            raise ValidationError("The encounter requires an organization and branch before confirmation.")
        extension = os.path.splitext(item.original_filename or "")[1].lower()
        permanent_key = _mobile_permanent_key(item)
        with _open_pending_image(item) as source:
            save_private_copy(key=permanent_key, source=source)
        item.permanent_object_key = permanent_key
        item.save(update_fields=["permanent_object_key"])
        upload = ImageUpload.objects.create(
            image_upload_id=f"IMG-{uuid.uuid4().hex[:10].upper()}",
            encounter=encounter, patient=encounter.patient,
            eye_laterality=laterality, image_type="fundus", image_file="",
            storage_kind="private_clinical", private_object_key=permanent_key,
            content_sha256=item.checksum_sha256,
            source_format="PNG" if extension == ".png" else "JPEG",
            asset_organization=organization, asset_branch=branch,
            image_quality=quality, gradable=request.data.get("gradable", True),
            retake_required=request.data.get("retake_required", False),
        )
        if encounter.includes_diabetic_screening:
            try:
                run_ai_analysis(upload)
            except Exception as exc:
                print("AI analysis failed after mobile transfer confirmation:", exc)
        item.status = "confirmed"
        item.reviewed_by = request.user
        item.reviewed_at = timezone.now()
        item.confirmed_upload = upload
        staged_key = item.staged_object_key
        item.staged_object_key = ""
        item.save(update_fields=["status", "reviewed_by", "reviewed_at", "confirmed_upload", "staged_object_key"])
        if staged_key:
            transaction.on_commit(
                lambda: get_bulk_staging_storage().delete(staged_key), robust=True
            )
        if not item.session.pending_images.filter(status="pending").exists():
            item.session.status = "completed"
            item.session.completed_at = timezone.now()
            item.session.save(update_fields=["status", "completed_at"])
        return Response(_pending_payload(item, request))


class ImageUploadListCreateView(generics.ListCreateAPIView):
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = ImageUpload.objects.select_related(
            "encounter",
            "encounter__patient",
            "encounter__patient__assigned_clinic",
            "patient",
        ).prefetch_related("ai_analysis", "dataset_label").all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ImageUpload.objects.none()

        return queryset.filter(encounter__patient__assigned_clinic=org)

    def perform_create(self, serializer):
        user = self.request.user
        encounter = serializer.validated_data.get("encounter")
        eye_laterality = serializer.validated_data.get("eye_laterality")

        if not encounter:
            raise PermissionDenied("An encounter is required.")

        duplicate_exists = ImageUpload.objects.filter(
            encounter=encounter,
            eye_laterality=eye_laterality,
        ).exists()

        if duplicate_exists:
            raise ValidationError(
                f"A {eye_laterality} eye image already exists for this encounter. Delete the existing image before uploading a replacement."
            )

        if not user.is_superuser:
            org = get_user_organization(user)
            if not org:
                raise PermissionDenied("You are not linked to a clinic organization.")

            if encounter.patient.assigned_clinic_id != org.id:
                raise PermissionDenied("You cannot upload images for another clinic's encounter.")

        organization = encounter.originating_organization or encounter.patient.assigned_clinic
        branch = encounter.service_branch or encounter.patient.assigned_branch
        if not organization or not branch:
            raise ValidationError("The encounter requires an organization and branch before upload.")
        if not (
            user.branch_access.filter(branch=branch).exists()
            or user.branch_access.filter(
                branch__organization=organization, has_all_branch_access=True,
            ).exists()
        ):
            raise PermissionDenied("You do not have access to this encounter branch.")
        uploaded = serializer.validated_data.pop("image_file")
        private = save_private_upload(uploaded, category="images")
        try:
            image_upload = serializer.save(
                patient=encounter.patient, image_file="",
                storage_kind="private_clinical",
                private_object_key=private["key"],
                content_sha256=private["sha256"],
                source_format=private["source_format"],
                pixel_width=private["width"], pixel_height=private["height"],
                asset_organization=organization, asset_branch=branch,
                confirmed_by=user, confirmed_at=timezone.now(),
            )
        except Exception:
            delete_private_object(private["key"])
            raise

        # General ocular images must not enter the diabetic AI/dataset
        # workflow. Combined encounters retain the diabetic component.
        if encounter.includes_diabetic_screening:
            try:
                run_ai_analysis(image_upload)
            except Exception as exc:
                print("AI analysis failed after upload:", exc)

        try:
            if hasattr(encounter, "update_status_from_related_records"):
                encounter.update_status_from_related_records()
        except Exception as exc:
            print("Upload encounter status update failed:", exc)

        return image_upload

    def create(self, request, *args, **kwargs):
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            image_upload = self.perform_create(serializer)
            response_serializer = self.get_serializer(image_upload)
            return Response(response_serializer.data, status=status.HTTP_201_CREATED)
        except PermissionDenied:
            raise
        except ValidationError:
            raise
        except Exception as exc:
            print("UPLOAD ERROR:", repr(exc))
            return Response(
                {"detail": f"Upload failed: {repr(exc)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class ImageUploadDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = ImageUpload.objects.select_related(
            "encounter",
            "encounter__patient",
            "encounter__patient__assigned_clinic",
            "patient",
        ).prefetch_related("ai_analysis", "dataset_label").all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ImageUpload.objects.none()

        return queryset.filter(encounter__patient__assigned_clinic=org)

    def perform_update(self, serializer):
        user = self.request.user
        encounter = serializer.validated_data.get("encounter", serializer.instance.encounter)

        if not user.is_superuser:
            org = get_user_organization(user)
            if not org:
                raise PermissionDenied("You are not linked to a clinic organization.")
            if encounter.patient.assigned_clinic_id != org.id:
                raise PermissionDenied("You cannot update uploads for another clinic's encounter.")

        replacement = serializer.validated_data.pop("image_file", None)
        if replacement:
            organization = encounter.originating_organization or encounter.patient.assigned_clinic
            branch = encounter.service_branch or encounter.patient.assigned_branch
            if not organization or not branch:
                raise ValidationError("The encounter requires an organization and branch before upload.")
            if not (
                user.branch_access.filter(branch=branch).exists()
                or user.branch_access.filter(
                    branch__organization=organization, has_all_branch_access=True,
                ).exists()
            ):
                raise PermissionDenied("You do not have access to this encounter branch.")
            private = save_private_upload(replacement, category="images")
            old_key = serializer.instance.private_object_key if serializer.instance.storage_kind == "private_clinical" else ""
            try:
                upload = serializer.save(
                    patient=encounter.patient, image_file="",
                    storage_kind="private_clinical", private_object_key=private["key"],
                    content_sha256=private["sha256"], source_format=private["source_format"],
                    pixel_width=private["width"], pixel_height=private["height"],
                    asset_organization=organization, asset_branch=branch,
                    confirmed_by=user, confirmed_at=timezone.now(),
                )
            except Exception:
                delete_private_object(private["key"])
                raise
            if old_key:
                delete_private_object(old_key)
        else:
            upload = serializer.save(patient=encounter.patient)

        latest_report = encounter.reports.order_by("-created_at").first()
        if latest_report:
            from .dataset_pipeline import sync_dataset_from_report
            sync_dataset_from_report(latest_report)

        if hasattr(encounter, "update_status_from_related_records"):
            encounter.update_status_from_related_records()

        return upload

    def perform_destroy(self, instance):
        key = instance.private_object_key if instance.storage_kind == "private_clinical" else ""
        instance.delete()
        if key:
            delete_private_object(key)


class EncounterImageUploadListView(generics.ListAPIView):
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]

    def get_queryset(self):
        encounter_id = self.kwargs["encounter_id"]
        queryset = ImageUpload.objects.select_related(
            "encounter",
            "encounter__patient",
            "encounter__patient__assigned_clinic",
            "patient",
        ).prefetch_related("ai_analysis", "dataset_label").filter(encounter_id=encounter_id)

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ImageUpload.objects.none()

        return queryset.filter(encounter__patient__assigned_clinic=org)


class PatientImageUploadListView(generics.ListAPIView):
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        queryset = ImageUpload.objects.select_related(
            "encounter",
            "encounter__patient",
            "encounter__patient__assigned_clinic",
            "patient",
        ).prefetch_related("ai_analysis", "dataset_label").filter(patient_id=patient_id)

        user = self.request.user
        if user.is_superuser:
            return queryset.order_by("-uploaded_at")

        org = get_user_organization(user)
        if not org:
            return ImageUpload.objects.none()

        return queryset.filter(patient__assigned_clinic=org).order_by("-uploaded_at")


class PatientImageComparisonView(APIView):
    permission_classes = [CanManageUploads]

    def get(self, request, patient_id):
        queryset = ImageUpload.objects.select_related(
            "encounter",
            "encounter__patient",
            "encounter__patient__assigned_clinic",
            "patient",
        ).prefetch_related("ai_analysis", "dataset_label").filter(patient_id=patient_id)

        user = request.user
        if not user.is_superuser:
            org = get_user_organization(user)
            if not org:
                return Response({"left": [], "right": []})
            queryset = queryset.filter(patient__assigned_clinic=org)

        serializer = ImageUploadSerializer(queryset.order_by("uploaded_at"), many=True, context={"request": request})
        grouped = {"left": [], "right": []}
        for item in serializer.data:
            laterality = item.get("eye_laterality")
            if laterality in grouped:
                grouped[laterality].append(item)
        return Response(grouped)


class DatasetTrainingExportView(APIView):
    permission_classes = [CanManageUploads]

    def get(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied("Only super admins can export the AI training dataset.")

        queryset = DatasetLabel.objects.select_related(
            "image_upload",
            "source_report",
            "encounter",
            "patient",
            "patient__assigned_clinic",
        ).filter(consent_confirmed=True)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="sentinel_training_dataset.csv"'

        writer = csv.writer(response)
        writer.writerow([
            "label_id", "image_upload_id", "image_url", "encounter_id", "patient_id", "clinic",
            "eye_laterality", "image_type", "image_quality_label", "unaided_visual_acuity",
            "corrected_visual_acuity", "dr_grade", "maculopathy_grade", "diabetic_referable",
            "vision_referral_needed", "vision_referral_reason", "overall_referable",
            "referral_urgency", "clinician_notes", "other_findings", "ai_provider",
            "ai_prediction", "ai_confidence", "ai_referable", "ai_clinician_agreement",
            "disagreement_flag", "quality_score", "quality_flag", "source_report_id",
            "report_status_at_label_time", "labelled_at",
        ])

        for label in queryset:
            if not has_ai_training_consent_granted(label.patient):
                continue
            clinic = label.patient.assigned_clinic
            writer.writerow([
                label.label_id,
                label.image_upload.image_upload_id,
                (
                    request.build_absolute_uri(f"/api/uploads/{label.image_upload_id}/content/")
                ),
                label.encounter.encounter_id,
                label.patient.patient_id,
                clinic.name if clinic else "",
                label.eye_laterality or label.image_upload.eye_laterality,
                label.image_upload.image_type,
                label.image_quality_label,
                label.unaided_visual_acuity,
                label.corrected_visual_acuity,
                label.dr_grade,
                label.maculopathy_grade,
                label.diabetic_referable,
                label.vision_referral_needed,
                label.vision_referral_reason,
                label.referable,
                label.referral_urgency,
                label.clinician_notes,
                label.other_findings,
                label.ai_provider_at_label_time,
                label.ai_prediction_at_label_time,
                label.ai_confidence_at_label_time,
                label.ai_referable_at_label_time,
                label.ai_clinician_agreement,
                label.disagreement_flag,
                label.quality_score,
                label.quality_flag,
                label.source_report.report_id if label.source_report else "",
                label.report_status_at_label_time,
                label.labelled_at,
            ])

        return response
