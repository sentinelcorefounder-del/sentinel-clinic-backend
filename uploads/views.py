import csv
import hashlib
import os
import secrets
import uuid
from datetime import timedelta

from PIL import Image, UnidentifiedImageError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
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


MOBILE_TRANSFER_MAX_BYTES = 15 * 1024 * 1024


def _token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def _pending_payload(item, request):
    return {
        "id": item.id,
        "original_filename": item.original_filename,
        "image_url": request.build_absolute_uri(item.image_file.url),
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
        MobileTransferSession.objects.filter(encounter=encounter, status="open").update(status="cancelled")
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
                item = PendingMobileImage.objects.create(
                    session=session,
                    image_file=uploaded,
                    original_filename=os.path.basename(uploaded.name)[:255],
                    checksum_sha256=checksum.hexdigest(),
                )
            except IntegrityError:
                raise ValidationError({"images": f"{uploaded.name}: this image was already transferred."})
            created.append(_pending_payload(item, request))
        return Response({"images": created}, status=status.HTTP_201_CREATED)


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
            raise ValidationError("This image has already been reviewed.")
        action = request.data.get("action")
        if action == "reject":
            item.status = "rejected"
            item.reviewed_by = request.user
            item.reviewed_at = timezone.now()
            item.save(update_fields=["status", "reviewed_by", "reviewed_at"])
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
        upload = ImageUpload.objects.create(
            image_upload_id=f"IMG-{uuid.uuid4().hex[:10].upper()}",
            encounter=encounter,
            patient=encounter.patient,
            eye_laterality=laterality,
            image_type="fundus",
            image_file=item.image_file.name,
            image_quality=quality,
            gradable=request.data.get("gradable", True),
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
        item.save(update_fields=["status", "reviewed_by", "reviewed_at", "confirmed_upload"])
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

        image_upload = serializer.save(patient=encounter.patient)

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

        upload = serializer.save(patient=encounter.patient)

        latest_report = encounter.reports.order_by("-created_at").first()
        if latest_report:
            from .dataset_pipeline import sync_dataset_from_report
            sync_dataset_from_report(latest_report)

        if hasattr(encounter, "update_status_from_related_records"):
            encounter.update_status_from_related_records()

        return upload


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
                label.image_upload.image_file.url if label.image_upload.image_file else "",
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
