from io import BytesIO
import os
import hashlib

from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter
from rest_framework import generics, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.services import record_patient_event
from common.tenant import get_user_organization
from organizations.models import OrganizationProfile
from uploads.models import ImageUpload
from .models import (
    EyeHealthScreeningReport, HistoricalReportDocument, ReportClinicalResponsibility,
    StructuredReport, ReportStatusEvent,
)
from .clinical_integrity import (
    CLINICAL_FIELDS,
    accept_responsibility,
    assert_expected,
    bind_issued_pdf,
    create_version_if_changed,
    delete_bound_pdf,
    event_once,
    expected_version,
    latest_version,
    require_responsible_clinician,
    sync_responsibility_to_verified_profile,
)
from .clinical_wording import apply_generated_wording
from .recall_services import apply_recall_schedule
from .permissions import (
    CanManageReports,
    CanReviewOpsReports,
    CanSubmitReportToOps,
    CanUploadHistoricalReports,
)
from .serializers import (
    EyeHealthScreeningReportSerializer, HistoricalReportDocumentSerializer, StructuredReportSerializer,
)
from .eye_health import (
    build_complete_pdf, finalize_screening_report, generate_suggested_wording,
    normalise_structured_findings, professional_snapshot, require_eye_health_authority,
    screening_snapshot,
)
from .referral_linking import (
    build_report_pdf_url,
    sync_report_to_local_hospital_referral,
)
from .release_control import is_report_released_to_hospital
from .distribution import audit_clean_pdf_access, structured_clean_pdf_ready, targeted_clean_pdf_ready
from .permissions import has_internal_ops_authority



class HistoricalReportAccessMixin:
    def _can_read_encounter(self, user, encounter):
        if user.is_superuser or has_internal_ops_authority(user):
            return True
        org = get_user_organization(user)
        return bool(
            org and (
                encounter.originating_organization_id == org.id
                or encounter.patient.assigned_clinic_id == org.id
            )
        )

    def _encounter(self, request, encounter_id):
        from encounters.models import ScreeningEncounter
        encounter = get_object_or_404(
            ScreeningEncounter.objects.select_related(
                "patient__assigned_clinic", "originating_organization",
                "hospital_referral__source_hospital",
            ),
            pk=encounter_id,
        )
        if not self._can_read_encounter(request.user, encounter):
            raise PermissionDenied("You do not have access to this encounter.")
        return encounter


class EncounterHistoricalReportListCreateView(HistoricalReportAccessMixin, APIView):
    permission_classes = [IsAuthenticated, CanUploadHistoricalReports]

    def get(self, request, encounter_id):
        encounter = self._encounter(request, encounter_id)
        items = HistoricalReportDocument.objects.select_related(
            "encounter__historical_finance__collecting_organization",
            "patient", "hospital_referral__source_hospital", "uploaded_by",
        ).filter(encounter=encounter)
        return Response(HistoricalReportDocumentSerializer(items, many=True, context={"request": request}).data)

    @transaction.atomic
    def post(self, request, encounter_id):
        encounter = self._encounter(request, encounter_id)
        document = request.FILES.get("document")
        if document is None:
            return Response({"detail": "A PDF report is required."}, status=400)
        if not str(document.name or "").lower().endswith(".pdf"):
            return Response({"detail": "Historical reports must be uploaded as PDF files."}, status=400)
        if getattr(document, "size", 0) > 20 * 1024 * 1024:
            return Response({"detail": "Historical report PDF must not exceed 20 MB."}, status=400)
        header = document.read(5)
        document.seek(0)
        if header != b"%PDF-":
            return Response({"detail": "The uploaded file is not a valid PDF document."}, status=400)

        report_date = request.data.get("report_date")
        if not report_date:
            return Response({"detail": "Historical report date is required."}, status=400)

        requested_hospital_visibility = str(request.data.get("hospital_visible", "")).lower() in {"1", "true", "yes", "on"}
        referral = encounter.hospital_referral
        if requested_hospital_visibility and not referral:
            return Response({"detail": "Hospital visibility is only available for a hospital-referred encounter."}, status=400)

        item = HistoricalReportDocument(
            encounter=encounter, patient=encounter.patient, hospital_referral=referral,
            title=(request.data.get("title") or "Historical uploaded report").strip(),
            report_date=report_date,
            source_organization_name=(request.data.get("source_organization_name") or "").strip(),
            source_note=(request.data.get("source_note") or "").strip(),
            document=document, original_filename=str(document.name or "")[:255],
            hospital_visible=bool(referral and requested_hospital_visibility),
            uploaded_by=request.user,
        )
        try:
            item.full_clean()
            item.save()
            record_patient_event(
                patient=item.patient,
                event_key=f"historical-report:{item.pk}:uploaded",
                category="report", event_type="historical_report_uploaded",
                title="Historical uploaded report",
                description=f"Historical report dated {item.report_date} was uploaded for {item.encounter.encounter_id}.",
                source_type="historical_report", source_id=item.pk,
                encounter_id=item.encounter.encounter_id, report_id=item.historical_report_id,
                actor=request.user, organization=item.patient.assigned_clinic, visibility="clinic_ops",
                metadata={"original_filename": item.original_filename, "source_organization_name": item.source_organization_name},
                occurred_at=item.created_at,
            )

            create_finance = str(request.data.get("create_finance_record", "")).lower() in {"1", "true", "yes", "on"}
            if create_finance and not hasattr(encounter, "historical_finance"):
                from finance.models import FinanceServiceCode
                from finance.services import record_historical_assessment_finance
                from organizations.models import Organization

                collector = None
                collector_id = request.data.get("collecting_organization")
                if collector_id not in (None, ""):
                    collector = Organization.objects.get(pk=collector_id)
                service_code = request.data.get("service_code") or getattr(encounter, "service_package", "")
                if service_code not in FinanceServiceCode.values:
                    from finance.models import canonical_finance_service_code
                    service_code = canonical_finance_service_code(encounter)
                record_historical_assessment_finance(
                    encounter=encounter, service_code=service_code, assessment_date=report_date,
                    payment_state=request.data.get("payment_state", "historical_unknown"),
                    amount=request.data.get("amount", "0"), amount_paid=request.data.get("amount_paid", "0"),
                    collecting_organization=collector, payment_method=request.data.get("payment_method", ""),
                    payment_reference=request.data.get("payment_reference", ""),
                    source_note=(request.data.get("finance_source_note") or request.data.get("source_note") or "Historical report upload").strip(),
                    idempotency_key=request.data.get("finance_idempotency_key") or f"historical-report:{item.historical_report_id}:finance",
                    actor=request.user,
                )
        except DjangoValidationError as exc:
            transaction.set_rollback(True)
            detail = getattr(exc, "message_dict", None) or getattr(exc, "messages", None) or str(exc)
            return Response({"detail": detail}, status=400)
        except Exception as exc:
            from django.core.exceptions import ObjectDoesNotExist
            if isinstance(exc, ObjectDoesNotExist):
                transaction.set_rollback(True)
                return Response({"detail": "Collecting organisation not found."}, status=404)
            raise

        item = HistoricalReportDocument.objects.select_related(
            "encounter__historical_finance__collecting_organization", "patient",
            "hospital_referral__source_hospital", "uploaded_by",
        ).get(pk=item.pk)
        return Response(HistoricalReportDocumentSerializer(item, context={"request": request}).data, status=201)


class PatientHistoricalReportListView(HistoricalReportAccessMixin, APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, patient_id):
        from patients.models import Patient
        patient = get_object_or_404(Patient.objects.select_related("assigned_clinic"), pk=patient_id)
        if not (request.user.is_superuser or has_internal_ops_authority(request.user)):
            org = get_user_organization(request.user)
            if not org or patient.assigned_clinic_id != org.id:
                raise PermissionDenied("You do not have access to this patient.")
        items = HistoricalReportDocument.objects.select_related(
            "encounter__historical_finance__collecting_organization", "patient",
            "hospital_referral__source_hospital", "uploaded_by",
        ).filter(patient=patient)
        return Response(HistoricalReportDocumentSerializer(items, many=True, context={"request": request}).data)


class HistoricalReportContentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        item = get_object_or_404(
            HistoricalReportDocument.objects.select_related(
                "encounter__patient__assigned_clinic", "encounter__originating_organization",
                "hospital_referral__source_hospital",
            ), pk=pk,
        )
        allowed = request.user.is_superuser or has_internal_ops_authority(request.user)
        org = get_user_organization(request.user)
        if org and (
            item.encounter.originating_organization_id == org.id
            or item.encounter.patient.assigned_clinic_id == org.id
        ):
            allowed = True
        if (
            org and org.organization_type == "hospital" and item.hospital_visible
            and item.hospital_referral_id and item.hospital_referral.source_hospital_id == org.id
        ):
            allowed = True
        if not allowed:
            raise PermissionDenied("You do not have access to this historical report.")
        response = FileResponse(item.document.open("rb"), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{item.original_filename or item.historical_report_id + ".pdf"}"'
        return response


class StructuredReportRulesMixin:
    @staticmethod
    def _clinical_metadata(data):
        return {
            "clinician_name": data.get("clinician_name"),
            "professional_role": data.get("professional_role"),
            "registration_number": data.get("registration_number"),
            "reason": data.get("takeover_reason", ""),
        }

    @staticmethod
    def _serializer_data(data):
        cleaned = data.copy()
        for key in (
            "expected_version", "clinician_name", "professional_role",
            "registration_number", "takeover_reason", "correction_note",
            "resubmission_note", "idempotency_key", "submitted_version",
        ):
            cleaned.pop(key, None)
        return cleaned

    def _has_any_uploaded_image(self, encounter) -> bool:
        return ImageUpload.objects.filter(encounter=encounter).exists()

    def _validate_report_prerequisites(self, serializer, patient, encounter):
        if not patient or not encounter:
            raise PermissionDenied("Both patient and encounter are required.")

        if encounter.patient_id != patient.id:
            raise PermissionDenied(
                "The selected encounter does not belong to the selected patient."
            )

        consent_status = (patient.consent_status or "").strip().lower()
        if consent_status != "completed":
            raise PermissionDenied(
                "Cannot create or update report until patient consent is completed."
            )

        has_uploaded_image = self._has_any_uploaded_image(encounter)

        report_marked_ungradable = bool(
            serializer.validated_data.get(
                "ungradable", getattr(serializer.instance, "ungradable", False)
            )
        )

        urgency_outcome = (
            serializer.validated_data.get(
                "urgency_outcome", getattr(serializer.instance, "urgency_outcome", "")
            ) or ""
        ).strip().lower()

        report_marked_retake = urgency_outcome == "image_retake"

        allowed_without_image = report_marked_ungradable or report_marked_retake

        if not has_uploaded_image and not allowed_without_image:
            raise PermissionDenied(
                "Cannot create or update report until an image is uploaded. If no usable image is available, mark the report as Ungradable or Image Retake."
            )


    def _apply_encounter_va_defaults(self, serializer, encounter):
        """
        Technician VA is captured on the encounter. The report should inherit it
        by default, while still allowing the optometrist to override the report
        values if clinically appropriate.
        """
        if not encounter:
            return

        data = serializer.validated_data

        if not data.get("left_unaided_va"):
            data["left_unaided_va"] = getattr(encounter, "left_unaided_va", "") or getattr(encounter, "visual_acuity_left", "")

        if not data.get("right_unaided_va"):
            data["right_unaided_va"] = getattr(encounter, "right_unaided_va", "") or getattr(encounter, "visual_acuity_right", "")

        if not data.get("left_corrected_va"):
            data["left_corrected_va"] = getattr(encounter, "left_corrected_pinhole_va", "")

        if not data.get("right_corrected_va"):
            data["right_corrected_va"] = getattr(encounter, "right_corrected_pinhole_va", "")

    def _validate_report_editable(self, report, user):
        editable_statuses = {
            "draft",
            "under_review",
            "returned_to_clinic",
            "ops_rejected",
        }
        if report.report_status not in editable_statuses:
            raise PermissionDenied(
                f"This report cannot be edited while its status is {report.report_status}. "
                "Reports submitted to Ops or already issued are read-only."
            )

    def _validate_report_can_be_clinic_issued(self, report):
        missing_items = []
        if not report.patient_id: missing_items.append("patient")
        if not report.encounter_id: missing_items.append("assessment")
        if not report.review_date: missing_items.append("review date")
        if (report.patient.consent_status or "").strip().lower() != "completed": missing_items.append("patient consent")
        has_uploaded_image = self._has_any_uploaded_image(report.encounter)
        allowed_without_image = bool(report.ungradable) or ((report.urgency_outcome or "").strip().lower() == "image_retake")
        if not has_uploaded_image and not allowed_without_image:
            missing_items.append("uploaded image or valid ungradable/image retake outcome")
        if missing_items:
            raise PermissionDenied("Report cannot be signed and issued yet. Missing/incomplete: " + ", ".join(missing_items) + ".")

    def _validate_report_can_be_submitted_to_ops(self, report):
        missing_items = []

        if not report.patient_id:
            missing_items.append("patient")
        if not report.encounter_id:
            missing_items.append("encounter")
        if not report.review_date:
            missing_items.append("review_date")

        consent_status = (report.patient.consent_status or "").strip().lower()
        if consent_status != "completed":
            missing_items.append("patient consent")

        has_uploaded_image = self._has_any_uploaded_image(report.encounter)
        allowed_without_image = bool(report.ungradable) or (
            (report.urgency_outcome or "").strip().lower() == "image_retake"
        )

        if not has_uploaded_image and not allowed_without_image:
            missing_items.append("uploaded image or valid ungradable/image retake outcome")

        if missing_items:
            raise PermissionDenied(
                f"Report cannot be submitted to Ops yet. Missing/incomplete: {', '.join(missing_items)}."
            )


class StructuredReportListCreateView(
    StructuredReportRulesMixin, generics.ListCreateAPIView
):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).all()

        report_status = self.request.query_params.get("report_status")
        if report_status:
            queryset = queryset.filter(report_status=report_status)

        user = self.request.user
        if user.is_superuser:
            return queryset

        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" in user_groups:
            return queryset

        org = get_user_organization(user)
        if not org:
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=self._serializer_data(request.data))
        serializer.is_valid(raise_exception=True)
        user = self.request.user
        patient = serializer.validated_data.get("patient")
        encounter = serializer.validated_data.get("encounter")

        if not encounter.includes_diabetic_screening:
            raise PermissionDenied(
                "General ocular assessments use the ocular clinical record, "
                "not the diabetic grading report."
            )

        self._validate_report_prerequisites(serializer, patient, encounter)
        self._apply_encounter_va_defaults(serializer, encounter)

        existing = StructuredReport.objects.filter(encounter=encounter).first()
        if existing:
            return Response(
                {
                    "detail": "A structured report already exists for this encounter. Edit the existing report.",
                    "existing_report": {"id": existing.pk, "report_id": existing.report_id},
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            with transaction.atomic():
                report = serializer.save(
                    report_owner=("clinic" if encounter.workflow_route == "clinic_managed" else "sentinel")
                )
                responsibility, authority, _ = accept_responsibility(
                    user=user, report=report, **self._clinical_metadata(request.data)
                )
                apply_generated_wording(report)
                apply_recall_schedule(report)
                report.save(update_fields=[
                    "generated_clinical_summary", "final_clinical_summary",
                    "recall_due_date", "recall_status", "updated_at",
                ])
                version, _ = create_version_if_changed(
                    report=report, editor=user, responsibility=responsibility, purpose="initial"
                )
                report.lock_version = 1
                report.save(update_fields=["lock_version", "updated_at"])
                event_once(
                    report=report, event_type="created", actor=user,
                    from_status="", to_status=report.report_status,
                    target_version=version, authority_used=authority,
                    note="Structured report created by clinic.",
                    idempotency_key=f"created:{report.pk}",
                )
        except IntegrityError:
            existing = StructuredReport.objects.filter(encounter=encounter).first()
            if existing:
                return Response(
                    {
                        "detail": "A structured report already exists for this encounter. Edit the existing report.",
                        "existing_report": {"id": existing.pk, "report_id": existing.report_id},
                    }, status=status.HTTP_409_CONFLICT,
                )
            raise
        output = self.get_serializer(report)
        return Response(output.data, status=status.HTTP_201_CREATED)


class StructuredReportDetailView(
    StructuredReportRulesMixin, generics.RetrieveUpdateAPIView
):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" in user_groups:
            return queryset

        org = get_user_organization(user)
        if not org:
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        with transaction.atomic():
            report = self.get_queryset().select_for_update(of=("self",)).get(pk=kwargs["pk"])
            assert_expected(report, expected_version(request.data))
            self._validate_report_editable(report, request.user)
            serializer = self.get_serializer(
                report, data=self._serializer_data(request.data), partial=partial
            )
            serializer.is_valid(raise_exception=True)
            self._validate_report_prerequisites(serializer, report.patient, report.encounter)
            self._apply_encounter_va_defaults(serializer, report.encounter)
            responsibility = ReportClinicalResponsibility.objects.filter(report=report).first()
            responsibility_changed = False
            if responsibility and responsibility.current_clinician_id == request.user.pk:
                responsibility, authority = require_responsible_clinician(request.user, report)
            else:
                had_responsibility = responsibility is not None
                responsibility, authority, responsibility_changed = accept_responsibility(
                    user=request.user, report=report, **self._clinical_metadata(request.data)
                )
                if responsibility_changed:
                    event_once(
                        report=report,
                        event_type="responsibility_taken_over" if had_responsibility else "responsibility_accepted",
                        actor=request.user,
                        from_status=report.report_status, to_status=report.report_status,
                        source_version=latest_version(report), authority_used=authority,
                        note=("Clinical responsibility taken over; reason recorded."
                              if had_responsibility else "Clinical responsibility explicitly accepted."),
                        correction_note=responsibility.takeover_reason,
                        idempotency_key=f"takeover:{report.lock_version}:{request.user.pk}",
                    )
            meaningful_change = any(
                field in serializer.validated_data
                and getattr(report, field) != serializer.validated_data[field]
                for field in CLINICAL_FIELDS
            )
            if not meaningful_change:
                if responsibility_changed:
                    report.lock_version += 1
                    report.save(update_fields=["lock_version", "updated_at"])
                return Response(self.get_serializer(report).data)
            report = serializer.save()
            apply_generated_wording(report)
            apply_recall_schedule(report)
            report.save(update_fields=[
                "generated_clinical_summary", "final_clinical_summary",
                "recall_due_date", "recall_status", "updated_at",
            ])
            purpose = "returned_correction" if report.report_status in {"returned_to_clinic", "ops_rejected"} else "clinical_edit"
            version, created = create_version_if_changed(
                report=report, editor=request.user, responsibility=responsibility,
                purpose=purpose, correction_note=request.data.get("correction_note", ""),
            )
            if created:
                report.lock_version += 1
                report.save(update_fields=["lock_version", "updated_at"])
        return Response(self.get_serializer(report).data)


class ClinicReportListView(APIView):
    permission_classes = [CanManageReports]

    def get(self, request):
        structured = StructuredReport.objects.select_related(
            "patient", "patient__assigned_clinic", "encounter",
            "submitted_to_ops_by", "ops_reviewed_by",
        ).prefetch_related("status_events").all()
        historical = HistoricalReportDocument.objects.select_related(
            "patient", "patient__assigned_clinic", "encounter",
            "hospital_referral__source_hospital", "uploaded_by",
        ).all()

        user = request.user
        if not user.is_superuser:
            user_groups = set(user.groups.values_list("name", flat=True))
            if "ops_admin" not in user_groups:
                org = get_user_organization(user)
                if not org:
                    return Response([])
                structured = structured.filter(patient__assigned_clinic=org)
                historical = historical.filter(patient__assigned_clinic=org)

        status_filter = (request.query_params.get("status") or "").strip()
        if status_filter and status_filter != "all":
            if status_filter == "historical":
                structured = structured.none()
            else:
                structured = structured.filter(report_status=status_filter)
                historical = historical.none()

        search = (request.query_params.get("search") or "").strip()
        if search:
            from django.db import models as db_models
            sq = (db_models.Q(report_id__icontains=search) | db_models.Q(patient__patient_id__icontains=search) |
                  db_models.Q(patient__first_name__icontains=search) | db_models.Q(patient__last_name__icontains=search) |
                  db_models.Q(encounter__encounter_id__icontains=search))
            hq = (db_models.Q(historical_report_id__icontains=search) | db_models.Q(patient__patient_id__icontains=search) |
                  db_models.Q(patient__first_name__icontains=search) | db_models.Q(patient__last_name__icontains=search) |
                  db_models.Q(encounter__encounter_id__icontains=search) | db_models.Q(original_filename__icontains=search))
            structured = structured.filter(sq)
            historical = historical.filter(hq)

        data = list(StructuredReportSerializer(structured, many=True, context={"request": request}).data)
        for item in HistoricalReportDocumentSerializer(historical, many=True, context={"request": request}).data:
            item = dict(item)
            item.update({
                "report_id": item.get("historical_report_id"),
                "report_status": "historical",
                "return_reason": "",
                "ops_review_note": "",
                "sentinel_patient_id": None,
                "patient_id": item.get("patient_reference"),
            })
            data.append(item)
        data.sort(key=lambda row: str(row.get("updated_at") or row.get("report_date") or ""), reverse=True)
        return Response(data)


class EncounterReportListView(generics.ListAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        encounter_id = self.kwargs["encounter_id"]
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).filter(encounter_id=encounter_id)

        user = self.request.user
        if user.is_superuser:
            return queryset

        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" in user_groups:
            return queryset

        org = get_user_organization(user)
        if not org:
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)


class PatientReportListView(generics.ListAPIView):
    serializer_class = StructuredReportSerializer
    permission_classes = [CanManageReports]

    def get_queryset(self):
        patient_id = self.kwargs["patient_id"]
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).filter(patient_id=patient_id)

        user = self.request.user
        if user.is_superuser:
            return queryset

        user_groups = set(user.groups.values_list("name", flat=True))
        if "ops_admin" in user_groups:
            return queryset

        org = get_user_organization(user)
        if not org:
            return StructuredReport.objects.none()

        return queryset.filter(patient__assigned_clinic=org)


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanSubmitReportToOps])
def submit_report_to_ops(request, pk):
    with transaction.atomic():
        report = StructuredReport.objects.select_for_update(of=("self",)).select_related(
            "patient", "patient__assigned_clinic", "encounter",
        ).filter(pk=pk).first()
        if not report:
            raise Http404("Report not found.")
        assert_expected(report, expected_version(request.data))
        responsibility, authority = require_responsible_clinician(request.user, report)
        signer_profile = sync_responsibility_to_verified_profile(
            responsibility, user=request.user, authority=authority
        )
        if report.encounter.workflow_route != "sentinel_managed":
            return Response({"detail": "This is a Clinic Managed assessment. Use Sign and Issue Report instead."}, status=status.HTTP_400_BAD_REQUEST)
        if report.report_status not in {"draft", "under_review", "ops_rejected", "returned_to_clinic"}:
            return Response({"detail": f"This report cannot be submitted from {report.report_status}."}, status=status.HTTP_400_BAD_REQUEST)
        previous_status = report.report_status
        is_resubmission = previous_status in {"ops_rejected", "returned_to_clinic"}
        resubmission_note = (request.data.get("resubmission_note") or "").strip()
        if is_resubmission and not resubmission_note:
            return Response({"detail": "A resubmission note is required."}, status=status.HTTP_400_BAD_REQUEST)
        StructuredReportRulesMixin()._validate_report_can_be_submitted_to_ops(report)
        version = latest_version(report)
        if not version:
            version, _ = create_version_if_changed(
                report=report, editor=request.user, responsibility=responsibility, purpose="initial"
            )
        key = (request.data.get("idempotency_key") or f"submit:{report.lock_version}").strip()[:120]
        prior = ReportStatusEvent.objects.filter(report=report, idempotency_key=key).first()
        if prior:
            return Response(StructuredReportSerializer(report, context={"request": request}).data)
        signed_at = timezone.now()
        report.signed_by = request.user
        report.signed_at = signed_at
        report.signer_name = signer_profile["signature_name"]
        report.signer_role = signer_profile["professional_role"]
        report.signer_registration_number = signer_profile["registration_number"]
        report.report_status = "submitted_to_ops"
        report.submitted_to_ops_at = signed_at
        report.submitted_to_ops_by = request.user
        report.submitted_version = version
        if is_resubmission:
            report.resubmission_count += 1
        report.lock_version += 1
        report.save(update_fields=[
            "signed_by", "signed_at", "signer_name", "signer_role",
            "signer_registration_number", "report_status", "submitted_to_ops_at",
            "submitted_to_ops_by", "submitted_version", "resubmission_count",
            "lock_version", "updated_at",
        ])
        event_once(
            report=report, event_type="clinician_signed", actor=request.user,
            from_status=previous_status, to_status="submitted_to_ops",
            source_version=version, target_version=version, authority_used=authority,
            note=f"Report electronically signed by {responsibility.clinician_name} before Ops review.",
            idempotency_key=f"clinician-sign:{version.pk}:{report.lock_version}",
        )
        event_once(
            report=report, event_type="resubmitted" if is_resubmission else "submitted_to_ops",
            actor=request.user, from_status=previous_status, to_status="submitted_to_ops",
            source_version=version, target_version=version, authority_used=authority,
            note="Report resubmitted to Sentinel Ops." if is_resubmission else "Report submitted to Sentinel Ops.",
            correction_note=resubmission_note, idempotency_key=key,
        )
        local_referral = sync_report_to_local_hospital_referral(report)

    return Response(
        {
            "message": "Report submitted to Sentinel Ops successfully.",
            "report_id": report.report_id,
            "report_pk": report.pk,
            "report_status": report.report_status,
            "lock_version": report.lock_version,
            "submitted_version": report.submitted_version_id,
            "submitted_to_ops_at": report.submitted_to_ops_at,
            "local_hospital_referral_id": local_referral.referral_id if local_referral else "",
        },
        status=status.HTTP_200_OK,
    )



@api_view(["POST"])
@permission_classes([IsAuthenticated, CanSubmitReportToOps])
def clinic_issue_report(request, pk):
    created_pdf = False
    issued_version = None
    try:
        with transaction.atomic():
            report = StructuredReport.objects.select_for_update(of=("self",)).select_related(
                "patient", "patient__assigned_clinic", "encounter"
            ).filter(pk=pk).first()
            if not report:
                raise Http404("Report not found.")
            assert_expected(report, expected_version(request.data))

            clinic = report.patient.assigned_clinic

            if report.encounter.workflow_route != "clinic_managed":
                return Response(
                    {"detail": "Only Clinic Managed assessments can be issued directly by the clinic."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            profile, _ = OrganizationProfile.objects.get_or_create(organization=clinic)
            if not profile.can_issue_reports_directly:
                raise PermissionDenied("This clinic is not permitted to issue reports directly.")

            responsibility, authority = require_responsible_clinician(request.user, report)
            signer_profile = sync_responsibility_to_verified_profile(
                responsibility,
                user=request.user,
                authority=authority,
            )
            if report.report_status not in {"draft", "under_review", "returned_to_clinic", "ops_rejected"}:
                return Response({"detail": f"Only an editable report can be issued. Current status: {report.report_status}"}, status=status.HTTP_400_BAD_REQUEST)
            signer_name = signer_profile["signature_name"]
            signer_role = signer_profile["professional_role"]
            signer_registration_number = signer_profile["registration_number"]
            StructuredReportRulesMixin()._validate_report_can_be_clinic_issued(report)
            issued_version = latest_version(report)
            if not issued_version:
                issued_version, _ = create_version_if_changed(
                    report=report, editor=request.user, responsibility=responsibility, purpose="initial"
                )
            previous_status = report.report_status
            now = timezone.now()
            report.report_owner = "clinic"
            report.signed_by = request.user
            report.signed_at = now
            report.signer_name = signer_name
            report.signer_role = signer_role
            report.signer_registration_number = signer_registration_number
            report.issued_by = request.user
            report.issued_at = now
            report.sentinel_archive_received_at = now
            report.report_status = "issued"
            report.distribution_status = "awaiting_distribution"
            report.issued_version = issued_version
            report.lock_version += 1
            apply_generated_wording(report)
            apply_recall_schedule(report)
            created_pdf = bind_issued_pdf(report, issued_version, request)
            report.save(update_fields=[
                "report_owner", "signed_by", "signed_at", "signer_name", "signer_role",
                "signer_registration_number", "issued_by", "issued_at",
                "sentinel_archive_received_at", "report_status", "distribution_status",
                "issued_version", "lock_version", "generated_clinical_summary",
                "final_clinical_summary", "recall_due_date", "recall_status", "updated_at",
            ])

            referral = getattr(report.encounter, "hospital_referral", None)
            if referral:
                referral.report = report
                referral.report_ready = False
                referral.referral_status = "report_issued"
                referral.save(update_fields=[
                    "report", "report_ready", "referral_status", "updated_at",
                ])
            else:
                from finance.services import recognize_service_partner_earning
                financial_record = getattr(report.encounter, "financial_record", None)
                if financial_record:
                    recognize_service_partner_earning(financial_record, trigger_source="clinic_report_issue")
            event_once(report=report, event_type="clinic_signed", actor=request.user,
                       from_status=previous_status, to_status="issued", source_version=issued_version,
                       target_version=issued_version, authority_used=authority,
                       note=f"Report electronically signed by {signer_name}.",
                       idempotency_key=f"clinic-issue:{report.lock_version}")
            event_once(report=report, event_type="clinic_issued", actor=request.user,
                       from_status=previous_status, to_status="issued", source_version=issued_version,
                       target_version=issued_version, authority_used=authority,
                       note="Clinic Managed report issued directly by the clinic. Sentinel retained a read-only audit copy.")
            event_once(report=report, event_type="queued_for_distribution", actor=request.user,
                       from_status="issued", to_status="issued", source_version=issued_version,
                       target_version=issued_version, authority_used=authority,
                       note="Issued report queued for Sentinel distribution.")
    except Exception:
        if created_pdf:
            delete_bound_pdf(issued_version)
        raise
    return Response({"message": "Report signed and issued successfully.", "report": StructuredReportSerializer(report, context={"request": request}).data, "report_status": report.report_status, "issued_at": report.issued_at, "report_pdf_url": build_report_pdf_url(request, report)}, status=status.HTTP_200_OK)

@api_view(["POST"])
@permission_classes([IsAuthenticated, CanReviewOpsReports])
def approve_report_by_ops(request, pk):
    # Compatibility wrapper. The canonical transition lives under /api/ops/.
    from ops.views import OpsReportApproveView
    return OpsReportApproveView().post(request, pk)


@api_view(["POST"])
@permission_classes([IsAuthenticated, CanReviewOpsReports])
def reject_report_by_ops(request, pk):
    # Compatibility wrapper. The canonical transition lives under /api/ops/.
    from ops.views import OpsReportRejectView
    return OpsReportRejectView().post(request, pk)


class StructuredReportPDFView(APIView):
    permission_classes = [IsAuthenticated, CanManageReports]

    def get(self, request, pk):
        from .pdf_renderer import ReportPDFRenderer, normalise_report_format

        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "encounter__patient",
        ).prefetch_related(
            "hospital_referrals",
            "status_events",
            "encounter__image_uploads",
            "encounter__ocular_investigations",
        )

        try:
            report = queryset.get(pk=pk)
        except StructuredReport.DoesNotExist:
            raise Http404("Report not found.")

        user = request.user
        if not user.is_superuser:
            user_groups = set(user.groups.values_list("name", flat=True))
            if "ops_admin" not in user_groups:
                org = get_user_organization(user)
                if not org:
                    raise PermissionDenied("You cannot access this report.")

                hospital_referral = report.hospital_referrals.filter(
                    source_hospital=org,
                ).first()
                is_hospital_user = getattr(org, "organization_type", "") == "hospital"
                is_hospital_match = (
                    is_hospital_user
                    and is_report_released_to_hospital(report, hospital_referral)
                )
                is_clinic_match = (
                    not is_hospital_user
                    and report.patient.assigned_clinic_id == org.id
                )

                if not is_clinic_match and not is_hospital_match:
                    raise PermissionDenied("You cannot access this report.")

                if is_hospital_match:
                    now = timezone.now()
                    if not report.hospital_viewed_at:
                        report.hospital_viewed_at = now
                    report.hospital_downloaded_at = now
                    report.save(
                        update_fields=[
                            "hospital_viewed_at",
                            "hospital_downloaded_at",
                            "updated_at",
                        ]
                    )
                    ReportStatusEvent.objects.create(
                        report=report,
                        event_type="hospital_downloaded",
                        from_status=report.report_status,
                        to_status=report.report_status,
                        note="Hospital opened/downloaded the issued report PDF.",
                        actor=request.user,
                    )

        report_format = normalise_report_format(
            request.query_params.get("report_format")
        )

        # Hospitals may open released hospital, clinician, and
        # patient-friendly presentations only. Ops/Audit remains internal.
        org = get_user_organization(user) if not user.is_superuser else None
        if org and getattr(org, "organization_type", "") == "hospital":
            if report_format not in {
                "hospital",
                "clinician",
                "patient",
            }:
                report_format = "hospital"

        issued_version = report.issued_version
        hospital_referral = None
        if org and getattr(org, "organization_type", "") == "hospital":
            hospital_referral = report.hospital_referrals.filter(source_hospital=org).first()
        clean_ready = structured_clean_pdf_ready(
            report, hospital_referral=hospital_referral
        )
        if clean_ready and report.report_status == "issued" and issued_version and issued_version.pdf_object_key:
            from uploads.storage import get_private_clinical_storage
            with get_private_clinical_storage().open(issued_version.pdf_object_key, "rb") as source:
                pdf_bytes = source.read()
            audit_clean_pdf_access(
                actor=request.user, report_kind="diabetic_report", report_id=report.pk,
                version_id=issued_version.pk, audience=report_format,
                context="hospital" if hospital_referral else "internal",
            )
            response = HttpResponse(pdf_bytes, content_type="application/pdf")
            response["Content-Disposition"] = f'inline; filename="{report.report_id}-issued.pdf"'
            return response

        pdf_bytes = ReportPDFRenderer(
            report=report,
            request=request,
            report_format=report_format,
            force_draft=not clean_ready,
        ).build()

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'inline; filename="{report.report_id}-{report_format}.pdf"'
        )
        return response


class EyeHealthScreeningReportView(APIView):
    permission_classes = [IsAuthenticated]

    def _encounter(self, request, encounter_id):
        from encounters.models import ScreeningEncounter
        encounter = get_object_or_404(
            ScreeningEncounter.objects.select_related(
                "patient__assigned_clinic", "patient__assigned_branch", "service_branch"
            ), pk=encounter_id,
        )
        if not encounter.includes_eye_health_screening:
            raise PermissionDenied("This encounter does not include targeted retinal and glaucoma-risk screening.")
        require_eye_health_authority(request.user, encounter)
        return encounter

    def get(self, request, encounter_id):
        encounter = self._encounter(request, encounter_id)
        report = EyeHealthScreeningReport.objects.filter(encounter=encounter).first()
        if not report:
            return Response({"detail": "No targeted screening report draft exists."}, status=404)
        return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)

    @transaction.atomic
    def post(self, request, encounter_id):
        encounter = self._encounter(request, encounter_id)
        encounter = encounter.__class__.objects.select_for_update().get(pk=encounter.pk)
        report = EyeHealthScreeningReport.objects.select_for_update().filter(encounter=encounter).first()
        _created = report is None
        if report is None:
            report = EyeHealthScreeningReport.objects.create(encounter=encounter)
        if report.status == report.Status.FINALIZED:
            return Response({"detail": "The finalized screening report is immutable."}, status=409)
        supplied_version = request.data.get("expected_version")
        if not _created:
            try:
                supplied_version = int(supplied_version)
            except (TypeError, ValueError):
                return Response({"detail": "The current report version is required."}, status=400)
            if supplied_version != report.lock_version:
                return Response(
                    {"detail": "This screening report changed after it was loaded."},
                    status=status.HTTP_409_CONFLICT,
                )
        payload = request.data.copy()
        payload.pop("expected_version", None)
        regenerate = payload.pop("regenerate_suggested_wording", False) is True
        serializer = EyeHealthScreeningReportSerializer(
            report, data=payload, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        extra = {"preview_checksum": "", "previewed_at": None, "lock_version": report.lock_version + 1}
        if regenerate:
            findings = normalise_structured_findings(
                serializer.validated_data.get("structured_findings", report.structured_findings)
            )
            suggestion = generate_suggested_wording(findings)
            extra.update(generated_suggestion=suggestion, clinical_summary=suggestion)
        serializer.save(**extra)
        return Response(serializer.data, status=201 if _created else 200)

    patch = post


class EyeHealthScreeningPreviewView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        report = get_object_or_404(
            EyeHealthScreeningReport.objects.select_for_update(of=("self",)).select_related(
                "encounter__patient__assigned_clinic", "encounter__patient__assigned_branch",
                "encounter__service_branch",
            ), pk=pk,
        )
        try:
            authority, clinic, branch = require_eye_health_authority(request.user, report.encounter)
            clinician = professional_snapshot(request.user, authority)
            clinician.update({
                "clinic_id": clinic.pk, "clinic_name": clinic.name,
                "branch_id": branch.pk, "branch_name": branch.name,
            })
            snapshot, checksum, _manifest = screening_snapshot(report, clinician)
            audience = str(request.data.get("report_format") or request.query_params.get("report_format") or "patient").lower()
            pdf, _manifest = build_complete_pdf(report, snapshot, audience=audience, draft=True)
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=400)
        report.preview_checksum = checksum
        report.previewed_at = timezone.now()
        report.save(update_fields=["preview_checksum", "previewed_at", "updated_at"])
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{report.encounter.encounter_id}-targeted-retinal-glaucoma-risk-preview.pdf"'
        response["Cache-Control"] = "private, no-store, max-age=0"
        return response


class EyeHealthScreeningFinalizeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        report = get_object_or_404(EyeHealthScreeningReport, pk=pk)
        try:
            version = finalize_screening_report(
                report, user=request.user,
                expected_version=int(request.data.get("expected_version")),
                signoff_confirmed=request.data.get("signoff_confirmed") is True,
            )
        except (DjangoValidationError, TypeError, ValueError) as exc:
            detail = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": detail}, status=400)
        report.refresh_from_db()
        return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)


class EyeHealthScreeningCorrectionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        report = get_object_or_404(
            EyeHealthScreeningReport.objects.select_for_update(of=("self",)).select_related(
                "encounter__patient__assigned_clinic", "encounter__patient__assigned_branch",
                "encounter__service_branch", "finalized_version",
            ), pk=pk,
        )
        require_eye_health_authority(request.user, report.encounter)
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            return Response({"detail": "A correction reason is required."}, status=400)
        if (
            report.status == report.Status.DRAFT
            and report.correction_source_version_id
            and report.correction_reason == reason
        ):
            return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)
        if report.status != report.Status.FINALIZED or not report.finalized_version:
            return Response({"detail": "Only a finalized report can enter correction."}, status=409)
        report.status = report.Status.DRAFT
        report.correction_reason = reason
        report.correction_source_version = report.finalized_version
        report.preview_checksum = ""
        report.previewed_at = None
        if getattr(report.encounter, "hospital_referral_id", None):
            report.review_status = report.ReviewStatus.RETURNED_TO_CLINIC
        else:
            report.review_status = report.ReviewStatus.DRAFT
        report.ops_reviewed_at = None
        report.ops_reviewed_by = None
        report.ops_review_note = ""
        report.issued_at = None
        report.issued_by = None
        report.lock_version += 1
        report.save(update_fields=[
            "status", "correction_reason", "correction_source_version",
            "preview_checksum", "previewed_at", "review_status",
            "ops_reviewed_at", "ops_reviewed_by", "ops_review_note",
            "issued_at", "issued_by", "lock_version", "updated_at",
        ])
        report.encounter.update_status_from_related_records()
        return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)


class EyeHealthScreeningOpsApproveView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        if not has_internal_ops_authority(request.user):
            raise PermissionDenied("Exact Sentinel Ops authority is required.")
        report = get_object_or_404(
            EyeHealthScreeningReport.objects.select_for_update(of=("self",)).select_related(
                "encounter__hospital_referral__source_hospital", "finalized_version", "signed_by",
            ),
            pk=pk,
        )
        if not report.encounter.hospital_referral_id:
            return Response({"detail": "Clinic-direct reports do not require Ops review."}, status=400)
        if report.status != report.Status.FINALIZED or not report.finalized_version_id:
            return Response({"detail": "Only a clinician-finalized report can be approved."}, status=409)
        if report.review_status == report.ReviewStatus.APPROVED:
            return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)
        if report.review_status not in {report.ReviewStatus.AWAITING_OPS, report.ReviewStatus.LEGACY}:
            return Response({"detail": f"This report is not awaiting Ops review. Current review status: {report.review_status}."}, status=409)
        if not (report.signed_by_id and report.signed_at and report.finalized_version.clinician_snapshot):
            if report.review_status != report.ReviewStatus.LEGACY:
                return Response({"detail": "The finalized report does not contain a complete clinician sign-off."}, status=409)
        now = timezone.now()
        report.review_status = report.ReviewStatus.APPROVED
        report.ops_reviewed_at = now
        report.ops_reviewed_by = request.user
        report.ops_review_note = str(request.data.get("note") or "").strip()
        report.issued_at = now
        report.issued_by = request.user
        report.lock_version += 1
        report.save(update_fields=[
            "review_status", "ops_reviewed_at", "ops_reviewed_by", "ops_review_note",
            "issued_at", "issued_by", "lock_version", "updated_at",
        ])
        from ops.models import OpsAuditLog
        OpsAuditLog.objects.create(
            actor=request.user, action="report_approved",
            entity_type="targeted_report", entity_id=str(report.pk),
            entity_label=f"targeted_report:{report.pk}",
            message="Clinician-signed targeted report approved by Sentinel Ops.",
            metadata={
                "report_version_id": report.finalized_version_id,
                "clinical_signer_user_id": report.signed_by_id,
                "review_note": report.ops_review_note,
            },
        )
        return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)


class EyeHealthScreeningOpsReturnView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        if not has_internal_ops_authority(request.user):
            raise PermissionDenied("Exact Sentinel Ops authority is required.")
        reason = str(request.data.get("reason") or request.data.get("note") or "").strip()
        if not reason:
            return Response({"detail": "A return reason is required."}, status=400)
        report = get_object_or_404(
            EyeHealthScreeningReport.objects.select_for_update(of=("self",)).select_related(
                "encounter__hospital_referral__source_hospital", "finalized_version",
            ),
            pk=pk,
        )
        if not report.encounter.hospital_referral_id:
            return Response({"detail": "Clinic-direct reports do not require Ops review."}, status=400)
        if report.status != report.Status.FINALIZED or not report.finalized_version_id:
            return Response({"detail": "Only a clinician-finalized report can be returned."}, status=409)
        if report.review_status not in {report.ReviewStatus.AWAITING_OPS, report.ReviewStatus.LEGACY}:
            return Response({"detail": f"This report is not awaiting Ops review. Current review status: {report.review_status}."}, status=409)
        report.status = report.Status.DRAFT
        report.review_status = report.ReviewStatus.RETURNED_TO_CLINIC
        report.correction_reason = reason
        report.correction_source_version = report.finalized_version
        report.preview_checksum = ""
        report.previewed_at = None
        report.ops_reviewed_at = timezone.now()
        report.ops_reviewed_by = request.user
        report.ops_review_note = reason
        report.issued_at = None
        report.issued_by = None
        report.lock_version += 1
        report.save(update_fields=[
            "status", "review_status", "correction_reason", "correction_source_version",
            "preview_checksum", "previewed_at", "ops_reviewed_at", "ops_reviewed_by",
            "ops_review_note", "issued_at", "issued_by", "lock_version", "updated_at",
        ])
        report.encounter.update_status_from_related_records()
        from ops.models import OpsAuditLog
        OpsAuditLog.objects.create(
            actor=request.user, action="report_returned",
            entity_type="targeted_report", entity_id=str(report.pk),
            entity_label=f"targeted_report:{report.pk}",
            message="Targeted report returned to clinic for correction.",
            metadata={"report_version_id": report.finalized_version_id, "reason": reason},
        )
        return Response(EyeHealthScreeningReportSerializer(report, context={"request": request}).data)


class EyeHealthScreeningPDFView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        from uploads.access import can_access_clinical_asset
        from uploads.storage import get_private_clinical_storage
        report = get_object_or_404(
            EyeHealthScreeningReport.objects.select_related(
                "encounter__patient__assigned_clinic", "encounter__patient__assigned_branch",
                "encounter__service_branch", "finalized_version",
            ), pk=pk,
        )
        encounter = report.encounter
        organization = encounter.patient.assigned_clinic
        branch = encounter.service_branch or encounter.patient.assigned_branch
        audience = str(request.query_params.get("report_format") or "patient").lower()
        if audience not in {"patient", "clinician"}:
            return Response({"detail": "Report format must be patient or clinician."}, status=400)
        user_org = get_user_organization(request.user)
        referral = getattr(encounter, "hospital_referral", None)
        is_hospital = bool(user_org and user_org.organization_type == "hospital")
        if is_hospital:
            roles = set(request.user.groups.values_list("name", flat=True))
            if not referral or referral.source_hospital_id != user_org.id or not roles.intersection({"hospital_admin", "hospital_clinician"}):
                raise PermissionDenied("You do not have access to this report.")
            version = report.hospital_released_version
            if not targeted_clean_pdf_ready(report, hospital_referral=referral):
                raise PermissionDenied("This exact report version has not been released to the referring hospital.")
            draft = False
        else:
            if not can_access_clinical_asset(
                request.user, encounter=encounter, organization=organization, branch=branch
            ):
                raise PermissionDenied("You do not have access to this report.")
            version = report.finalized_version
            draft = not targeted_clean_pdf_ready(report)
            if draft and not has_internal_ops_authority(request.user):
                require_eye_health_authority(request.user, encounter)
        if not version:
            return Response({"detail": "No finalized screening report is available."}, status=404)
        content, _manifest = build_complete_pdf(
            report, version.clinical_snapshot, audience=audience, draft=draft,
            manifest=version.attachment_manifest,
        )
        if not draft:
            audit_clean_pdf_access(
                actor=request.user, report_kind="targeted_report", report_id=report.pk,
                version_id=version.pk, audience=audience,
                context="hospital" if is_hospital else "internal",
            )
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{encounter.encounter_id}-targeted-{audience}-report.pdf"'
        response["Cache-Control"] = "private, no-store, max-age=0"
        return response


class EyeHealthScreeningReleaseView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        from finance.services import capture_finance_for_hospital_publication
        from referrals.models import HospitalReferral
        if not has_internal_ops_authority(request.user):
            raise PermissionDenied("Exact Sentinel Ops authority is required.")
        report = get_object_or_404(
            EyeHealthScreeningReport.objects.select_for_update(of=("self",)).select_related(
                "encounter__hospital_referral__source_hospital", "finalized_version",
            ), pk=pk,
        )
        if report.status != report.Status.FINALIZED or not report.finalized_version:
            return Response({"detail": "Only a finalized targeted report can be released."}, status=409)
        referral = getattr(report.encounter, "hospital_referral", None)
        if not referral or not referral.source_hospital_id:
            return Response({"detail": "This encounter has no original referring hospital."}, status=400)
        referral = HospitalReferral.objects.select_for_update().get(pk=referral.pk)
        if report.hospital_released_version_id == report.finalized_version_id and report.hospital_released_at:
            return Response({"detail": "This exact targeted report version is already released.", "version": report.finalized_version_id})
        if report.review_status != report.ReviewStatus.APPROVED:
            return Response(
                {"detail": "This clinician-signed targeted report requires Sentinel Ops approval before hospital release."},
                status=409,
            )
        try:
            capture_finance_for_hospital_publication(report.encounter, actor=request.user)
        except DjangoValidationError as exc:
            detail = (getattr(exc, "messages", None) or [str(exc)])[0]
            return Response({"detail": detail, "code": "PAYMENT_REQUIRED"}, status=402)
        report.hospital_released_version = report.finalized_version
        report.hospital_released_at = timezone.now()
        report.hospital_released_by = request.user
        report.lock_version += 1
        report.save(update_fields=[
            "hospital_released_version", "hospital_released_at", "hospital_released_by",
            "lock_version", "updated_at",
        ])
        from ops.models import OpsAuditLog
        OpsAuditLog.objects.create(
            actor=request.user, action="targeted_report_released",
            entity_type="targeted_report", entity_id=str(report.pk),
            entity_label=f"targeted_report:{report.pk}",
            message="Targeted report released to original referring hospital.",
            metadata={
                "hospital_id": referral.source_hospital_id,
                "referral_id": referral.pk,
                "report_version_id": report.hospital_released_version_id,
            },
        )
        return Response({
            "detail": "Targeted report released to the original referring hospital.",
            "version": report.hospital_released_version_id,
            "hospital": referral.source_hospital.name,
        })


class CombinedScreeningBundleView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, encounter_id):
        from encounters.models import ScreeningEncounter
        from uploads.access import can_access_clinical_asset
        from uploads.storage import get_private_clinical_storage
        encounter = get_object_or_404(
            ScreeningEncounter.objects.select_related(
                "patient__assigned_clinic", "patient__assigned_branch", "service_branch",
            ), pk=encounter_id, service_package=ScreeningEncounter.ServicePackage.COMBINED,
        )
        organization = encounter.patient.assigned_clinic
        branch = encounter.service_branch or encounter.patient.assigned_branch
        audience = str(request.query_params.get("report_format") or "patient").lower()
        if audience not in {"patient", "clinician"}:
            return Response({"detail": "Report format must be patient or clinician."}, status=400)
        diabetic = getattr(encounter, "structured_report", None)
        screening = getattr(encounter, "eye_health_report", None)
        if (
            not diabetic or diabetic.report_status != "issued" or not diabetic.issued_version
            or not diabetic.issued_version.pdf_object_key
            or not screening or not screening.finalized_version
            or not screening.finalized_version.pdf_object_key
        ):
            return Response({"detail": "Both finalized report components are required."}, status=409)
        user_org = get_user_organization(request.user)
        referral = getattr(encounter, "hospital_referral", None)
        is_hospital = bool(user_org and user_org.organization_type == "hospital")
        if is_hospital:
            if not referral or referral.source_hospital_id != user_org.id:
                raise PermissionDenied("You do not have access to this report bundle.")
            diabetic_referral = diabetic.hospital_referrals.filter(source_hospital=user_org).first()
            if not structured_clean_pdf_ready(diabetic, hospital_referral=diabetic_referral) or not targeted_clean_pdf_ready(screening, hospital_referral=referral):
                raise PermissionDenied("The exact combined report versions have not been released.")
            screening_version = screening.hospital_released_version
        else:
            if not can_access_clinical_asset(
                request.user, encounter=encounter, organization=organization, branch=branch
            ):
                raise PermissionDenied("You do not have access to this report bundle.")
            if not structured_clean_pdf_ready(diabetic) or not targeted_clean_pdf_ready(screening):
                return Response({"detail": "Clean report bundle is unavailable until completion and capture."}, status=402)
            screening_version = screening.finalized_version
        storage = get_private_clinical_storage()
        writer = PdfWriter()
        with storage.open(diabetic.issued_version.pdf_object_key, "rb") as source:
            diabetic_content = source.read()
        targeted_content, _manifest = build_complete_pdf(
            screening, screening_version.clinical_snapshot, audience=audience, draft=False,
            manifest=screening_version.attachment_manifest,
        )
        for content in (diabetic_content, targeted_content):
            for page in PdfReader(BytesIO(content), strict=True).pages:
                writer.add_page(page)
        audit_clean_pdf_access(
            actor=request.user, report_kind="combined_report_bundle", report_id=encounter.pk,
            version_id=screening_version.pk, audience=audience,
            context="hospital" if is_hospital else "internal",
        )
        output = BytesIO()
        writer.write(output)
        response = HttpResponse(output.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{encounter.encounter_id}-combined-{audience}-bundle.pdf"'
        response["Cache-Control"] = "private, no-store, max-age=0"
        return response
