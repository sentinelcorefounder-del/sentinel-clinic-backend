from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.services import record_patient_event
from common.tenant import get_user_organization
from consents.models import ConsentRecord
from reports.models import StructuredReport
from uploads.models import ImageUpload

from .models import (
    HistoricalRecordAccessRequest,
    Patient,
)
from .serializers import (
    HistoricalRecordAccessRequestSerializer,
)


def get_clinic_org(user):
    org = get_user_organization(user)
    if not org or org.organization_type != "clinic":
        raise PermissionDenied("You are not linked to a clinic.")
    return org


class ClinicHistoricalAccessRequestListCreateView(APIView):
    def get(self, request):
        org = get_clinic_org(request.user)
        queryset = HistoricalRecordAccessRequest.objects.select_related(
            "master_patient",
            "requesting_organization",
            "requested_by",
            "reviewed_by",
        ).filter(requesting_organization=org)

        serializer = HistoricalRecordAccessRequestSerializer(
            queryset,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data)

    @transaction.atomic
    def post(self, request):
        org = get_clinic_org(request.user)

        patient_id = request.data.get("patient")
        patient = Patient.objects.select_related(
            "master_patient",
            "assigned_clinic",
        ).filter(
            pk=patient_id,
            assigned_clinic=org,
        ).first()

        if not patient:
            return Response(
                {"detail": "Patient not found in this clinic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not patient.master_patient_id:
            return Response(
                {
                    "detail": (
                        "This patient has not yet been linked to "
                        "a Sentinel master identity."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        consent_record_id = request.data.get("consent_record")
        consent_record = None
        if consent_record_id:
            consent_record = ConsentRecord.objects.filter(
                pk=consent_record_id,
                patient=patient,
                consent_type="data_sharing",
                consent_status="granted",
            ).first()

        consent_reference = (
            request.data.get("consent_reference") or ""
        ).strip()

        if not consent_record and not consent_reference:
            return Response(
                {
                    "detail": (
                        "A granted data-sharing consent record "
                        "or consent reference is required."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        purpose = (request.data.get("purpose") or "").strip()
        if not purpose:
            return Response(
                {"detail": "Clinical purpose is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        access_request = HistoricalRecordAccessRequest.objects.create(
            master_patient=patient.master_patient,
            requesting_organization=org,
            requested_by=request.user,
            purpose=purpose,
            consent_reference=(
                consent_reference
                or consent_record.consent_id
            ),
            consent_record=consent_record,
            include_reports=bool(
                request.data.get("include_reports", True)
            ),
            include_images=bool(
                request.data.get("include_images", True)
            ),
        )

        record_patient_event(
            patient=patient,
            event_key=(
                f"historical-access:{access_request.pk}:requested"
            ),
            category="consent",
            event_type="historical_record_access_requested",
            title="Historical record access requested",
            description=(
                f"{org.name} requested read-only historical "
                "record access."
            ),
            source_type="historical_access_request",
            source_id=access_request.pk,
            actor=request.user,
            organization=org,
            visibility="clinic_ops",
            metadata={
                "include_reports": access_request.include_reports,
                "include_images": access_request.include_images,
                "purpose": access_request.purpose,
            },
        )

        return Response(
            HistoricalRecordAccessRequestSerializer(
                access_request,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )


class ClinicHistoricalRecordView(APIView):
    def get(self, request, pk):
        org = get_clinic_org(request.user)

        access_request = (
            HistoricalRecordAccessRequest.objects.select_related(
                "master_patient",
                "requesting_organization",
            )
            .filter(
                pk=pk,
                requesting_organization=org,
            )
            .first()
        )

        if not access_request:
            return Response(
                {"detail": "Access request not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not access_request.is_currently_active:
            return Response(
                {
                    "detail": (
                        "Historical access is not currently active."
                    )
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        master = access_request.master_patient
        patient_records = master.patient_records.all()

        reports = []
        if access_request.include_reports:
            for report in StructuredReport.objects.select_related(
                "patient",
                "encounter",
            ).filter(
                patient__in=patient_records,
                report_status="issued",
            ).order_by("-issued_at"):
                # Do not return internal Ops format or notes.
                reports.append(
                    {
                        "id": report.id,
                        "report_id": report.report_id,
                        "patient_record_id": report.patient_id,
                        "encounter_id": (
                            report.encounter.encounter_id
                            if report.encounter
                            else ""
                        ),
                        "issued_at": report.issued_at,
                        "review_date": report.review_date,
                        "urgency_outcome": report.urgency_outcome,
                        "pdf_url": request.build_absolute_uri(
                            f"/api/reports/{report.id}/pdf/"
                            "?report_format=clinician"
                        ),
                        "patient_pdf_url": request.build_absolute_uri(
                            f"/api/reports/{report.id}/pdf/"
                            "?report_format=patient"
                        ),
                    }
                )

        images = []
        if access_request.include_images:
            for upload in ImageUpload.objects.select_related(
                "encounter",
                "patient",
            ).filter(
                patient__in=patient_records,
            ).order_by("-uploaded_at"):
                file_obj = upload.image_file
                images.append(
                    {
                        "id": upload.id,
                        "image_upload_id": upload.image_upload_id,
                        "patient_record_id": upload.patient_id,
                        "encounter_id": (
                            upload.encounter.encounter_id
                            if upload.encounter
                            else ""
                        ),
                        "eye_laterality": upload.eye_laterality,
                        "image_type": upload.image_type,
                        "image_quality": upload.image_quality,
                        "uploaded_at": upload.uploaded_at,
                        "image_file": (
                            request.build_absolute_uri(file_obj.url)
                            if file_obj
                            else ""
                        ),
                    }
                )

        return Response(
            {
                "access_request": (
                    HistoricalRecordAccessRequestSerializer(
                        access_request,
                        context={"request": request},
                    ).data
                ),
                "master_patient": {
                    "id": master.id,
                    "sentinel_patient_id": (
                        master.sentinel_patient_id
                    ),
                    "name": (
                        f"{master.first_name} "
                        f"{master.last_name}"
                    ).strip(),
                    "date_of_birth": master.date_of_birth,
                    "sex": master.sex,
                },
                "reports": reports,
                "images": images,
            }
        )
