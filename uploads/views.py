import csv

from django.http import HttpResponse
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from .dataset_pipeline import has_ai_training_consent_granted
from .models import ImageUpload, DatasetLabel
from .serializers import ImageUploadSerializer
from .permissions import CanManageUploads
from .ai_services import run_ai_analysis


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
