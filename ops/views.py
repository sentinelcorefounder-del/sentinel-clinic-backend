import hashlib
import hmac
import json
from decimal import Decimal

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

from organizations.models import Organization
from organizations.services.provisioning import (
    provision_clinic_with_admin,
    provision_hospital_with_admin,
)
from patients.models import Patient
from payments.services.paystack import initialize_transaction, verify_transaction
from referrals.models import HospitalReferral
from reports.models import StructuredReport
from users.models import UserSecurityProfile

from .models import OpsAuditLog, OpsNotification, OpsPayment
from .serializers import (
    OpsAuditLogSerializer,
    OpsNotificationSerializer,
    OpsPaymentSerializer,
    OpsReferralSerializer,
    OpsReportSerializer,
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

        try:
            amount = Decimal(str(request.data.get("amount", "15000")).replace(",", "").strip())
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
                "currency": "NGN",
                "status": "draft",
            },
        )

        if not created:
            payment.patient_email = patient_email
            payment.amount = amount
            payment.save(update_fields=["patient_email", "amount", "updated_at"])

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

        return StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
            "submitted_to_ops_by",
            "ops_reviewed_by",
        ).filter(report_status="submitted_to_ops").order_by("-submitted_to_ops_at", "-created_at")


class OpsReportApproveView(OpsOnlyMixin, APIView):
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        report = StructuredReport.objects.filter(pk=pk).first()
        if not report:
            return Response({"detail": "Report not found."}, status=status.HTTP_404_NOT_FOUND)

        if report.report_status != "submitted_to_ops":
            return Response(
                {"detail": f"Only submitted_to_ops reports can be approved. Current status: {report.report_status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        report.report_status = "ops_approved"
        report.ops_reviewed_at = timezone.now()
        report.ops_reviewed_by = request.user
        report.ops_review_note = (request.data.get("note") or "").strip()
        report.save(update_fields=["report_status", "ops_reviewed_at", "ops_reviewed_by", "ops_review_note", "updated_at"])

        create_audit_log(
            actor=request.user,
            action="report_approved",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            message=f"Report {report.report_id} approved by Ops.",
        )

        create_ops_notification(
            title="Report approved",
            message=f"Report {report.report_id} was approved by Ops.",
            level="success",
            entity_type="report",
            entity_id=report.id,
            entity_label=report.report_id,
            created_by=request.user,
        )

        return Response({"message": "Report approved by Ops.", "report": OpsReportSerializer(report).data})


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

        report.report_status = "ops_rejected"
        report.ops_reviewed_at = timezone.now()
        report.ops_reviewed_by = request.user
        report.ops_review_note = (request.data.get("note") or "").strip()
        report.save(update_fields=["report_status", "ops_reviewed_at", "ops_reviewed_by", "ops_review_note", "updated_at"])

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
            }

            if not payload["hospital_id"] or not payload["hospital_name"] or not payload["admin_username"] or not payload["admin_email"]:
                return Response(
                    {"detail": "Hospital code, name, admin username, and admin email are required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            result = provision_hospital_with_admin(payload)

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
        if not request.user.is_superuser:
            return Response(
                {"detail": "Only super admin can create Ops users."},
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

        patients = Patient.objects.select_related("assigned_clinic").order_by("-created_at")
        data = []

        for p in patients:
            referrals = HospitalReferral.objects.filter(patient=p)
            payments = OpsPayment.objects.filter(referral__patient=p)

            data.append(
                {
                    "id": p.id,
                    "patient_id": p.patient_id,
                    "name": f"{p.first_name} {p.last_name}".strip(),
                    "dob": p.date_of_birth,
                    "sex": p.sex,
                    "phone": p.phone,
                    "email": p.email,
                    "assigned_clinic": p.assigned_clinic.name if p.assigned_clinic else "",
                    "referrals_count": referrals.count(),
                    "payments_count": payments.count(),
                    "created_at": p.created_at,
                }
            )

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

            for img in image_qs:
                image_file = getattr(img, "image", None)
                uploads.append(
                    {
                        "id": img.id,
                        "encounter_id": getattr(getattr(img, "encounter", None), "encounter_id", ""),
                        "eye_laterality": getattr(img, "eye_laterality", ""),
                        "image_type": getattr(img, "image_type", ""),
                        "uploaded_at": getattr(img, "uploaded_at", ""),
                        "image_quality": getattr(img, "image_quality", ""),
                        "gradable": getattr(img, "gradable", ""),
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
                "reports": OpsReportSerializer(reports, many=True).data,
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
                    "referrals_count": referrals.count(),
                    "paid_payments": payments.filter(status="paid").count(),
                    "pending_payments": payments.filter(status="pending").count(),
                }
            )

        return Response(data)


class OpsHospitalDetailView(OpsOnlyMixin, APIView):
    def get(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        hospital = Organization.objects.filter(pk=pk, organization_type="hospital").first()
        if not hospital:
            return Response({"detail": "Hospital not found."}, status=status.HTTP_404_NOT_FOUND)

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
                },
                "referrals": OpsReferralSerializer(referrals, many=True).data,
                "payments": OpsPaymentSerializer(payments, many=True).data,
            }
        )


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
                }
            )

        return Response(data)


class OpsClinicDetailView(OpsOnlyMixin, APIView):
    def get(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        clinic = Organization.objects.filter(pk=pk, organization_type="clinic").first()
        if not clinic:
            return Response({"detail": "Clinic not found."}, status=status.HTTP_404_NOT_FOUND)

        referrals = HospitalReferral.objects.select_related("patient", "source_hospital", "report").filter(matched_clinic=clinic)
        patients = Patient.objects.filter(assigned_clinic=clinic)
        payments = OpsPayment.objects.select_related("referral").filter(referral__matched_clinic=clinic)
        reports = StructuredReport.objects.select_related("patient", "encounter").filter(patient__assigned_clinic=clinic)

        return Response(
            {
                "clinic": {
                    "id": clinic.id,
                    "code": clinic.clinic_id,
                    "name": clinic.name,
                    "contact_email": clinic.contact_email,
                    "phone": clinic.phone,
                    "address": clinic.address,
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
                "referrals": OpsReferralSerializer(referrals, many=True).data,
                "payments": OpsPaymentSerializer(payments, many=True).data,
                "reports": OpsReportSerializer(reports, many=True).data,
            }
        )


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
            first_name=first_name,
            last_name=last_name,
            dob=dob,
            patient_sex=patient_sex,
            phone_number=phone_number,
            email=email,
            reason_for_referral=reason_for_referral or "Self-referral from Sentinel website",
            diabetes_type=diabetes_type,
            referral_status="submitted",
            notes=f"Self-referred patient from usesentinelhealth.com.\n{notes}".strip(),
        )

        create_audit_log(
            actor=None,
            action="self_referral_created",
            entity_type="referral",
            entity_id=referral.id,
            entity_label=referral.referral_id,
            message=f"Self-referral created for {first_name} {last_name}.",
            metadata={"email": email, "phone_number": phone_number},
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