import hashlib
import hmac
import json
from decimal import Decimal

from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from organizations.models import Organization, OrganizationProfile
from organizations.services.provisioning import (
    provision_clinic_with_admin,
    provision_hospital_with_admin,
)
from patients.models import Patient
from payments.services.paystack import initialize_transaction, verify_transaction
from referrals.models import HospitalReferral
from reports.models import StructuredReport, ReportStatusEvent
from users.models import UserSecurityProfile

from .models import OpsAuditLog, OpsNotification, OpsPayment
from .serializers import (
    OpsAuditLogSerializer,
    OpsNotificationSerializer,
    OpsPaymentSerializer,
    OpsReferralSerializer,
    OpsReportSerializer,
    OpsOrganizationCapabilitySerializer,
)

try:
    from uploads.models import ImageUpload
except Exception:
    ImageUpload = None


User = get_user_model()


def user_is_ops(user):
    if user.is_superuser:
        return True
    roles = set(user.groups.values_list("name", flat=True))
    return bool({"ops_admin", "sentinel_ops", "super_admin"} & roles)



def get_or_create_capability_profile(organization):
    profile, _ = OrganizationProfile.objects.get_or_create(
        organization=organization
    )
    return profile


def capability_profile_data(organization):
    profile = get_or_create_capability_profile(organization)
    return OpsOrganizationCapabilitySerializer(profile).data


def generate_payment_id(referral):
    return f"PAY-{referral.referral_id}"


def create_audit_log(
    *,
    actor=None,
    action,
    entity_type="",
    entity_id="",
    entity_label="",
    message="",
    metadata=None,
):
    return OpsAuditLog.objects.create(
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id or ""),
        entity_label=entity_label or "",
        message=message or "",
        metadata=metadata or {},
    )


def create_ops_notification(
    *,
    title,
    message="",
    level="info",
    entity_type="",
    entity_id="",
    entity_label="",
    created_by=None,
):
    return OpsNotification.objects.create(
        title=title,
        message=message,
        level=level,
        entity_type=entity_type,
        entity_id=str(entity_id or ""),
        entity_label=entity_label or "",
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
    )


def mark_payment_as_paid(payment, verify_data):
    amount_received = Decimal(str(verify_data.get("amount", 0))) / Decimal("100")

    if amount_received != payment.amount:
        payment.status = "exception"
        payment.amount_received = amount_received
        payment.internal_notes = (
            f"Amount mismatch. Expected {payment.amount}, got {amount_received}."
        )
        payment.save(update_fields=["status", "amount_received", "internal_notes", "updated_at"])
        return False, "Payment amount does not match the expected amount."

    payment.status = "paid"
    payment.amount_received = amount_received
    payment.paid_at = verify_data.get("paid_at") or timezone.now()
    payment.internal_notes = "Verified via Paystack."
    payment.save(update_fields=["status", "amount_received", "paid_at", "internal_notes", "updated_at"])

    referral = payment.referral
    note = "Payment verified via Paystack. Patient payment completed."

    if note not in (referral.notes or ""):
        referral.notes = f"{referral.notes or ''}\n{note}".strip()

    referral.save(update_fields=["notes", "updated_at"])

    return True, "Payment verified successfully."


def notify_clinic_of_matched_referral(referral, clinic):
    recipient = clinic.contact_email
    if not recipient:
        return False

    patient_name = f"{referral.first_name} {referral.last_name}".strip()
    subject = f"New Sentinel referral assigned: {referral.referral_id}"
    message = f"""
Hello {clinic.name},

A new patient referral has been assigned to your clinic.

Referral ID: {referral.referral_id}
Patient: {patient_name}
DOB: {referral.dob or "-"}
Sex: {referral.patient_sex or "-"}
Phone: {referral.phone_number or "-"}
Email: {referral.email or "-"}
Source Hospital: {referral.source_hospital.name if referral.source_hospital else "-"}

Please log in to the Sentinel Clinic Portal to continue the screening workflow.

Thank you,
Sentinel Health
""".strip()

    send_mail(
        subject=subject,
        message=message,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
        recipient_list=[recipient],
        fail_silently=False,
    )

    return True


class OpsOnlyMixin:
    permission_classes = [IsAuthenticated]

    def check_ops_permission(self, request):
        if not user_is_ops(request.user):
            return Response(
                {"detail": "Only Sentinel Ops users can access this endpoint."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return None


class OpsDashboardView(OpsOnlyMixin, APIView):
    def get(self, request):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        referrals = HospitalReferral.objects.all()
        payments = OpsPayment.objects.all()
        reports = StructuredReport.objects.all()
        patients = Patient.objects.all()

        return Response(
            {
                "referrals": {
                    "total": referrals.count(),
                    "submitted": referrals.filter(referral_status="submitted").count(),
                    "clinic_matched": referrals.filter(referral_status="clinic_matched").count(),
                    "in_clinic": referrals.filter(referral_status="in_clinic").count(),
                    "report_issued": referrals.filter(referral_status="report_issued").count(),
                    "completed": referrals.filter(referral_status="completed").count(),
                    "cancelled": referrals.filter(referral_status="cancelled").count(),
                },
                "payments": {
                    "total": payments.count(),
                    "draft": payments.filter(status="draft").count(),
                    "pending": payments.filter(status="pending").count(),
                    "paid": payments.filter(status="paid").count(),
                    "failed": payments.filter(status="failed").count(),
                    "exception": payments.filter(status="exception").count(),
                },
                "reports": {
                    "total": reports.count(),
                    "submitted_to_ops": reports.filter(report_status="submitted_to_ops").count(),
                    "ops_approved": reports.filter(report_status="ops_approved").count(),
                    "ops_rejected": reports.filter(report_status="ops_rejected").count(),
                    "issued": reports.filter(report_status="issued").count(),
                },
                "network": {
                    "patients": patients.count(),
                    "clinics": Organization.objects.filter(organization_type="clinic").count(),
                    "hospitals": Organization.objects.filter(organization_type="hospital").count(),
                },
            }
        )


class OpsReferralListView(OpsOnlyMixin, generics.ListAPIView):
    serializer_class = OpsReferralSerializer

    def get_queryset(self):
        if not user_is_ops(self.request.user):
            return HospitalReferral.objects.none()

        return HospitalReferral.objects.select_related(
            "source_hospital",
            "matched_clinic",
            "patient",
            "report",
        ).prefetch_related("ops_payments").order_by("-created_at")


class OpsReferralDetailView(OpsOnlyMixin, generics.RetrieveAPIView):
    serializer_class = OpsReferralSerializer

    def get_queryset(self):
        if not user_is_ops(self.request.user):
            return HospitalReferral.objects.none()

        return HospitalReferral.objects.select_related(
            "source_hospital",
            "matched_clinic",
            "patient",
            "report",
        ).prefetch_related("ops_payments")


class OpsAssignClinicView(OpsOnlyMixin, APIView):
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        clinic_id = request.data.get("clinic_id")
        if not clinic_id:
            return Response({"detail": "clinic_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        referral = HospitalReferral.objects.select_related("patient", "source_hospital").filter(pk=pk).first()
        if not referral:
            return Response({"detail": "Referral not found."}, status=status.HTTP_404_NOT_FOUND)

        clinic = Organization.objects.filter(pk=clinic_id, organization_type="clinic").first()
        if not clinic:
            return Response({"detail": "Clinic not found."}, status=status.HTTP_404_NOT_FOUND)

        patient = referral.patient

        if not patient:
            base_patient_id = f"PAT-{referral.referral_id}"
            patient, _ = Patient.objects.update_or_create(
                patient_id=base_patient_id,
                defaults={
                    "first_name": referral.first_name or "Unknown",
                    "last_name": referral.last_name or "Patient",
                    "date_of_birth": referral.dob,
                    "sex": referral.patient_sex or "",
                    "phone": referral.phone_number or "",
                    "email": referral.email or "",
                    "country": "Nigeria",
                    "consent_status": "pending",
                    "assigned_clinic": clinic,
                    "referral_id": referral.referral_id,
                    "referral_status": "clinic_matched",
                    "source_system": "sentinel_ops",
                },
            )
            referral.patient = patient
        else:
            patient.assigned_clinic = clinic
            patient.referral_status = "clinic_matched"
            patient.referral_id = referral.referral_id
            patient.save(update_fields=["assigned_clinic", "referral_status", "referral_id", "updated_at"])

        referral.matched_clinic = clinic
        referral.referral_status = "clinic_matched"
        referral.notes = f"{referral.notes or ''}\nAssigned to clinic: {clinic.name}. Patient linked to clinic record.".strip()
        referral.save(update_fields=["matched_clinic", "referral_status", "patient", "notes", "updated_at"])

        clinic_email_sent = False
        try:
            clinic_email_sent = notify_clinic_of_matched_referral(referral, clinic)
        except Exception as exc:
            referral.notes = f"{referral.notes or ''}\nClinic notification email failed: {str(exc)}".strip()
            referral.save(update_fields=["notes", "updated_at"])

        create_audit_log(
            actor=request.user,
            action="clinic_assigned",
            entity_type="referral",
            entity_id=referral.id,
            entity_label=referral.referral_id,
            message=f"Referral {referral.referral_id} assigned to clinic {clinic.name}.",
            metadata={
                "clinic_id": clinic.id,
                "clinic_name": clinic.name,
                "patient_id": patient.id,
                "clinic_email_sent": clinic_email_sent,
            },
        )

        create_ops_notification(
            title="Clinic assigned",
            message=f"Referral {referral.referral_id} was assigned to {clinic.name}. Clinic email sent: {clinic_email_sent}.",
            level="info",
            entity_type="referral",
            entity_id=referral.id,
            entity_label=referral.referral_id,
            created_by=request.user,
        )

        return Response(
            {
                "message": "Clinic assigned successfully and patient linked.",
                "clinic_email_sent": clinic_email_sent,
                "patient_id": patient.id,
                "referral": OpsReferralSerializer(referral).data,
            }
        )


class OpsPaymentListView(OpsOnlyMixin, generics.ListAPIView):
    serializer_class = OpsPaymentSerializer

    def get_queryset(self):
        if not user_is_ops(self.request.user):
            return OpsPayment.objects.none()

        return OpsPayment.objects.select_related(
            "referral",
            "referral__source_hospital",
            "referral__matched_clinic",
        ).order_by("-created_at")


class CreatePaymentForReferralView(OpsOnlyMixin, APIView):
    def post(self, request, referral_pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        referral = HospitalReferral.objects.filter(pk=referral_pk).first()
        if not referral:
            return Response({"detail": "Referral not found."}, status=status.HTTP_404_NOT_FOUND)

        hospital = referral.source_hospital

        default_amount = (
            hospital.screening_fee_amount
            if hospital and hospital.screening_fee_amount
            else Decimal("15000")
        )

        default_currency = (
            hospital.currency
            if hospital and hospital.currency
            else "NGN"
        )

        # Ops can still override amount manually if needed.
        # If no amount is sent, use the source hospital's configured screening fee.
        try:
            amount = Decimal(
                str(
                    request.data.get("amount", default_amount)
                ).replace(",", "").strip()
            )
        except Exception:
            return Response({"detail": "Invalid amount."}, status=status.HTTP_400_BAD_REQUEST)

        patient_email = (request.data.get("patient_email") or referral.email or "").strip()
        if not patient_email:
            return Response(
                {"detail": "Patient email is required before creating a payment link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payment, created = OpsPayment.objects.get_or_create(
            referral=referral,
            payment_id=generate_payment_id(referral),
            defaults={
                "patient_email": patient_email,
                "amount": amount,
                "currency": default_currency,
                "status": "draft",
            },
        )

        if not created:
            payment.patient_email = patient_email
            payment.amount = amount
            payment.currency = default_currency
            payment.save(update_fields=["patient_email", "amount", "currency", "updated_at"])

        create_audit_log(
            actor=request.user,
            action="payment_created",
            entity_type="payment",
            entity_id=payment.id,
            entity_label=payment.payment_id,
            message=f"Payment record {payment.payment_id} created for referral {referral.referral_id}.",
            metadata={"referral_id": referral.referral_id, "created": created},
        )

        return Response(
            {
                "message": "Payment record ready.",
                "created": created,
                "payment": OpsPaymentSerializer(payment).data,
            }
        )


class InitializeOpsPaymentView(OpsOnlyMixin, APIView):
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        payment = OpsPayment.objects.select_related("referral").filter(pk=pk).first()
        if not payment:
            return Response({"detail": "Payment not found."}, status=status.HTTP_404_NOT_FOUND)

        if payment.status == "paid":
            return Response({"detail": "This payment has already been paid."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            result = initialize_transaction(
                email=payment.patient_email,
                amount_kobo=int(payment.amount * 100),
                reference=payment.payment_id,
            )
        except Exception as exc:
            payment.status = "failed"
            payment.internal_notes = f"Paystack initialization failed: {str(exc)}"
            payment.save(update_fields=["status", "internal_notes", "updated_at"])
            return Response(
                {"detail": "Paystack initialization failed.", "error": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        data = result.get("data", {})
        payment.payment_link = data.get("authorization_url", "")
        payment.paystack_reference = data.get("reference", payment.payment_id)
        payment.status = "pending"
        payment.internal_notes = "Payment link created via Sentinel Ops."
        payment.save(
            update_fields=[
                "payment_link",
                "paystack_reference",
                "status",
                "internal_notes",
                "updated_at",
            ]
        )

        email_sent = False

        if payment.patient_email and payment.payment_link:
            referral = payment.referral
            patient_name = f"{referral.first_name} {referral.last_name}".strip() or "Patient"

            subject = "Complete your Sentinel eye screening payment"
            message = f"""
Hello {patient_name},

Your Sentinel diabetic eye screening payment link is ready.

Amount: {payment.currency} {payment.amount}
Payment link: {payment.payment_link}

Please complete your payment using the link above. Once payment is confirmed, Sentinel Ops will continue your screening pathway.

Thank you,
Sentinel Health
""".strip()

            try:
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                    recipient_list=[payment.patient_email],
                    fail_silently=False,
                )
                email_sent = True
                payment.internal_notes = "Payment link created and emailed to patient."
                payment.save(update_fields=["internal_notes", "updated_at"])
            except Exception as exc:
                payment.internal_notes = f"Payment link created, but email failed: {str(exc)}"
                payment.save(update_fields=["internal_notes", "updated_at"])

        create_audit_log(
            actor=request.user,
            action="payment_link_generated",
            entity_type="payment",
            entity_id=payment.id,
            entity_label=payment.payment_id,
            message=f"Payment link generated for {payment.payment_id}. Email sent: {email_sent}.",
            metadata={
                "payment_link": payment.payment_link,
                "email_sent": email_sent,
                "paystack_reference": payment.paystack_reference,
            },
        )

        create_ops_notification(
            title="Payment link generated",
            message=f"Payment link generated for {payment.payment_id}. Email sent: {email_sent}.",
            level="info",
            entity_type="payment",
            entity_id=payment.id,
            entity_label=payment.payment_id,
            created_by=request.user,
        )

        return Response(
            {
                "message": "Payment link created.",
                "payment_link": payment.payment_link,
                "paystack_reference": payment.paystack_reference,
                "email_sent": email_sent,
                "payment": OpsPaymentSerializer(payment).data,
            }
        )


class VerifyOpsPaymentView(OpsOnlyMixin, APIView):
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        payment = OpsPayment.objects.select_related("referral").filter(pk=pk).first()
        if not payment:
            return Response({"detail": "Payment not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            verify = verify_transaction(payment.paystack_reference or payment.payment_id)
        except Exception as exc:
            return Response(
                {"detail": "Payment verification failed. Please check the Paystack key and try again.", "error": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        verify_data = verify.get("data", {})
        if verify.get("status") is not True or verify_data.get("status") != "success":
            return Response(
                {
                    "detail": "Payment is not successful yet.",
                    "paystack_status": verify_data.get("status"),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        success, message = mark_payment_as_paid(payment, verify_data)
        if not success:
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)

        create_audit_log(
            actor=request.user,
            action="payment_verified",
            entity_type="payment",
            entity_id=payment.id,
            entity_label=payment.payment_id,
            message=f"Payment {payment.payment_id} verified successfully.",
            metadata={
                "amount_received": str(payment.amount_received),
                "paid_at": str(payment.paid_at),
            },
        )

        create_ops_notification(
            title="Payment received",
            message=f"Payment {payment.payment_id} has been verified successfully.",
            level="success",
            entity_type="payment",
            entity_id=payment.id,
            entity_label=payment.payment_id,
            created_by=request.user,
        )

        return Response(
            {
                "success": True,
                "message": message,
                "payment": OpsPaymentSerializer(payment).data,
            }
        )


class OpsReportApprovalQueueView(OpsOnlyMixin, generics.ListAPIView):
    serializer_class = OpsReportSerializer

    def get_queryset(self):
        if not user_is_ops(self.request.user):
            return StructuredReport.objects.none()

        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).prefetch_related(
            "hospital_referrals",
            "hospital_referrals__source_hospital",
            "hospital_referrals__ops_payments",
        )

        report_status = (
            self.request.query_params.get("status")
            or self.request.query_params.get("report_status")
            or "submitted_to_ops"
        )

        if report_status and report_status != "all":
            queryset = queryset.filter(report_status=report_status)

        clinic = (self.request.query_params.get("clinic") or "").strip()
        if clinic:
            if clinic.isdigit():
                queryset = queryset.filter(patient__assigned_clinic_id=clinic)
            else:
                queryset = queryset.filter(
                    models.Q(patient__assigned_clinic__name__icontains=clinic)
                    | models.Q(patient__assigned_clinic__clinic_id__icontains=clinic)
                )

        hospital = (self.request.query_params.get("hospital") or "").strip()
        if hospital:
            if hospital.isdigit():
                queryset = queryset.filter(hospital_referrals__source_hospital_id=hospital)
            else:
                queryset = queryset.filter(
                    models.Q(hospital_referrals__source_hospital__name__icontains=hospital)
                    | models.Q(hospital_referrals__source_hospital__clinic_id__icontains=hospital)
                )

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                models.Q(report_id__icontains=search)
                | models.Q(patient__patient_id__icontains=search)
                | models.Q(patient__first_name__icontains=search)
                | models.Q(patient__last_name__icontains=search)
                | models.Q(hospital_referrals__referral_id__icontains=search)
            )

        return queryset.distinct().order_by("-submitted_to_ops_at", "-created_at")


class OpsReportDetailView(OpsOnlyMixin, APIView):
    def get(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        report = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).prefetch_related(
            "encounter__image_uploads",
            "encounter__image_uploads__ai_analysis",
            "hospital_referrals",
            "hospital_referrals__source_hospital",
            "hospital_referrals__matched_clinic",
            "hospital_referrals__ops_payments",
            "status_events",
            "status_events__actor",
        ).filter(pk=pk).first()

        if not report:
            return Response({"detail": "Report not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            OpsReportSerializer(report, context={"request": request}).data
        )


class OpsReportReturnView(OpsOnlyMixin, APIView):
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        report = StructuredReport.objects.filter(pk=pk).first()
        if not report:
            return Response({"detail": "Report not found."}, status=status.HTTP_404_NOT_FOUND)

        if report.report_status != "submitted_to_ops":
            return Response(
                {"detail": f"Only submitted_to_ops reports can be returned. Current status: {report.report_status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = (request.data.get("reason") or request.data.get("note") or "").strip()
        if not reason:
            return Response(
                {"detail": "A return reason is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        previous_status = report.report_status
        report.report_status = "returned_to_clinic"
        report.return_reason = reason
        report.ops_review_note = reason
        report.ops_reviewed_at = timezone.now()
        report.ops_reviewed_by = request.user
        report.save(
            update_fields=[
                "report_status",
                "return_reason",
                "ops_review_note",
                "ops_reviewed_at",
                "ops_reviewed_by",
                "updated_at",
            ]
        )

        ReportStatusEvent.objects.create(
            report=report,
            event_type="returned_to_clinic",
            from_status=previous_status,
            to_status="returned_to_clinic",
            note=reason,
            actor=request.user,
        )

        create_audit_log(
            actor=request.user,
            action="report_returned",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            message=f"Report {report.report_id} returned to clinic.",
            metadata={"reason": reason},
        )

        create_ops_notification(
            title="Report returned to clinic",
            message=f"Report {report.report_id} was returned for correction.",
            level="warning",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            created_by=request.user,
        )

        return Response(
            {
                "message": "Report returned to clinic.",
                "report": OpsReportSerializer(report, context={"request": request}).data,
            }
        )


class OpsReportApproveView(OpsOnlyMixin, APIView):
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        report = StructuredReport.objects.filter(pk=pk).first()

        if not report:
            return Response(
                {"detail": "Report not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if report.report_status not in {
            "submitted_to_ops",
            "ops_rejected",
        }:
            return Response(
                {
                    "detail": (
                        "Only submitted_to_ops or ops_rejected "
                        "reports can be approved and issued. "
                        f"Current status: {report.report_status}"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        signer_name = (
            request.data.get("signer_name") or ""
        ).strip()

        signer_role = (
            request.data.get("signer_role") or ""
        ).strip()

        signer_registration_number = (
            request.data.get(
                "signer_registration_number"
            )
            or ""
        ).strip()

        missing_signature_fields = []

        if not signer_name:
            missing_signature_fields.append(
                "clinician name"
            )

        if not signer_role:
            missing_signature_fields.append(
                "professional role"
            )

        if not signer_registration_number:
            missing_signature_fields.append(
                "registration number"
            )

        if missing_signature_fields:
            return Response(
                {
                    "detail": (
                        "Clinical sign-off is incomplete. Missing: "
                        + ", ".join(missing_signature_fields)
                        + "."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        previous_status = report.report_status
        issued_time = timezone.now()

        report.report_status = "issued"

        report.ops_reviewed_at = issued_time
        report.ops_reviewed_by = request.user
        report.ops_review_note = (
            request.data.get("note") or ""
        ).strip()

        report.signed_by = request.user
        report.signed_at = issued_time
        report.signer_name = signer_name
        report.signer_role = signer_role
        report.signer_registration_number = (
            signer_registration_number
        )

        report.issued_by = request.user
        report.issued_at = issued_time

        report.save(
            update_fields=[
                "report_status",
                "ops_reviewed_at",
                "ops_reviewed_by",
                "ops_review_note",
                "signed_by",
                "signed_at",
                "signer_name",
                "signer_role",
                "signer_registration_number",
                "issued_by",
                "issued_at",
                "updated_at",
            ]
        )

        ReportStatusEvent.objects.create(
            report=report,
            event_type="issued",
            from_status=previous_status,
            to_status="issued",
            note=(
                report.ops_review_note
                or (
                    "Report reviewed, electronically "
                    "signed and issued by Sentinel Ops."
                )
            ),
            actor=request.user,
        )

        report.distribution_status = "awaiting_distribution"
        report.save(update_fields=["distribution_status", "updated_at"])

        encounter_referral = getattr(report.encounter, "hospital_referral", None)
        if encounter_referral:
            encounter_referral.report = report
            encounter_referral.report_ready = False
            encounter_referral.referral_status = "report_issued"
            encounter_referral.save(update_fields=[
                "report", "report_ready", "referral_status", "updated_at",
            ])

        for referral in report.hospital_referrals.exclude(
            pk=getattr(encounter_referral, "pk", None)
        ):
            referral.report_ready = False
            referral.referral_status = "report_issued"
            referral.save(update_fields=[
                "report_ready", "referral_status", "updated_at",
            ])

        ReportStatusEvent.objects.create(
            report=report,
            event_type="queued_for_distribution",
            from_status="issued",
            to_status="issued",
            note="Issued report queued for Sentinel distribution.",
            actor=request.user,
        )

        create_audit_log(
            actor=request.user,
            action="report_issued",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            message=(
                f"Report {report.report_id} reviewed, "
                "signed and issued by Sentinel Ops."
            ),
            metadata={
                "signer_name": signer_name,
                "signer_role": signer_role,
                "signer_registration_number": (
                    signer_registration_number
                ),
            },
        )

        create_ops_notification(
            title="Report issued",
            message=(
                f"Report {report.report_id} was reviewed, "
                "signed and issued by Sentinel Ops."
            ),
            level="success",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            created_by=request.user,
        )

        return Response(
            {
                "message": (
                    "Report approved, signed and issued "
                    "by Sentinel Ops."
                ),
                "report": OpsReportSerializer(
                    report,
                    context={"request": request},
                ).data,
            }
        )

class OpsReportRejectView(OpsOnlyMixin, APIView):
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        report = StructuredReport.objects.filter(pk=pk).first()
        if not report:
            return Response({"detail": "Report not found."}, status=status.HTTP_404_NOT_FOUND)

        if report.report_status != "submitted_to_ops":
            return Response(
                {"detail": f"Only submitted_to_ops reports can be rejected. Current status: {report.report_status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rejection_note = (request.data.get("note") or "").strip()
        if not rejection_note:
            return Response(
                {"detail": "A rejection reason is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        report.report_status = "ops_rejected"
        report.ops_reviewed_at = timezone.now()
        report.ops_reviewed_by = request.user
        report.ops_review_note = rejection_note
        report.save(update_fields=["report_status", "ops_reviewed_at", "ops_reviewed_by", "ops_review_note", "updated_at"])

        ReportStatusEvent.objects.create(
            report=report,
            event_type="rejected",
            from_status="submitted_to_ops",
            to_status="ops_rejected",
            note=report.ops_review_note,
            actor=request.user,
        )

        create_audit_log(
            actor=request.user,
            action="report_rejected",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            message=f"Report {report.report_id} rejected by Ops.",
            metadata={"note": report.ops_review_note},
        )

        create_ops_notification(
            title="Report rejected",
            message=f"Report {report.report_id} was rejected by Ops.",
            level="warning",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            created_by=request.user,
        )

        return Response({"message": "Report rejected by Ops.", "report": OpsReportSerializer(report).data})


class OpsDistributionQueueView(OpsOnlyMixin, APIView):
    def get(self, request):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        queue = (
            StructuredReport.objects.select_related(
                "patient",
                "patient__assigned_clinic",
                "encounter",
                "hospital_released_by",
            )
            .prefetch_related(
                "hospital_referrals",
                "hospital_referrals__source_hospital",
                "hospital_referrals__matched_clinic",
            )
            .filter(report_status="issued")
        )

        status_filter = (
            request.query_params.get("status")
            or "awaiting_distribution"
        ).strip()

        if status_filter and status_filter != "all":
            queue = queue.filter(distribution_status=status_filter)

        search = (request.query_params.get("search") or "").strip()
        if search:
            queue = queue.filter(
                models.Q(report_id__icontains=search)
                | models.Q(patient__patient_id__icontains=search)
                | models.Q(patient__first_name__icontains=search)
                | models.Q(patient__last_name__icontains=search)
                | models.Q(hospital_referrals__referral_id__icontains=search)
                | models.Q(
                    hospital_referrals__source_hospital__name__icontains=search
                )
            )

        data = []
        for report in queue.distinct().order_by("-issued_at", "-updated_at"):
            referral = report.hospital_referrals.select_related(
                "source_hospital", "matched_clinic"
            ).first()

            data.append({
                "id": report.id,
                "report_id": report.report_id,
                "patient_id": report.patient.patient_id if report.patient else "",
                "patient_name": (
                    f"{report.patient.first_name} {report.patient.last_name}".strip()
                    if report.patient else ""
                ),
                "clinic_name": (
                    report.patient.assigned_clinic.name
                    if report.patient and report.patient.assigned_clinic else ""
                ),
                "source_type": report.encounter.source_type if report.encounter else "",
                "workflow_route": (
                    report.encounter.workflow_route if report.encounter else ""
                ),
                "referral_id": referral.referral_id if referral else "",
                "source_hospital_name": (
                    referral.source_hospital.name
                    if referral and referral.source_hospital else ""
                ),
                "has_hospital_recipient": bool(
                    referral and referral.source_hospital
                ),
                "report_status": report.report_status,
                "distribution_status": report.distribution_status,
                "patient_delivery_required": report.patient_delivery_required,
                "issued_at": report.issued_at,
                "hospital_released_at": report.hospital_released_at,
                "pdf_url": request.build_absolute_uri(
                    f"/api/reports/{report.id}/pdf/?report_format=clinician"
                ),
            })

        return Response(data)


class OpsReleaseReportToHospitalView(OpsOnlyMixin, APIView):
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        report = (
            StructuredReport.objects.select_related(
                "patient",
                "patient__assigned_clinic",
                "encounter",
                "encounter__hospital_referral",
            )
            .prefetch_related(
                "hospital_referrals",
                "hospital_referrals__source_hospital",
            )
            .filter(pk=pk)
            .first()
        )

        if not report:
            return Response(
                {"detail": "Report not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if report.report_status != "issued":
            return Response(
                {"detail": "Only clinically issued reports can be released."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        referral = getattr(report.encounter, "hospital_referral", None)
        if not referral:
            referral = report.hospital_referrals.select_related(
                "source_hospital"
            ).first()

        if not referral or not referral.source_hospital:
            return Response(
                {"detail": "This report has no referring hospital."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        referral.report = report
        referral.report_ready = True
        referral.referral_status = "report_issued"
        referral.save(update_fields=[
            "report", "report_ready", "referral_status", "updated_at",
        ])

        now = timezone.now()
        report.distribution_status = "released_to_hospital"
        report.hospital_released_at = now
        report.hospital_released_by = request.user
        report.save(update_fields=[
            "distribution_status",
            "hospital_released_at",
            "hospital_released_by",
            "updated_at",
        ])

        ReportStatusEvent.objects.create(
            report=report,
            event_type="released_to_hospital",
            from_status="issued",
            to_status="issued",
            note=f"Report released by Sentinel to {referral.source_hospital.name}.",
            actor=request.user,
        )

        create_audit_log(
            actor=request.user,
            action="report_released_to_hospital",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            message=(
                f"Report {report.report_id} released to "
                f"{referral.source_hospital.name}."
            ),
            metadata={
                "referral_id": referral.referral_id,
                "hospital_id": referral.source_hospital_id,
                "hospital_name": referral.source_hospital.name,
            },
        )

        create_ops_notification(
            title="Report released to hospital",
            message=(
                f"Report {report.report_id} was released to "
                f"{referral.source_hospital.name}."
            ),
            level="success",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            created_by=request.user,
        )

        return Response({
            "message": "Report released to hospital.",
            "report_id": report.report_id,
            "distribution_status": report.distribution_status,
            "hospital_released_at": report.hospital_released_at,
            "referral_id": referral.referral_id,
            "hospital_name": referral.source_hospital.name,
        })


class OpsMarkPatientDeliveryRequiredView(OpsOnlyMixin, APIView):
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        report = StructuredReport.objects.filter(pk=pk).first()
        if not report:
            return Response(
                {"detail": "Report not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        report.patient_delivery_required = True
        if report.distribution_status == "not_ready":
            report.distribution_status = "awaiting_distribution"
        report.save(update_fields=[
            "patient_delivery_required",
            "distribution_status",
            "updated_at",
        ])

        create_audit_log(
            actor=request.user,
            action="patient_delivery_required",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            message=(
                f"Patient delivery marked as required for "
                f"report {report.report_id}."
            ),
        )

        return Response({
            "message": "Patient delivery marked as required.",
            "report_id": report.report_id,
            "patient_delivery_required": True,
        })


class OpsCreateOrganizationView(OpsOnlyMixin, APIView):
    def post(self, request):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        organization_type = (request.data.get("organization_type") or "").strip()

        if organization_type == "clinic":
            payload = {
                "clinic_id": request.data.get("org_code"),
                "name": request.data.get("name"),
                "contact_email": request.data.get("contact_email", ""),
                "phone": request.data.get("phone", ""),
                "address": request.data.get("address", ""),
                "admin_username": request.data.get("admin_username"),
                "admin_email": request.data.get("admin_email"),
                "admin_first_name": request.data.get("admin_first_name", ""),
                "admin_last_name": request.data.get("admin_last_name", ""),
                "admin_role": "clinic_admin",
                "temporary_password": request.data.get("temporary_password", ""),
                "is_active": True,
            }

            if not payload["clinic_id"] or not payload["name"] or not payload["admin_username"] or not payload["admin_email"]:
                return Response(
                    {"detail": "Clinic code, name, admin username, and admin email are required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            result = provision_clinic_with_admin(payload)

            clinic_org = (
                Organization.objects.filter(
                    id=result.get("organization_id")
                ).first()
                or Organization.objects.filter(
                    clinic_id=payload["clinic_id"]
                ).first()
            )

            if clinic_org:
                profile = get_or_create_capability_profile(clinic_org)

                requested_mode = (
                    request.data.get("workflow_mode")
                    or "sentinel_managed"
                )
                profile.workflow_mode = requested_mode
                profile.referral_requirement = (
                    request.data.get("referral_requirement")
                    or "required"
                )
                profile.patient_ownership = (
                    request.data.get("patient_ownership")
                    or "shared"
                )
                profile.can_create_direct_patients = bool(
                    request.data.get("can_create_direct_patients", False)
                )
                profile.electronic_signature_required = bool(
                    request.data.get(
                        "electronic_signature_required",
                        False,
                    )
                )
                profile.default_payment_responsibility = (
                    request.data.get(
                        "default_payment_responsibility"
                    )
                    or "hospital"
                )
                profile.branding_policy = (
                    request.data.get("branding_policy")
                    or "organization_and_sentinel"
                )
                profile.subscription_tier = (
                    request.data.get("subscription_tier")
                    or "pilot"
                )
                profile.ai_enabled = bool(
                    request.data.get("ai_enabled", True)
                )
                profile.clinic_direct_screening_enabled = bool(
                    request.data.get(
                        "clinic_direct_screening_enabled",
                        False,
                    )
                )

                if requested_mode == "sentinel_managed":
                    profile.can_issue_reports_directly = False
                    profile.sentinel_review_policy = "mandatory"
                elif requested_mode == "clinic_managed":
                    profile.can_issue_reports_directly = True
                    profile.sentinel_review_policy = "unavailable"
                else:
                    profile.can_issue_reports_directly = True
                    profile.sentinel_review_policy = "optional"

                profile.save()

            create_audit_log(
                actor=request.user,
                action="clinic_created",
                entity_type="clinic",
                entity_id=result.get("organization_id"),
                entity_label=payload["clinic_id"],
                message=f"Clinic {payload['name']} created/onboarded.",
                metadata=result,
            )

            create_ops_notification(
                title="Clinic onboarded",
                message=f"Clinic {payload['name']} was created and onboarding email was sent.",
                level="success",
                entity_type="clinic",
                entity_id=result.get("organization_id"),
                entity_label=payload["clinic_id"],
                created_by=request.user,
            )

            return Response({"message": "Clinic created and onboarding email sent.", **result})

        if organization_type == "hospital":
            payload = {
                "hospital_id": request.data.get("org_code"),
                "hospital_name": request.data.get("name"),
                "contact_email": request.data.get("contact_email", ""),
                "phone": request.data.get("phone", ""),
                "address": request.data.get("address", ""),
                "admin_username": request.data.get("admin_username"),
                "admin_email": request.data.get("admin_email"),
                "admin_first_name": request.data.get("admin_first_name", ""),
                "admin_last_name": request.data.get("admin_last_name", ""),
                "admin_role": "hospital_admin",
                "temporary_password": request.data.get("temporary_password", ""),
                "is_active": True,
                "screening_fee_amount": request.data.get("screening_fee_amount", "15000"),
                "hospital_commission_amount": request.data.get("hospital_commission_amount", "0"),
                "currency": request.data.get("currency", "NGN"),
            }

            if not payload["hospital_id"] or not payload["hospital_name"] or not payload["admin_username"] or not payload["admin_email"]:
                return Response(
                    {"detail": "Hospital code, name, admin username, and admin email are required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            result = provision_hospital_with_admin(payload)

            hospital_org = (
                Organization.objects.filter(id=result.get("organization_id")).first()
                or Organization.objects.filter(clinic_id=payload["hospital_id"]).first()
            )

            if hospital_org:
                try:
                    hospital_org.screening_fee_amount = Decimal(str(payload.get("screening_fee_amount", "15000")).replace(",", "").strip())
                    hospital_org.hospital_commission_amount = Decimal(str(payload.get("hospital_commission_amount", "0")).replace(",", "").strip())
                    hospital_org.currency = payload.get("currency") or "NGN"
                    hospital_org.save(
                        update_fields=[
                            "screening_fee_amount",
                            "hospital_commission_amount",
                            "currency",
                        ]
                    )
                except Exception as exc:
                    print("Hospital pricing update failed:", exc)

            create_audit_log(
                actor=request.user,
                action="hospital_created",
                entity_type="hospital",
                entity_id=result.get("organization_id"),
                entity_label=payload["hospital_id"],
                message=f"Hospital {payload['hospital_name']} created/onboarded.",
                metadata=result,
            )

            create_ops_notification(
                title="Hospital onboarded",
                message=f"Hospital {payload['hospital_name']} was created and onboarding email was sent.",
                level="success",
                entity_type="hospital",
                entity_id=result.get("organization_id"),
                entity_label=payload["hospital_id"],
                created_by=request.user,
            )

            return Response({"message": "Hospital created and onboarding email sent.", **result})

        return Response(
            {"detail": "organization_type must be clinic or hospital."},
            status=status.HTTP_400_BAD_REQUEST,
        )


class OpsCreateUserView(OpsOnlyMixin, APIView):
    def post(self, request):
        if not (
            request.user.is_superuser
            or request.user.groups.filter(name="super_admin").exists()
        ):
            return Response(
                {"detail": "Only Sentinel super admins can create Ops users."},
                status=status.HTTP_403_FORBIDDEN,
            )

        username = (request.data.get("username") or "").strip()
        email = (request.data.get("email") or "").strip()
        first_name = (request.data.get("first_name") or "").strip()
        last_name = (request.data.get("last_name") or "").strip()
        temporary_password = request.data.get("temporary_password") or ""

        if not username or not email:
            return Response(
                {"detail": "username and email are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if User.objects.filter(username=username).exists():
            return Response(
                {"detail": "A user with this username already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.create_user(
            username=username,
            email=email,
            password=temporary_password or None,
            first_name=first_name,
            last_name=last_name,
            is_staff=True,
            is_active=True,
        )

        if not temporary_password:
            user.set_unusable_password()
            user.save(update_fields=["password"])

        group, _ = Group.objects.get_or_create(name="ops_admin")
        user.groups.add(group)

        clinic_group, _ = Group.objects.get_or_create(name="clinic_admin")
        user.groups.add(clinic_group)

        sentinel_clinic = Organization.objects.filter(clinic_id="SNT-CLINIC").first()

        if sentinel_clinic:
            from users.models import UserOrganization

            UserOrganization.objects.update_or_create(
                user=user,
                defaults={"organization": sentinel_clinic},
            )

        profile, _ = UserSecurityProfile.objects.get_or_create(user=user)
        profile.must_change_password = True
        profile.save(update_fields=["must_change_password"])

        frontend_base = getattr(settings, "FRONTEND_URL", "").rstrip("/")
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        activation_link = f"{frontend_base}/reset-password?uid={uid}&token={token}"

        email_sent = False

        try:
            send_mail(
                subject="Activate your Sentinel Ops account",
                message=(
                    f"Hello {first_name or username},\n\n"
                    f"Your Sentinel Ops account has been created.\n\n"
                    f"Username: {username}\n\n"
                    f"Please click the activation link below to set your password and access Sentinel Ops:\n\n"
                    f"{activation_link}\n\n"
                    f"If you did not expect this email, please contact Sentinel Ops.\n\n"
                    f"Thank you,\n"
                    f"Sentinel Health"
                ),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[email],
                fail_silently=False,
            )
            email_sent = True
        except Exception as exc:
            return Response(
                {
                    "detail": "Ops user was created, but activation email failed.",
                    "error": str(exc),
                    "username": user.username,
                    "activation_link": activation_link,
                },
                status=status.HTTP_201_CREATED,
            )

        create_audit_log(
            actor=request.user,
            action="ops_user_created",
            entity_type="user",
            entity_id=user.id,
            entity_label=user.username,
            message=f"Ops user {user.username} created.",
            metadata={"email": user.email, "email_sent": email_sent},
        )

        create_ops_notification(
            title="Ops user created",
            message=f"Ops user {user.username} was created.",
            level="success",
            entity_type="user",
            entity_id=user.id,
            entity_label=user.username,
            created_by=request.user,
        )

        return Response(
            {
                "message": "Ops user created and activation email sent.",
                "user": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "role": "ops_admin",
                },
                "activation_link": activation_link,
                "email_sent": email_sent,
            },
            status=status.HTTP_201_CREATED,
        )


class OpsPatientListView(OpsOnlyMixin, APIView):
    def get(self, request):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        search = (request.query_params.get("search") or "").strip()
        clinic = (request.query_params.get("clinic") or "").strip()
        hospital = (request.query_params.get("hospital") or "").strip()
        referral_status = (request.query_params.get("referral_status") or "").strip()
        report_status = (request.query_params.get("report_status") or "").strip()
        payment_status = (request.query_params.get("payment_status") or "").strip()

        patients = Patient.objects.select_related("assigned_clinic").all().order_by("-created_at")

        if search:
            patients = patients.filter(
                models.Q(patient_id__icontains=search)
                | models.Q(first_name__icontains=search)
                | models.Q(last_name__icontains=search)
                | models.Q(phone__icontains=search)
                | models.Q(email__icontains=search)
                | models.Q(referral_id__icontains=search)
                | models.Q(hospital_referrals__referral_id__icontains=search)
                | models.Q(hospital_referrals__source_hospital__name__icontains=search)
                | models.Q(hospital_referrals__source_hospital__clinic_id__icontains=search)
                | models.Q(assigned_clinic__name__icontains=search)
                | models.Q(assigned_clinic__clinic_id__icontains=search)
            )

        if clinic:
            if clinic.isdigit():
                patients = patients.filter(assigned_clinic_id=clinic)
            else:
                patients = patients.filter(
                    models.Q(assigned_clinic__name__icontains=clinic)
                    | models.Q(assigned_clinic__clinic_id__icontains=clinic)
                    | models.Q(hospital_referrals__matched_clinic__name__icontains=clinic)
                    | models.Q(hospital_referrals__matched_clinic__clinic_id__icontains=clinic)
                )

        if hospital:
            if hospital.isdigit():
                patients = patients.filter(hospital_referrals__source_hospital_id=hospital)
            else:
                patients = patients.filter(
                    models.Q(hospital_referrals__source_hospital__name__icontains=hospital)
                    | models.Q(hospital_referrals__source_hospital__clinic_id__icontains=hospital)
                )

        if referral_status:
            patients = patients.filter(hospital_referrals__referral_status=referral_status)

        if report_status:
            if report_status == "not_created":
                patients = patients.filter(reports__isnull=True)
            else:
                patients = patients.filter(reports__report_status=report_status)

        if payment_status:
            if payment_status == "not_created":
                patients = patients.filter(hospital_referrals__ops_payments__isnull=True)
            else:
                patients = patients.filter(hospital_referrals__ops_payments__status=payment_status)

        patients = patients.distinct()
        data = []
        seen_patient_ids = set()

        def image_count_for_patient(patient):
            if not ImageUpload:
                return 0
            try:
                return ImageUpload.objects.filter(patient=patient).count()
            except Exception:
                return ImageUpload.objects.filter(encounter__patient=patient).count()

        for p in patients:
            seen_patient_ids.add(p.id)

            referrals = HospitalReferral.objects.select_related(
                "source_hospital",
                "matched_clinic",
                "report",
            ).filter(patient=p).order_by("-created_at")

            latest_referral = referrals.first()
            payments = OpsPayment.objects.filter(referral__patient=p).order_by("-created_at")
            latest_payment = payments.first()
            reports = StructuredReport.objects.filter(patient=p).select_related("encounter").order_by("-created_at")
            latest_report = reports.first()

            data.append(
                {
                    "id": p.id,
                    "record_type": "patient",
                    "patient_id": p.patient_id,
                    "name": f"{p.first_name} {p.last_name}".strip(),
                    "dob": p.date_of_birth,
                    "sex": p.sex,
                    "phone": p.phone,
                    "email": p.email,
                    "source_hospital": latest_referral.source_hospital.name if latest_referral and latest_referral.source_hospital else "",
                    "source_hospital_code": latest_referral.source_hospital.clinic_id if latest_referral and latest_referral.source_hospital else "",
                    "source_hospital_id": latest_referral.source_hospital_id if latest_referral else None,
                    "assigned_clinic": (
                        latest_referral.matched_clinic.name
                        if latest_referral and latest_referral.matched_clinic
                        else (p.assigned_clinic.name if p.assigned_clinic else "")
                    ),
                    "assigned_clinic_code": (
                        latest_referral.matched_clinic.clinic_id
                        if latest_referral and latest_referral.matched_clinic
                        else (p.assigned_clinic.clinic_id if p.assigned_clinic else "")
                    ),
                    "assigned_clinic_id": (
                        latest_referral.matched_clinic_id
                        if latest_referral and latest_referral.matched_clinic_id
                        else p.assigned_clinic_id
                    ),
                    "referral_status": latest_referral.referral_status if latest_referral else (p.referral_status or ""),
                    "referral_id": latest_referral.referral_id if latest_referral else (p.referral_id or ""),
                    "payment_status": latest_payment.status if latest_payment else "not_created",
                    "report_status": latest_report.report_status if latest_report else "not_created",
                    "latest_report_id": latest_report.report_id if latest_report else "",
                    "latest_report_pk": latest_report.id if latest_report else None,
                    "latest_encounter_id": latest_report.encounter.encounter_id if latest_report and latest_report.encounter else "",
                    "referrals_count": referrals.count(),
                    "payments_count": payments.count(),
                    "reports_count": reports.count(),
                    "images_count": image_count_for_patient(p),
                    "created_at": p.created_at,
                }
            )

        # Include hospital referrals that are not currently linked to a Patient record.
        # This prevents Ops from missing referrals/patients when patient linkage has not happened yet.
        orphan_referrals = HospitalReferral.objects.select_related(
            "source_hospital",
            "matched_clinic",
            "patient",
            "report",
        ).filter(patient__isnull=True)

        if search:
            orphan_referrals = orphan_referrals.filter(
                models.Q(referral_id__icontains=search)
                | models.Q(first_name__icontains=search)
                | models.Q(last_name__icontains=search)
                | models.Q(phone_number__icontains=search)
                | models.Q(email__icontains=search)
                | models.Q(source_hospital__name__icontains=search)
                | models.Q(source_hospital__clinic_id__icontains=search)
                | models.Q(matched_clinic__name__icontains=search)
                | models.Q(matched_clinic__clinic_id__icontains=search)
            )

        if clinic:
            if clinic.isdigit():
                orphan_referrals = orphan_referrals.filter(matched_clinic_id=clinic)
            else:
                orphan_referrals = orphan_referrals.filter(
                    models.Q(matched_clinic__name__icontains=clinic)
                    | models.Q(matched_clinic__clinic_id__icontains=clinic)
                )

        if hospital:
            if hospital.isdigit():
                orphan_referrals = orphan_referrals.filter(source_hospital_id=hospital)
            else:
                orphan_referrals = orphan_referrals.filter(
                    models.Q(source_hospital__name__icontains=hospital)
                    | models.Q(source_hospital__clinic_id__icontains=hospital)
                )

        if referral_status:
            orphan_referrals = orphan_referrals.filter(referral_status=referral_status)

        if report_status:
            if report_status == "not_created":
                orphan_referrals = orphan_referrals.filter(report__isnull=True)
            else:
                orphan_referrals = orphan_referrals.filter(report__report_status=report_status)

        if payment_status:
            if payment_status == "not_created":
                orphan_referrals = orphan_referrals.filter(ops_payments__isnull=True)
            else:
                orphan_referrals = orphan_referrals.filter(ops_payments__status=payment_status)

        for referral in orphan_referrals.distinct().order_by("-created_at"):
            latest_payment = referral.ops_payments.order_by("-created_at").first()
            report = referral.report

            data.append(
                {
                    "id": f"referral-{referral.id}",
                    "record_type": "referral_only",
                    "patient_id": referral.patient_id_text or referral.hospital_mrn or referral.referral_id,
                    "name": f"{referral.first_name} {referral.last_name}".strip(),
                    "dob": referral.dob,
                    "sex": referral.patient_sex,
                    "phone": referral.phone_number,
                    "email": referral.email,
                    "source_hospital": referral.source_hospital.name if referral.source_hospital else "",
                    "source_hospital_code": referral.source_hospital.clinic_id if referral.source_hospital else "",
                    "source_hospital_id": referral.source_hospital_id,
                    "assigned_clinic": referral.matched_clinic.name if referral.matched_clinic else "",
                    "assigned_clinic_code": referral.matched_clinic.clinic_id if referral.matched_clinic else "",
                    "assigned_clinic_id": referral.matched_clinic_id,
                    "referral_status": referral.referral_status,
                    "referral_id": referral.referral_id,
                    "payment_status": latest_payment.status if latest_payment else "not_created",
                    "report_status": report.report_status if report else "not_created",
                    "latest_report_id": report.report_id if report else "",
                    "latest_report_pk": report.id if report else None,
                    "latest_encounter_id": report.encounter.encounter_id if report and report.encounter else "",
                    "referrals_count": 1,
                    "payments_count": referral.ops_payments.count(),
                    "reports_count": 1 if report else 0,
                    "images_count": 0,
                    "created_at": referral.created_at,
                    "cannot_open_patient_detail": True,
                }
            )

        data.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return Response(data)


class OpsPatientDetailView(OpsOnlyMixin, APIView):
    def get(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        patient = Patient.objects.select_related("assigned_clinic").filter(pk=pk).first()
        if not patient:
            return Response({"detail": "Patient not found."}, status=status.HTTP_404_NOT_FOUND)

        referrals = HospitalReferral.objects.select_related("source_hospital", "matched_clinic", "report").filter(patient=patient)
        payments = OpsPayment.objects.select_related("referral").filter(referral__patient=patient)
        reports = StructuredReport.objects.select_related("encounter").filter(patient=patient)

        uploads = []
        if ImageUpload:
            try:
                image_qs = ImageUpload.objects.filter(patient=patient)
            except Exception:
                image_qs = ImageUpload.objects.filter(encounter__patient=patient)

            for img in image_qs.select_related("encounter").order_by("-uploaded_at"):
                image_file = getattr(img, "image_file", None) or getattr(img, "image", None)
                uploads.append(
                    {
                        "id": img.id,
                        "image_upload_id": getattr(img, "image_upload_id", ""),
                        "encounter_id": getattr(getattr(img, "encounter", None), "encounter_id", ""),
                        "encounter_pk": getattr(img, "encounter_id", None),
                        "eye_laterality": getattr(img, "eye_laterality", ""),
                        "image_type": getattr(img, "image_type", ""),
                        "uploaded_at": getattr(img, "uploaded_at", ""),
                        "image_quality": getattr(img, "image_quality", ""),
                        "gradable": getattr(img, "gradable", ""),
                        "retake_required": getattr(img, "retake_required", False),
                        "url": request.build_absolute_uri(image_file.url) if image_file else "",
                    }
                )

        return Response(
            {
                "patient": {
                    "id": patient.id,
                    "patient_id": patient.patient_id,
                    "first_name": patient.first_name,
                    "last_name": patient.last_name,
                    "name": f"{patient.first_name} {patient.last_name}".strip(),
                    "date_of_birth": patient.date_of_birth,
                    "sex": patient.sex,
                    "phone": patient.phone,
                    "email": patient.email,
                    "address": patient.address,
                    "city": patient.city,
                    "state": patient.state,
                    "country": patient.country,
                    "consent_status": patient.consent_status,
                    "assigned_clinic": patient.assigned_clinic.name if patient.assigned_clinic else "",
                    "referral_id": patient.referral_id,
                    "referral_status": patient.referral_status,
                    "appointment_date": patient.appointment_date,
                },
                "referrals": OpsReferralSerializer(referrals, many=True).data,
                "payments": OpsPaymentSerializer(payments, many=True).data,
                "reports": OpsReportSerializer(reports, many=True, context={"request": request}).data,
                "uploads": uploads,
            }
        )


class OpsHospitalListView(OpsOnlyMixin, APIView):
    def get(self, request):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        hospitals = Organization.objects.filter(organization_type="hospital").order_by("name")
        data = []

        for h in hospitals:
            referrals = HospitalReferral.objects.filter(source_hospital=h)
            payments = OpsPayment.objects.filter(referral__source_hospital=h)

            data.append(
                {
                    "id": h.id,
                    "code": h.clinic_id,
                    "name": h.name,
                    "contact_email": h.contact_email,
                    "phone": h.phone,
                    "address": h.address,
                    "screening_fee_amount": h.screening_fee_amount,
                    "hospital_commission_amount": h.hospital_commission_amount,
                    "currency": h.currency,
                    "referrals_count": referrals.count(),
                    "paid_payments": payments.filter(status="paid").count(),
                    "pending_payments": payments.filter(status="pending").count(),
                }
            )

        return Response(data)


class OpsHospitalDetailView(OpsOnlyMixin, APIView):
    def _build_response(self, request, hospital):
        referrals = HospitalReferral.objects.select_related("patient", "matched_clinic", "report").filter(source_hospital=hospital)
        payments = OpsPayment.objects.select_related("referral").filter(referral__source_hospital=hospital)

        return Response(
            {
                "hospital": {
                    "id": hospital.id,
                    "code": hospital.clinic_id,
                    "name": hospital.name,
                    "contact_email": hospital.contact_email,
                    "phone": hospital.phone,
                    "address": hospital.address,
                    "screening_fee_amount": hospital.screening_fee_amount,
                    "hospital_commission_amount": hospital.hospital_commission_amount,
                    "currency": hospital.currency,
                },
                "referrals": OpsReferralSerializer(referrals, many=True).data,
                "payments": OpsPaymentSerializer(payments, many=True).data,
            }
        )

    def get(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        hospital = Organization.objects.filter(pk=pk, organization_type="hospital").first()
        if not hospital:
            return Response({"detail": "Hospital not found."}, status=status.HTTP_404_NOT_FOUND)

        return self._build_response(request, hospital)

    def patch(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        hospital = Organization.objects.filter(pk=pk, organization_type="hospital").first()
        if not hospital:
            return Response({"detail": "Hospital not found."}, status=status.HTTP_404_NOT_FOUND)

        for field in ["name", "contact_email", "phone", "address", "currency"]:
            if field in request.data:
                value = request.data.get(field)
                if field == "currency":
                    value = (value or "NGN").strip().upper()
                setattr(hospital, field, value)

        for field in ["screening_fee_amount", "hospital_commission_amount"]:
            if field in request.data:
                try:
                    value = Decimal(str(request.data.get(field) or "0").replace(",", "").strip())
                except Exception:
                    return Response(
                        {"detail": f"{field.replace('_', ' ').title()} must be a valid number."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                if value < 0:
                    return Response(
                        {"detail": f"{field.replace('_', ' ').title()} cannot be negative."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                setattr(hospital, field, value)

        hospital.save(update_fields=[
            "name",
            "contact_email",
            "phone",
            "address",
            "currency",
            "screening_fee_amount",
            "hospital_commission_amount",
        ])

        create_audit_log(
            actor=request.user,
            action="hospital_pricing_updated",
            entity_type="hospital",
            entity_id=hospital.id,
            entity_label=hospital.clinic_id,
            message=f"Hospital {hospital.name} charge updated.",
            metadata={
                "screening_fee_amount": str(hospital.screening_fee_amount),
                "hospital_commission_amount": str(hospital.hospital_commission_amount),
                "currency": hospital.currency,
            },
        )

        return self._build_response(request, hospital)


class OpsClinicListView(OpsOnlyMixin, APIView):
    def get(self, request):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        clinics = Organization.objects.filter(organization_type="clinic").order_by("name")
        data = []

        for c in clinics:
            referrals = HospitalReferral.objects.filter(matched_clinic=c)
            payments = OpsPayment.objects.filter(referral__matched_clinic=c)
            reports = StructuredReport.objects.filter(patient__assigned_clinic=c)

            data.append(
                {
                    "id": c.id,
                    "code": c.clinic_id,
                    "name": c.name,
                    "contact_email": c.contact_email,
                    "phone": c.phone,
                    "address": c.address,
                    "assigned_referrals": referrals.count(),
                    "reports_count": reports.count(),
                    "paid_payments": payments.filter(status="paid").count(),
                    "capability_profile": capability_profile_data(c),
                }
            )

        return Response(data)


class OpsClinicDetailView(OpsOnlyMixin, APIView):
    def _build_response(self, request, clinic):
        referrals = HospitalReferral.objects.select_related(
            "patient",
            "source_hospital",
            "report",
        ).filter(matched_clinic=clinic)
        patients = Patient.objects.filter(assigned_clinic=clinic)
        payments = OpsPayment.objects.select_related("referral").filter(
            referral__matched_clinic=clinic
        )
        reports = StructuredReport.objects.select_related(
            "patient",
            "encounter",
        ).filter(patient__assigned_clinic=clinic)

        return Response(
            {
                "clinic": {
                    "id": clinic.id,
                    "code": clinic.clinic_id,
                    "name": clinic.name,
                    "contact_email": clinic.contact_email,
                    "phone": clinic.phone,
                    "address": clinic.address,
                    "is_active": clinic.is_active,
                    "capability_profile": capability_profile_data(clinic),
                },
                "patients": [
                    {
                        "id": p.id,
                        "patient_id": p.patient_id,
                        "name": f"{p.first_name} {p.last_name}".strip(),
                        "phone": p.phone,
                        "email": p.email,
                    }
                    for p in patients
                ],
                "referrals": OpsReferralSerializer(
                    referrals,
                    many=True,
                ).data,
                "payments": OpsPaymentSerializer(
                    payments,
                    many=True,
                ).data,
                "reports": OpsReportSerializer(
                    reports,
                    many=True,
                    context={"request": request},
                ).data,
            }
        )

    def get(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        clinic = Organization.objects.filter(
            pk=pk,
            organization_type="clinic",
        ).first()

        if not clinic:
            return Response(
                {"detail": "Clinic not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return self._build_response(request, clinic)

    def patch(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        clinic = Organization.objects.filter(
            pk=pk,
            organization_type="clinic",
        ).first()

        if not clinic:
            return Response(
                {"detail": "Clinic not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        profile = get_or_create_capability_profile(clinic)
        serializer = OpsOrganizationCapabilitySerializer(
            profile,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        create_audit_log(
            actor=request.user,
            action="clinic_capabilities_updated",
            entity_type="clinic",
            entity_id=clinic.id,
            entity_label=clinic.clinic_id,
            message=f"Capability profile updated for {clinic.name}.",
            metadata=serializer.data,
        )

        create_ops_notification(
            title="Clinic capabilities updated",
            message=(
                f"{clinic.name} is configured as "
                f"{serializer.data['workflow_mode'].replace('_', ' ')}."
            ),
            level="info",
            entity_type="clinic",
            entity_id=clinic.id,
            entity_label=clinic.clinic_id,
            created_by=request.user,
        )

        return self._build_response(request, clinic)


class OpsAuditLogListView(OpsOnlyMixin, generics.ListAPIView):
    serializer_class = OpsAuditLogSerializer

    def get_queryset(self):
        if not user_is_ops(self.request.user):
            return OpsAuditLog.objects.none()

        queryset = OpsAuditLog.objects.select_related("actor").all()

        action = self.request.query_params.get("action")
        if action:
            queryset = queryset.filter(action=action)

        entity_type = self.request.query_params.get("entity_type")
        if entity_type:
            queryset = queryset.filter(entity_type=entity_type)

        return queryset.order_by("-created_at")[:300]


class OpsNotificationListView(OpsOnlyMixin, generics.ListAPIView):
    serializer_class = OpsNotificationSerializer

    def get_queryset(self):
        if not user_is_ops(self.request.user):
            return OpsNotification.objects.none()

        queryset = OpsNotification.objects.select_related("created_by").all()

        unread = self.request.query_params.get("unread")
        if unread == "true":
            queryset = queryset.filter(is_read=False)

        return queryset.order_by("-created_at")[:300]


class OpsNotificationMarkReadView(OpsOnlyMixin, APIView):
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        notification = OpsNotification.objects.filter(pk=pk).first()
        if not notification:
            return Response({"detail": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)

        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at"])

        return Response({"message": "Notification marked as read."})


class OpsNotificationMarkAllReadView(OpsOnlyMixin, APIView):
    def post(self, request):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        OpsNotification.objects.filter(is_read=False).update(
            is_read=True,
            read_at=timezone.now(),
        )

        return Response({"message": "All notifications marked as read."})
    
class OpsNotificationDeleteView(OpsOnlyMixin, APIView):
    def delete(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        notification = OpsNotification.objects.filter(pk=pk).first()
        if not notification:
            return Response(
                {"detail": "Notification not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        notification.delete()

        return Response({"message": "Notification deleted."})

class PublicSelfReferralView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        first_name = (request.data.get("first_name") or "").strip()
        last_name = (request.data.get("last_name") or "").strip()
        dob = request.data.get("dob") or None
        patient_sex = (request.data.get("patient_sex") or "").strip()
        phone_number = (request.data.get("phone_number") or "").strip()
        email = (request.data.get("email") or "").strip()
        reason_for_referral = (request.data.get("reason_for_referral") or "").strip()
        diabetes_type = (request.data.get("diabetes_type") or "").strip()
        notes = (request.data.get("notes") or "").strip()

        if not first_name or not last_name or not email or not phone_number:
            return Response(
                {"detail": "First name, last name, email, and phone number are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        referral = HospitalReferral.objects.create(
            source_hospital=None,
            patient=None,
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            patient_sex=patient_sex,
            phone_number=phone_number,
            email=email,
            reason_for_referral=reason_for_referral or "Self-referral from Sentinel website",
            diabetes_type=diabetes_type,
            referral_status="submitted",
            referral_date=timezone.now(),
            source_system="self_referral",
            submitted_by_username="public_self_referral",
            notes=f"Self-referred patient from usesentinelhealth.com.\n{notes}".strip(),
        )

        patient_id = f"PAT-{referral.referral_id}"

        patient = Patient.objects.create(
            patient_id=patient_id,
            first_name=first_name,
            last_name=last_name,
            date_of_birth=dob or "1900-01-01",
            sex=patient_sex or "unknown",
            phone=phone_number,
            email=email,
            country="Nigeria",
            consent_status="pending",
        )

        referral.patient = patient
        referral.save(update_fields=["patient", "updated_at"])

        try:
            send_mail(
                subject="Your Sentinel request has been received",
                message=f"""
Hello {first_name},

Your retinal analysis request has been received.

A Sentinel coordinator will review your referral and contact you with the next steps.

Reference: {referral.referral_id}

Thank you,
Sentinel Health
""".strip(),
                from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
                recipient_list=[email],
                fail_silently=True,
            )
        except Exception:
            pass

        create_audit_log(
            actor=None,
            action="self_referral_created",
            entity_type="referral",
            entity_id=referral.id,
            entity_label=referral.referral_id,
            message=f"Self-referral created for {first_name} {last_name}.",
            metadata={
                "email": email,
                "phone_number": phone_number,
                "patient_id": patient.id,
                "source_system": "self_referral",
            },
        )

        create_ops_notification(
            title="New self-referral",
            message=f"{first_name} {last_name} submitted a self-referral from the Sentinel website.",
            level="info",
            entity_type="referral",
            entity_id=referral.id,
            entity_label=referral.referral_id,
            created_by=None,
        )

        return Response(
            {
                "message": "Self-referral submitted successfully.",
                "referral_id": referral.referral_id,
                "patient_id": patient.patient_id,
            },
            status=status.HTTP_201_CREATED,
        )


class PaystackOpsWebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        raw_body = request.body
        signature = request.headers.get("X-Paystack-Signature", "")
        secret = getattr(settings, "PAYSTACK_SECRET_KEY", "")

        if secret:
            expected_signature = hmac.new(
                secret.encode("utf-8"),
                raw_body,
                hashlib.sha512,
            ).hexdigest()

            if not hmac.compare_digest(signature, expected_signature):
                return Response({"detail": "Invalid Paystack signature."}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception:
            return Response({"detail": "Invalid webhook payload."}, status=status.HTTP_400_BAD_REQUEST)

        reference = payload.get("data", {}).get("reference")

        if payload.get("event") != "charge.success" or not reference:
            return Response({"success": True, "ignored": True})

        payment = OpsPayment.objects.filter(paystack_reference=reference).select_related("referral").first()
        if not payment:
            payment = OpsPayment.objects.filter(payment_id=reference).select_related("referral").first()

        if not payment:
            return Response({"detail": "Matching Sentinel Ops payment not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            verify = verify_transaction(reference)
        except Exception as exc:
            return Response(
                {"detail": "Payment webhook verification failed.", "error": str(exc)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        success, message = mark_payment_as_paid(payment, verify.get("data", {}))
        if not success:
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)

        create_audit_log(
            actor=None,
            action="payment_verified",
            entity_type="payment",
            entity_id=payment.id,
            entity_label=payment.payment_id,
            message=f"Payment {payment.payment_id} verified via Paystack webhook.",
            metadata={"source": "paystack_webhook"},
        )

        create_ops_notification(
            title="Payment received",
            message=f"Payment {payment.payment_id} has been verified via Paystack webhook.",
            level="success",
            entity_type="payment",
            entity_id=payment.id,
            entity_label=payment.payment_id,
            created_by=None,
        )

        return Response({"success": True, "message": message})