import csv

from django.http import HttpResponse
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from .models import ImageUpload, DatasetLabel
from .serializers import ImageUploadSerializer, DatasetLabelSerializer
from .permissions import CanManageUploads
from .ai_services import run_ai_analysis


def patient_has_completed_consent(patient):
    return getattr(patient, "consent_status", None) == "completed"


def user_can_access_image(user, image_upload):
    if user.is_superuser:
        return True

    org = get_user_organization(user)
    if not org:
        return False

    return image_upload.encounter.patient.assigned_clinic_id == org.id


class ImageUploadListCreateView(generics.ListCreateAPIView):
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        queryset = ImageUpload.objects.select_related(
            "encounter",
            "encounter__patient",
            "encounter__patient__assigned_clinic",
        ).prefetch_related(
            "ai_analysis",
            "dataset_label",
        ).all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ImageUpload.objects.none()

        return queryset.filter(encounter__patient__assigned_clinic=org)

    def perform_create(self, serializer):
        user = self.request.user

        if user.is_superuser:
            image_upload = serializer.save()
            run_ai_analysis(image_upload)
            return image_upload

        org = get_user_organization(user)
        if not org:
            raise PermissionDenied("You are not linked to a clinic organization.")

        encounter = serializer.validated_data.get("encounter")
        if not encounter:
            raise PermissionDenied("An encounter is required.")

        if encounter.patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot upload images for another clinic's encounter.")

        image_upload = serializer.save()
        run_ai_analysis(image_upload)
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
        ).prefetch_related(
            "ai_analysis",
            "dataset_label",
        ).all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ImageUpload.objects.none()

        return queryset.filter(encounter__patient__assigned_clinic=org)

    def perform_update(self, serializer):
        user = self.request.user

        if user.is_superuser:
            serializer.save()
            return

        org = get_user_organization(user)
        if not org:
            raise PermissionDenied("You are not linked to a clinic organization.")

        encounter = serializer.validated_data.get("encounter", serializer.instance.encounter)
        if encounter.patient.assigned_clinic_id != org.id:
            raise PermissionDenied("You cannot update uploads for another clinic's encounter.")

        serializer.save()


class EncounterImageUploadListView(generics.ListAPIView):
    serializer_class = ImageUploadSerializer
    permission_classes = [CanManageUploads]

    def get_queryset(self):
        encounter_id = self.kwargs["encounter_id"]
        queryset = ImageUpload.objects.select_related(
            "encounter",
            "encounter__patient",
            "encounter__patient__assigned_clinic",
        ).prefetch_related(
            "ai_analysis",
            "dataset_label",
        ).filter(encounter_id=encounter_id)

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return ImageUpload.objects.none()

        return queryset.filter(encounter__patient__assigned_clinic=org)


class DatasetLabelListCreateView(generics.ListCreateAPIView):
    serializer_class = DatasetLabelSerializer
    permission_classes = [CanManageUploads]

    def get_queryset(self):
        queryset = DatasetLabel.objects.select_related(
            "image_upload",
            "encounter",
            "patient",
            "patient__assigned_clinic",
            "labelled_by",
        ).filter(consent_confirmed=True)

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return DatasetLabel.objects.none()

        return queryset.filter(patient__assigned_clinic=org)

    def perform_create(self, serializer):
        image_upload = serializer.validated_data.get("image_upload")

        if not image_upload:
            raise ValidationError("Image upload is required.")

        if not user_can_access_image(self.request.user, image_upload):
            raise PermissionDenied("You cannot label this image.")

        patient = image_upload.patient

        if not patient_has_completed_consent(patient):
            raise ValidationError(
                "Dataset label cannot be created because patient consent is not completed."
            )

        ai = getattr(image_upload, "ai_analysis", None)

        serializer.save(
            encounter=image_upload.encounter,
            patient=patient,
            consent_confirmed=True,
            labelled_by=self.request.user,
            ai_prediction_at_label_time=getattr(ai, "prediction", "") if ai else "",
            ai_provider_at_label_time=getattr(ai, "provider", "") if ai else "",
            ai_confidence_at_label_time=getattr(ai, "confidence", None) if ai else None,
            ai_raw_response_at_label_time=getattr(ai, "raw_response_json", None) if ai else None,
        )


class DatasetLabelDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = DatasetLabelSerializer
    permission_classes = [CanManageUploads]

    def get_queryset(self):
        queryset = DatasetLabel.objects.select_related(
            "image_upload",
            "encounter",
            "patient",
            "patient__assigned_clinic",
            "labelled_by",
        ).filter(consent_confirmed=True)

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return DatasetLabel.objects.none()

        return queryset.filter(patient__assigned_clinic=org)

    def perform_update(self, serializer):
        instance = serializer.instance

        if not patient_has_completed_consent(instance.patient):
            raise ValidationError(
                "Dataset label cannot be updated because patient consent is not completed."
            )

        if not user_can_access_image(self.request.user, instance.image_upload):
            raise PermissionDenied("You cannot update this label.")

        serializer.save(
            consent_confirmed=True,
            labelled_by=self.request.user,
        )


class DatasetLabelExportView(APIView):
    permission_classes = [CanManageUploads]

    def get(self, request):
        queryset = DatasetLabel.objects.select_related(
            "image_upload",
            "encounter",
            "patient",
            "patient__assigned_clinic",
            "labelled_by",
        ).filter(consent_confirmed=True)

        if not request.user.is_superuser:
            org = get_user_organization(request.user)
            if not org:
                queryset = DatasetLabel.objects.none()
            else:
                queryset = queryset.filter(patient__assigned_clinic=org)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="sentinel_dataset_labels.csv"'

        writer = csv.writer(response)
        writer.writerow([
            "label_id",
            "image_upload_id",
            "encounter_id",
            "eye_laterality",
            "image_type",
            "image_file",
            "image_quality_label",
            "dr_grade",
            "maculopathy_grade",
            "referable",
            "referral_urgency",
            "clinician_notes",
            "other_findings",
            "ai_provider_at_label_time",
            "ai_prediction_at_label_time",
            "ai_confidence_at_label_time",
            "labelled_by",
            "labelled_at",
        ])

        for label in queryset:
            writer.writerow([
                label.label_id,
                label.image_upload.image_upload_id,
                label.encounter.encounter_id,
                label.image_upload.eye_laterality,
                label.image_upload.image_type,
                label.image_upload.image_file.url if label.image_upload.image_file else "",
                label.image_quality_label,
                label.dr_grade,
                label.maculopathy_grade,
                label.referable,
                label.referral_urgency,
                label.clinician_notes,
                label.other_findings,
                label.ai_provider_at_label_time,
                label.ai_prediction_at_label_time,
                label.ai_confidence_at_label_time,
                label.labelled_by.username if label.labelled_by else "",
                label.labelled_at,
            ])

        return response