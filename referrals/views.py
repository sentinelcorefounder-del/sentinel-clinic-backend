import os

from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from organizations.models import Organization
from patients.models import Patient
from reports.models import StructuredReport
from .models import HospitalReferral
from .permissions import IsHospitalUser
from .serializers import HospitalReferralSerializer
from .services_baserow import create_hospital_intake_row, find_hospital_row_id
from .submit_serializers import HospitalReferralSubmitSerializer
from .sync_serializers import HospitalReferralStatusSyncSerializer


def generate_submission_id(org_code: str) -> str:
    timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"SUB-{org_code}-{timestamp}"


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
        ).all()

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

        data = {
            "total_referrals": referrals.count(),
            "submitted": referrals.filter(referral_status="submitted").count(),
            "clinic_matched": referrals.filter(referral_status="clinic_matched").count(),
            "completed": referrals.filter(referral_status="completed").count(),
            "cancelled": referrals.filter(referral_status="cancelled").count(),
            "payout_pending": referrals.filter(payout_status="pending").count(),
            "payout_approved": referrals.filter(payout_status="approved").count(),
            "payout_paid": referrals.filter(payout_status="paid").count(),
        }

        return Response(data)


class HospitalReferralSubmitView(APIView):
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
        submission_id = generate_submission_id(org.clinic_id)

        referrer_name = (
            user.get_full_name().strip()
            if hasattr(user, "get_full_name") and user.get_full_name().strip()
            else user.username
        )

        try:
            referring_hospital_row_id = find_hospital_row_id(
                hospital_id=org.clinic_id,
                name=org.name,
            )
        except Exception as exc:
            return Response(
                {
                    "detail": "Failed to resolve referring hospital row.",
                    "error": str(exc),
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if not referring_hospital_row_id:
            return Response(
                {
                    "detail": "Hospital could not be matched to a Basecrow hospitals row.",
                    "error": f"No Hospitals row found for clinic_id={org.clinic_id} or name={org.name}",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        diabetes_type_display = {
            "type_1": "type 1",
            "type_2": "type 2",
            "other": "other",
            "unknown": "unknown",
        }[data["diabetes_type"]]

        patient_sex_display = {
            "male": "male",
            "female": "female",
            "prefer_not_to_say": "prefer not to say",
        }[data["patient_sex"]]

        baserow_payload = {
            "Submission ID": submission_id,
            "Patient ID": data["patient_id"],
            "First Name": data["first_name"],
            "Last Name": data["last_name"],
            "DOB": data["dob"].isoformat(),
            "Patient Sex": patient_sex_display,
            "Hospital MRN / Local Patient Number": data.get("hospital_mrn", ""),
            "Phone Number": data.get("phone_number", ""),
            "Email": data.get("email", ""),
            "Diabetes Type": diabetes_type_display,
            "Referring Hospital": [referring_hospital_row_id],
            "Reason for referral": data["reason_for_referral"],
            "Referrer Name": referrer_name,
            "Notes": data.get("notes", ""),
            "Processed": False,
        }

        try:
            created_row = create_hospital_intake_row(baserow_payload)
        except Exception as exc:
            return Response(
                {
                    "detail": "Failed to create hospital intake row.",
                    "error": str(exc),
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        hospital_referral = HospitalReferral.objects.create(
            referral_id=submission_id,
            source_hospital=org,
            patient_id_text=data["patient_id"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            dob=data["dob"],
            patient_sex=patient_sex_display,
            hospital_mrn=data.get("hospital_mrn", ""),
            diabetes_type=diabetes_type_display,
            reason_for_referral=data["reason_for_referral"],
            phone_number=data.get("phone_number", ""),
            email=data.get("email", ""),
            referral_date=timezone.now(),
            referral_status="submitted",
            baserow_row_id=created_row.get("id"),
            source_system="hospital_portal",
            notes=data.get("notes", ""),
            submitted_by_username=user.username,
        )

        return Response(
            {
                "message": "Hospital referral submitted successfully.",
                "submission_id": submission_id,
                "hospital_intake_row_id": created_row.get("id"),
                "hospital_name": org.name,
                "hospital_referral_id": hospital_referral.id,
            },
            status=status.HTTP_201_CREATED,
        )


class HospitalReferralStatusSyncView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        token = request.headers.get("X-SENTINEL-SYNC-TOKEN")
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
                updated_fields.append("report")

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
            if hospital_referral.report_ready or hospital_referral.report_id:
                hospital_referral.referral_status = "completed"
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