
from django.utils import timezone
from django.db.models import Q, Max
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from organizations.models import Organization
from patients.models import Patient
from reports.models import StructuredReport, ReportStatusEvent
from .models import HospitalReferral, generate_referral_id
from .permissions import IsHospitalUser
from .serializers import HospitalReferralSerializer
from .submit_serializers import HospitalReferralSubmitSerializer
from .sync_serializers import HospitalReferralStatusSyncSerializer


class HospitalScopedMixin:
    def get_hospital_org(self):
        user = self.request.user
        if user.is_superuser:
            return None
        return get_user_organization(user)


class HospitalReferralQuerysetMixin(HospitalScopedMixin):
    def get_queryset(self):
        queryset = HospitalReferral.objects.select_related(
            "source_hospital",
            "patient",
            "matched_clinic",
            "report",
        ).prefetch_related("ops_payments").all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = self.get_hospital_org()
        if not org:
            return HospitalReferral.objects.none()

        return queryset.filter(source_hospital=org)


class HospitalReferralListView(HospitalReferralQuerysetMixin, generics.ListAPIView):
    serializer_class = HospitalReferralSerializer
    permission_classes = [IsAuthenticated, IsHospitalUser]


class HospitalReferralDetailView(HospitalReferralQuerysetMixin, generics.RetrieveAPIView):
    serializer_class = HospitalReferralSerializer
    permission_classes = [IsAuthenticated, IsHospitalUser]

    def retrieve(self, request, *args, **kwargs):
        referral = self.get_object()
        report = referral.report

        if report and report.report_status == "issued" and not report.hospital_viewed_at:
            report.hospital_viewed_at = timezone.now()
            report.save(update_fields=["hospital_viewed_at", "updated_at"])
            ReportStatusEvent.objects.create(
                report=report,
                event_type="hospital_viewed",
                from_status="issued",
                to_status="issued",
                note="Hospital opened the referral containing the issued report.",
                actor=request.user,
            )

        serializer = self.get_serializer(referral)
        return Response(serializer.data)


class HospitalIssuedReportListView(APIView):
    permission_classes = [IsAuthenticated, IsHospitalUser]

    def get(self, request):
        org = get_user_organization(request.user)
        if not request.user.is_superuser and not org:
            return Response(
                {"detail": "You are not linked to a hospital organization."},
                status=status.HTTP_403_FORBIDDEN,
            )

        referrals = HospitalReferral.objects.select_related(
            "patient",
            "matched_clinic",
            "report",
            "source_hospital",
        ).filter(
            report__report_status="issued",
        )

        if not request.user.is_superuser:
            referrals = referrals.filter(source_hospital=org)

        search = (request.query_params.get("search") or "").strip()
        if search:
            from django.db import models as db_models
            referrals = referrals.filter(
                db_models.Q(report__report_id__icontains=search)
                | db_models.Q(referral_id__icontains=search)
                | db_models.Q(first_name__icontains=search)
                | db_models.Q(last_name__icontains=search)
                | db_models.Q(patient__patient_id__icontains=search)
            )

        data = []
        for referral in referrals.order_by("-report__issued_at", "-updated_at"):
            report = referral.report
            data.append(
                {
                    "id": report.id,
                    "report_id": report.report_id,
                    "referral_id": referral.referral_id,
                    "referral_pk": referral.id,
                    "patient_id": referral.patient.patient_id if referral.patient else referral.patient_id_text,
                    "patient_name": f"{referral.first_name} {referral.last_name}".strip(),
                    "clinic_name": referral.matched_clinic.name if referral.matched_clinic else "",
                    "issued_at": report.issued_at,
                    "review_date": report.review_date,
                    "report_status": report.report_status,
                    "hospital_viewed_at": report.hospital_viewed_at,
                    "hospital_downloaded_at": report.hospital_downloaded_at,
                    "pdf_url": request.build_absolute_uri(f"/api/reports/{report.id}/pdf/"),
                }
            )

        return Response(data)


class HospitalIssuedReportDetailView(APIView):
    permission_classes = [IsAuthenticated, IsHospitalUser]

    def get(self, request, pk):
        org = get_user_organization(request.user)

        referral_qs = HospitalReferral.objects.select_related(
            "patient",
            "matched_clinic",
            "report",
            "source_hospital",
        ).filter(
            report_id=pk,
            report__report_status="issued",
        )

        if not request.user.is_superuser:
            if not org:
                raise PermissionDenied("You are not linked to a hospital organization.")
            referral_qs = referral_qs.filter(source_hospital=org)

        referral = referral_qs.first()
        if not referral:
            return Response(
                {"detail": "Issued report not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        report = referral.report
        if not report.hospital_viewed_at:
            report.hospital_viewed_at = timezone.now()
            report.save(update_fields=["hospital_viewed_at", "updated_at"])
            ReportStatusEvent.objects.create(
                report=report,
                event_type="hospital_viewed",
                from_status="issued",
                to_status="issued",
                note="Hospital opened the issued report detail page.",
                actor=request.user,
            )

        images = []
        for upload in report.encounter.image_uploads.all().order_by("eye_laterality"):
            image_file = getattr(upload, "image_file", None)
            images.append(
                {
                    "id": upload.id,
                    "image_upload_id": upload.image_upload_id,
                    "eye_laterality": upload.eye_laterality,
                    "image_quality": upload.image_quality,
                    "url": request.build_absolute_uri(image_file.url) if image_file else "",
                }
            )

        return Response(
            {
                "id": report.id,
                "report_id": report.report_id,
                "referral_id": referral.referral_id,
                "referral_pk": referral.id,
                "patient_id": referral.patient.patient_id if referral.patient else referral.patient_id_text,
                "patient_name": f"{referral.first_name} {referral.last_name}".strip(),
                "clinic_name": referral.matched_clinic.name if referral.matched_clinic else "",
                "review_date": report.review_date,
                "issued_at": report.issued_at,
                "report_status": report.report_status,
                "left_dr_grade": report.left_dr_grade,
                "left_maculopathy_grade": report.left_maculopathy_grade,
                "right_dr_grade": report.right_dr_grade,
                "right_maculopathy_grade": report.right_maculopathy_grade,
                "urgency_outcome": report.urgency_outcome,
                "recommendation": report.recommendation,
                "next_followup_interval": report.next_followup_interval,
                "images": images,
                "pdf_url": request.build_absolute_uri(f"/api/reports/{report.id}/pdf/"),
            }
        )



class HospitalPatientListView(APIView):
    """
    Hospital-scoped patient registry.

    A hospital only sees patients linked to referrals submitted by its own
    organisation. Duplicate referrals for the same patient are collapsed into
    one patient row using the latest referral.
    """

    permission_classes = [IsAuthenticated, IsHospitalUser]

    def get(self, request):
        org = get_user_organization(request.user)

        if not request.user.is_superuser:
            if not org:
                return Response(
                    {"detail": "You are not linked to a hospital organization."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if org.organization_type != "hospital":
                return Response(
                    {"detail": "Only hospital users can access hospital patients."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        referrals = HospitalReferral.objects.select_related(
            "patient",
            "matched_clinic",
            "report",
            "source_hospital",
        ).prefetch_related("ops_payments").filter(patient__isnull=False)

        if not request.user.is_superuser:
            referrals = referrals.filter(source_hospital=org)

        search = (request.query_params.get("search") or "").strip()
        referral_status = (request.query_params.get("status") or "").strip()

        if search:
            referrals = referrals.filter(
                Q(patient__patient_id__icontains=search)
                | Q(patient__first_name__icontains=search)
                | Q(patient__last_name__icontains=search)
                | Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(hospital_mrn__icontains=search)
                | Q(referral_id__icontains=search)
            )

        if referral_status and referral_status != "all":
            if referral_status == "report_ready":
                referrals = referrals.filter(
                    report__report_status="issued",
                    report_ready=True,
                )
            else:
                referrals = referrals.filter(referral_status=referral_status)

        referrals = referrals.order_by("patient_id", "-updated_at", "-id")

        seen_patient_ids = set()
        data = []

        for referral in referrals:
            patient = referral.patient
            if not patient or patient.id in seen_patient_ids:
                continue

            seen_patient_ids.add(patient.id)
            payment = referral.ops_payments.order_by("-created_at").first()
            report = referral.report

            data.append(
                {
                    "patient_pk": patient.id,
                    "patient_id": patient.patient_id,
                    "patient_name": f"{patient.first_name} {patient.last_name}".strip(),
                    "date_of_birth": patient.date_of_birth,
                    "sex": patient.sex,
                    "phone": patient.phone,
                    "email": patient.email,
                    "hospital_mrn": referral.hospital_mrn,
                    "referral_pk": referral.id,
                    "referral_id": referral.referral_id,
                    "referral_status": referral.referral_status,
                    "clinic_name": (
                        referral.matched_clinic.name
                        if referral.matched_clinic
                        else ""
                    ),
                    "payment_status": payment.status if payment else "not_created",
                    "report_pk": report.id if report else None,
                    "report_id": report.report_id if report else "",
                    "report_status": report.report_status if report else "not_created",
                    "report_ready": bool(
                        report
                        and report.report_status == "issued"
                        and referral.report_ready
                    ),
                    "latest_activity": referral.updated_at,
                }
            )

        return Response(data)


class HospitalPatientDetailView(APIView):
    """
    Hospital-scoped patient detail.

    Only data linked to the authenticated hospital is returned. Clinical
    reports and fundus images are exposed only for reports issued by Ops.
    """

    permission_classes = [IsAuthenticated, IsHospitalUser]

    def get(self, request, pk):
        org = get_user_organization(request.user)

        referrals = HospitalReferral.objects.select_related(
            "patient",
            "matched_clinic",
            "report",
            "source_hospital",
            "report__encounter",
        ).prefetch_related(
            "ops_payments",
            "report__encounter__image_uploads",
            "report__encounter__image_uploads__ai_analysis",
        ).filter(
            patient_id=pk,
        )

        if not request.user.is_superuser:
            if not org:
                return Response(
                    {"detail": "You are not linked to a hospital organization."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            referrals = referrals.filter(source_hospital=org)

        referrals = referrals.order_by("-updated_at", "-id")
        latest_referral = referrals.first()

        if not latest_referral or not latest_referral.patient:
            return Response(
                {"detail": "Patient not found for this hospital."},
                status=status.HTTP_404_NOT_FOUND,
            )

        patient = latest_referral.patient

        referral_rows = []
        report_rows = []
        encounter_rows = []
        upload_rows = []

        seen_report_ids = set()
        seen_encounter_ids = set()
        seen_upload_ids = set()

        for referral in referrals:
            payment = referral.ops_payments.order_by("-created_at").first()
            report = referral.report

            referral_rows.append(
                {
                    "id": referral.id,
                    "referral_id": referral.referral_id,
                    "hospital_mrn": referral.hospital_mrn,
                    "clinic_name": (
                        referral.matched_clinic.name
                        if referral.matched_clinic
                        else ""
                    ),
                    "referral_status": referral.referral_status,
                    "payment_status": payment.status if payment else "not_created",
                    "report_status": report.report_status if report else "not_created",
                    "created_at": referral.created_at,
                    "updated_at": referral.updated_at,
                }
            )

            if not report or report.report_status != "issued":
                continue

            if report.id not in seen_report_ids:
                seen_report_ids.add(report.id)
                report_rows.append(
                    {
                        "id": report.id,
                        "report_id": report.report_id,
                        "review_date": report.review_date,
                        "report_status": report.report_status,
                        "issued_at": report.issued_at,
                        "left_dr_grade": report.left_dr_grade,
                        "right_dr_grade": report.right_dr_grade,
                        "left_maculopathy_grade": report.left_maculopathy_grade,
                        "right_maculopathy_grade": report.right_maculopathy_grade,
                        "urgency_outcome": report.urgency_outcome,
                        "recommendation": report.recommendation,
                        "pdf_url": request.build_absolute_uri(
                            f"/api/reports/{report.id}/pdf/"
                        ),
                    }
                )

            encounter = report.encounter
            if encounter and encounter.id not in seen_encounter_ids:
                seen_encounter_ids.add(encounter.id)
                encounter_rows.append(
                    {
                        "id": encounter.id,
                        "encounter_id": encounter.encounter_id,
                        "encounter_date": encounter.encounter_date,
                        "screening_status": encounter.screening_status,
                        "encounter_type": encounter.encounter_type,
                    }
                )

            if encounter:
                for upload in encounter.image_uploads.all():
                    if upload.id in seen_upload_ids:
                        continue
                    seen_upload_ids.add(upload.id)

                    image_file = getattr(upload, "image_file", None)
                    analysis = getattr(upload, "ai_analysis", None)

                    upload_rows.append(
                        {
                            "id": upload.id,
                            "image_upload_id": upload.image_upload_id,
                            "encounter": encounter.id,
                            "encounter_display": encounter.encounter_id,
                            "eye_laterality": upload.eye_laterality,
                            "image_quality": upload.image_quality,
                            "uploaded_at": upload.uploaded_at,
                            "image_file": (
                                request.build_absolute_uri(image_file.url)
                                if image_file
                                else ""
                            ),
                            "ai_analysis": (
                                {
                                    "prediction": getattr(
                                        analysis, "prediction", ""
                                    ),
                                    "confidence": getattr(
                                        analysis, "confidence", None
                                    ),
                                }
                                if analysis
                                else None
                            ),
                        }
                    )

        return Response(
            {
                "patient": {
                    "id": patient.id,
                    "patient_id": patient.patient_id,
                    "first_name": patient.first_name,
                    "last_name": patient.last_name,
                    "date_of_birth": patient.date_of_birth,
                    "sex": patient.sex,
                    "phone": patient.phone,
                    "email": patient.email,
                    "consent_status": patient.consent_status,
                    "referral_id": patient.referral_id,
                },
                "referrals": referral_rows,
                "encounters": encounter_rows,
                "reports": report_rows,
                "uploads": upload_rows,
            }
        )


class HospitalPayoutListView(HospitalReferralQuerysetMixin, generics.ListAPIView):
    serializer_class = HospitalReferralSerializer
    permission_classes = [IsAuthenticated, IsHospitalUser]

    def get_queryset(self):
        return super().get_queryset().exclude(payout_status="not_due").order_by("-updated_at")


class HospitalDashboardView(APIView):
    permission_classes = [IsAuthenticated, IsHospitalUser]

    def get(self, request):
        user = request.user
        referrals = HospitalReferral.objects.all()

        if not user.is_superuser:
            org = get_user_organization(user)
            if not org:
                referrals = HospitalReferral.objects.none()
            else:
                referrals = referrals.filter(source_hospital=org)

        patient_count = referrals.exclude(
            patient__isnull=True
        ).values("patient_id").distinct().count()

        data = {
            "total_patients": patient_count,
            "total_referrals": referrals.count(),
            "submitted": referrals.filter(referral_status="submitted").count(),
            "clinic_matched": referrals.filter(referral_status="clinic_matched").count(),
            "in_clinic": referrals.filter(referral_status="in_clinic").count(),
            "report_issued": referrals.filter(referral_status="report_issued").count(),
            "completed": referrals.filter(referral_status="completed").count(),
            "cancelled": referrals.filter(referral_status="cancelled").count(),
            "payout_pending": referrals.filter(payout_status="pending").count(),
            "payout_approved": referrals.filter(payout_status="approved").count(),
            "payout_paid": referrals.filter(payout_status="paid").count(),
        }

        return Response(data)


def _normalise_diabetes_type(value):
    mapping = {
        "type_1": "type 1",
        "type_2": "type 2",
        "other": "other",
        "unknown": "unknown",
    }
    return mapping.get(value, value or "unknown")


def _normalise_patient_sex(value):
    mapping = {
        "male": "male",
        "female": "female",
        "prefer_not_to_say": "prefer not to say",
    }
    return mapping.get(value, value or "")


def _create_or_update_patient_from_referral(org, data, referral_id):
    patient_id = data.get("patient_id") or referral_id

    patient, _created = Patient.objects.update_or_create(
        patient_id=patient_id,
        defaults={
            "first_name": data["first_name"],
            "last_name": data["last_name"],
            "date_of_birth": data["dob"],
            "sex": _normalise_patient_sex(data["patient_sex"]),
            "phone": data.get("phone_number", ""),
            "email": data.get("email", ""),
            "country": "Nigeria",
            "consent_status": "pending",
            "source_system": "hospital_portal",
            "referral_id": referral_id,
            "referral_status": "submitted",
        },
    )
    return patient


class HospitalReferralSubmitView(APIView):
    """
    Backend-owned hospital referral submission.

    This no longer depends on Basecrow/Baserow to create the referral.
    The Django backend creates the master referral_id and patient record directly.
    """

    permission_classes = [IsAuthenticated, IsHospitalUser]

    def post(self, request):
        serializer = HospitalReferralSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        org = get_user_organization(user)

        if not org:
            return Response(
                {"detail": "You are not linked to a hospital organization."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if org.organization_type != "hospital":
            return Response(
                {"detail": "Only hospital users can submit hospital referrals."},
                status=status.HTTP_403_FORBIDDEN,
            )

        data = serializer.validated_data
        referral_id = generate_referral_id()

        patient = _create_or_update_patient_from_referral(
            org=org,
            data=data,
            referral_id=referral_id,
        )

        hospital_referral = HospitalReferral.objects.create(
            referral_id=referral_id,
            source_hospital=org,
            patient=patient,
            patient_id_text=data.get("patient_id", patient.patient_id),
            first_name=data["first_name"],
            last_name=data["last_name"],
            dob=data["dob"],
            patient_sex=_normalise_patient_sex(data["patient_sex"]),
            hospital_mrn=data.get("hospital_mrn", ""),
            diabetes_type=_normalise_diabetes_type(data.get("diabetes_type", "")),
            reason_for_referral=data["reason_for_referral"],
            phone_number=data.get("phone_number", ""),
            email=data.get("email", ""),
            referral_date=timezone.now(),
            referral_status="submitted",
            source_system="backend_ops",
            notes=data.get("notes", ""),
            submitted_by_username=user.username,
        )

        return Response(
            {
                "message": "Hospital referral submitted successfully.",
                "referral_id": hospital_referral.referral_id,
                "hospital_name": org.name,
                "hospital_referral_id": hospital_referral.id,
                "patient_id": patient.patient_id,
            },
            status=status.HTTP_201_CREATED,
        )


class HospitalReferralStatusSyncView(APIView):
    """
    Temporary compatibility endpoint.

    This is retained so old Basecrow/Pipedream syncs do not break immediately,
    but the new source of truth is Django backend Ops.
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        token = request.headers.get("X-SENTINEL-SYNC-TOKEN")
        import os
        expected = os.environ.get("SENTINEL_SYNC_TOKEN", "")

        if not expected or token != expected:
            return Response(
                {"detail": "Unauthorized"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = HospitalReferralStatusSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            hospital_referral = HospitalReferral.objects.select_related(
                "patient",
                "matched_clinic",
                "report",
                "source_hospital",
            ).get(referral_id=data["referral_id"])
        except HospitalReferral.DoesNotExist:
            return Response(
                {
                    "detail": "Hospital referral not found.",
                    "referral_id": data["referral_id"],
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        updated_fields = []

        matched_clinic = None
        matched_clinic_code = data.get("matched_clinic_code", "").strip()
        matched_clinic_name = data.get("matched_clinic_name", "").strip()

        if matched_clinic_code:
            matched_clinic = Organization.objects.filter(clinic_id=matched_clinic_code).first()
        elif matched_clinic_name:
            matched_clinic = Organization.objects.filter(name__iexact=matched_clinic_name).first()

        if matched_clinic and hospital_referral.matched_clinic_id != matched_clinic.id:
            hospital_referral.matched_clinic = matched_clinic
            updated_fields.append("matched_clinic")

            if hospital_referral.patient:
                hospital_referral.patient.assigned_clinic = matched_clinic
                hospital_referral.patient.referral_status = "clinic_matched"
                hospital_referral.patient.save(update_fields=["assigned_clinic", "referral_status", "updated_at"])

        linked_patient_id = data.get("linked_patient_id", "").strip()
        if linked_patient_id:
            patient = Patient.objects.filter(patient_id=linked_patient_id).first()
            if patient and hospital_referral.patient_id != patient.id:
                hospital_referral.patient = patient
                updated_fields.append("patient")

        report_id = data.get("report_id", "").strip()
        if report_id:
            report = StructuredReport.objects.filter(report_id=report_id).first()
            if report and hospital_referral.report_id != report.id:
                hospital_referral.report = report
                hospital_referral.report_ready = report.report_status == "issued"
                updated_fields.extend(["report", "report_ready"])

        if "report_ready" in data:
            if hospital_referral.report_ready != data["report_ready"]:
                hospital_referral.report_ready = data["report_ready"]
                updated_fields.append("report_ready")

        if "payout_status" in data:
            if hospital_referral.payout_status != data["payout_status"]:
                hospital_referral.payout_status = data["payout_status"]
                updated_fields.append("payout_status")

        if data.get("notes"):
            hospital_referral.notes = data["notes"]
            updated_fields.append("notes")

        requested_status = data.get("referral_status")

        if requested_status:
            hospital_referral.referral_status = requested_status
        else:
            if (
                hospital_referral.report_id
                and hospital_referral.report.report_status == "issued"
            ):
                hospital_referral.referral_status = "report_issued"
            elif (
                hospital_referral.report_id
                and hospital_referral.report.report_status == "submitted_to_ops"
            ):
                hospital_referral.referral_status = "submitted_to_ops"
            elif (
                hospital_referral.report_id
                and hospital_referral.report.report_status in {"returned_to_clinic", "ops_rejected"}
            ):
                hospital_referral.referral_status = "returned_to_clinic"
            elif hospital_referral.report_id:
                hospital_referral.referral_status = "report_created"
            elif hospital_referral.matched_clinic_id:
                hospital_referral.referral_status = "clinic_matched"
            else:
                hospital_referral.referral_status = "submitted"

        updated_fields.append("referral_status")
        hospital_referral.updated_at = timezone.now()
        updated_fields.append("updated_at")

        hospital_referral.save(update_fields=list(dict.fromkeys(updated_fields)))

        return Response(
            {
                "message": "Hospital referral synced successfully.",
                "referral_id": hospital_referral.referral_id,
                "referral_status": hospital_referral.referral_status,
                "matched_clinic": hospital_referral.matched_clinic.name if hospital_referral.matched_clinic else "",
                "patient_id": hospital_referral.patient.patient_id if hospital_referral.patient else "",
                "report_ready": hospital_referral.report_ready,
                "payout_status": hospital_referral.payout_status,
            },
            status=status.HTTP_200_OK,
        )


class MatchClinicView(APIView):
    """
    Backend Ops clinic matching endpoint.

    Expected JSON:
    {
      "referral_id": "SNT-REF-2026-000001",
      "clinic_id": 3
    }

    clinic_id is the Django Organization primary key.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        referral_id = (request.data.get("referral_id") or "").strip()
        clinic_id = request.data.get("clinic_id")

        if not referral_id or not clinic_id:
            raise ValidationError("referral_id and clinic_id are required.")

        try:
            referral = HospitalReferral.objects.select_related("patient").get(referral_id=referral_id)
        except HospitalReferral.DoesNotExist:
            return Response(
                {"detail": "Referral not found.", "referral_id": referral_id},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            clinic = Organization.objects.get(id=clinic_id)
        except Organization.DoesNotExist:
            return Response(
                {"detail": "Clinic organization not found.", "clinic_id": clinic_id},
                status=status.HTTP_404_NOT_FOUND,
            )

        if clinic.organization_type != "clinic":
            return Response(
                {"detail": "Selected organization is not a clinic."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        referral.matched_clinic = clinic
        referral.referral_status = "clinic_matched"
        referral.save(update_fields=["matched_clinic", "referral_status", "updated_at"])

        if referral.patient:
            referral.patient.assigned_clinic = clinic
            referral.patient.referral_status = "clinic_matched"
            referral.patient.save(update_fields=["assigned_clinic", "referral_status", "updated_at"])

        return Response(
            {
                "message": "Clinic matched successfully.",
                "referral_id": referral.referral_id,
                "matched_clinic": clinic.name,
                "referral_status": referral.referral_status,
            },
            status=status.HTTP_200_OK,
        )
