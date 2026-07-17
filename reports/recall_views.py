from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import StructuredReport
from .recall_services import calculate_live_recall_status


class RecallQueueView(APIView):
    def get(self, request):
        queryset = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
        ).filter(
            report_status="issued",
            recall_due_date__isnull=False,
        )

        status_filter = (request.query_params.get("status") or "").strip()
        data = []

        for report in queryset.order_by("recall_due_date"):
            live_status = calculate_live_recall_status(report)
            if status_filter and status_filter != "all":
                if live_status != status_filter:
                    continue

            data.append(
                {
                    "id": report.id,
                    "report_id": report.report_id,
                    "patient_id": report.patient.patient_id,
                    "patient_name": (
                        f"{report.patient.first_name} "
                        f"{report.patient.last_name}"
                    ).strip(),
                    "patient_email": report.patient.email,
                    "clinic_name": (
                        report.patient.assigned_clinic.name
                        if report.patient.assigned_clinic
                        else ""
                    ),
                    "recall_months": report.recall_months,
                    "recall_due_date": report.recall_due_date,
                    "recall_status": live_status,
                    "recall_note": report.recall_note,
                }
            )

        return Response(data)


class RecallActionView(APIView):
    def post(self, request, pk):
        report = StructuredReport.objects.select_related(
            "patient",
        ).filter(pk=pk).first()

        if not report:
            return Response(
                {"detail": "Report not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        action = (request.data.get("action") or "").strip()
        note = (request.data.get("note") or "").strip()
        now = timezone.now()

        if action == "contacted":
            report.recall_status = "contacted"
            report.recall_contacted_at = now
        elif action == "booked":
            report.recall_status = "booked"
            report.recall_booked_at = now
        elif action == "completed":
            report.recall_status = "completed"
            report.recall_completed_at = now
        elif action == "deferred":
            report.recall_status = "deferred"
        elif action == "send_email":
            if not report.patient.email:
                return Response(
                    {"detail": "Patient email is missing."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            send_mail(
                subject="Sentinel retinal assessment recall reminder",
                message=(
                    f"Hello {report.patient.first_name},\n\n"
                    f"Your retinal assessment recall is due on "
                    f"{report.recall_due_date}.\n\n"
                    "Please contact your clinic to arrange an appointment.\n\n"
                    "Sentinel Health"
                ),
                from_email=getattr(
                    settings,
                    "DEFAULT_FROM_EMAIL",
                    None,
                ),
                recipient_list=[report.patient.email],
                fail_silently=False,
            )
            report.recall_status = "contacted"
            report.recall_contacted_at = now
        else:
            return Response(
                {
                    "detail": (
                        "action must be contacted, booked, completed, "
                        "deferred or send_email."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        report.recall_note = note
        report.save(
            update_fields=[
                "recall_status",
                "recall_contacted_at",
                "recall_booked_at",
                "recall_completed_at",
                "recall_note",
                "updated_at",
            ]
        )

        return Response(
            {
                "message": "Recall updated.",
                "report_id": report.report_id,
                "recall_status": report.recall_status,
            }
        )
