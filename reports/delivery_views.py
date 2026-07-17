from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization

from .models import PatientReportDelivery, StructuredReport
from .serializers import PatientReportDeliverySerializer


def _user_is_ops(user):
    if user.is_superuser:
        return True
    roles = set(user.groups.values_list("name", flat=True))
    return bool({"ops_admin", "sentinel_ops", "super_admin"} & roles)


class PatientDeliveryListCreateView(APIView):
    def get(self, request):
        queryset = PatientReportDelivery.objects.select_related(
            "report",
            "patient",
            "requested_by",
            "sent_by",
        )

        if not _user_is_ops(request.user):
            org = get_user_organization(request.user)
            if not org:
                raise PermissionDenied("You are not linked to an organization.")
            queryset = queryset.filter(
                report__patient__assigned_clinic=org
            )

        status_filter = (request.query_params.get("status") or "").strip()
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        return Response(
            PatientReportDeliverySerializer(
                queryset,
                many=True,
                context={"request": request},
            ).data
        )

    def post(self, request):
        report_id = request.data.get("report")
        report = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
        ).filter(pk=report_id).first()

        if not report:
            return Response(
                {"detail": "Report not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if report.report_status != "issued":
            return Response(
                {"detail": "Only issued reports can be delivered."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _user_is_ops(request.user):
            org = get_user_organization(request.user)
            if (
                not org
                or report.patient.assigned_clinic_id != org.id
            ):
                raise PermissionDenied(
                    "You cannot deliver this report."
                )

        recipient = (
            request.data.get("recipient")
            or report.patient.email
            or ""
        ).strip()

        if not recipient:
            return Response(
                {"detail": "Patient email address is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not bool(request.data.get("consent_confirmed")):
            return Response(
                {
                    "detail": (
                        "Patient consent confirmation is required "
                        "before delivery."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        delivery = PatientReportDelivery.objects.create(
            report=report,
            patient=report.patient,
            recipient=recipient,
            include_images=bool(
                request.data.get("include_images", False)
            ),
            consent_confirmed=True,
            requested_by=request.user,
        )

        return Response(
            PatientReportDeliverySerializer(delivery).data,
            status=status.HTTP_201_CREATED,
        )


class PatientDeliverySendView(APIView):
    def post(self, request, pk):
        delivery = PatientReportDelivery.objects.select_related(
            "report",
            "patient",
            "report__patient__assigned_clinic",
        ).filter(pk=pk).first()

        if not delivery:
            return Response(
                {"detail": "Delivery record not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not _user_is_ops(request.user):
            org = get_user_organization(request.user)
            if (
                not org
                or delivery.report.patient.assigned_clinic_id != org.id
            ):
                raise PermissionDenied(
                    "You cannot send this report."
                )

        frontend_url = getattr(
            settings,
            "FRONTEND_URL",
            "",
        ).rstrip("/")
        patient_name = (
            f"{delivery.patient.first_name} "
            f"{delivery.patient.last_name}"
        ).strip()

        # Secure production delivery should later use a signed one-time link.
        # For now, the email records the action and directs the patient to
        # contact the clinic if secure portal access is unavailable.
        body = f"""
Hello {patient_name},

Your Sentinel retinal assessment report is ready.

Report reference: {delivery.report.report_id}

Please use the secure link provided by your clinic or contact the clinic if you need assistance accessing your report.

{frontend_url}

Thank you,
Sentinel Health
""".strip()

        try:
            send_mail(
                subject="Your Sentinel retinal assessment report",
                message=body,
                from_email=getattr(
                    settings,
                    "DEFAULT_FROM_EMAIL",
                    None,
                ),
                recipient_list=[delivery.recipient],
                fail_silently=False,
            )
            delivery.status = "sent"
            delivery.sent_by = request.user
            delivery.sent_at = timezone.now()
            delivery.failure_reason = ""
            delivery.save(
                update_fields=[
                    "status",
                    "sent_by",
                    "sent_at",
                    "failure_reason",
                    "updated_at",
                ]
            )

            report = delivery.report
            report.patient_delivery_required = False
            report.patient_delivered_at = delivery.sent_at
            report.save(
                update_fields=[
                    "patient_delivery_required",
                    "patient_delivered_at",
                    "updated_at",
                ]
            )
        except Exception as exc:
            delivery.status = "failed"
            delivery.failure_reason = str(exc)
            delivery.save(
                update_fields=[
                    "status",
                    "failure_reason",
                    "updated_at",
                ]
            )
            return Response(
                {
                    "detail": "Patient report delivery failed.",
                    "error": str(exc),
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            PatientReportDeliverySerializer(delivery).data
        )
