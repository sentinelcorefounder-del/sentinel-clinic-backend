from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from patients.models import Patient
from encounters.models import ScreeningEncounter
from uploads.models import ImageUpload
from reports.models import StructuredReport
from consents.models import ConsentRecord


class DashboardSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user

        patients = Patient.objects.select_related("assigned_clinic").all()
        encounters = ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
        ).all()
        uploads = ImageUpload.objects.select_related(
            "encounter",
            "encounter__patient",
            "encounter__patient__assigned_clinic",
        ).all()
        reports = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
        ).all()
        consents = ConsentRecord.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
        ).all()

        if not user.is_superuser:
            org = get_user_organization(user)
            if not org:
                return Response(
                    {
                        "total_patients": 0,
                        "total_encounters": 0,
                        "total_uploads": 0,
                        "total_reports": 0,
                        "total_consents": 0,
                        "encounters_pending_review": 0,
                        "completed_encounters": 0,
                    }
                )

            patients = patients.filter(assigned_clinic=org)
            encounters = encounters.filter(patient__assigned_clinic=org)
            uploads = uploads.filter(encounter__patient__assigned_clinic=org)
            reports = reports.filter(patient__assigned_clinic=org)
            consents = consents.filter(patient__assigned_clinic=org)

        data = {
            "total_patients": patients.count(),
            "total_encounters": encounters.count(),
            "total_uploads": uploads.count(),
            "total_reports": reports.count(),
            "total_consents": consents.count(),
            "encounters_pending_review": encounters.filter(
                screening_status__in=["images_uploaded", "under_review"]
            ).count(),
            "completed_encounters": encounters.filter(
                screening_status="completed"
            ).count(),
        }
        return Response(data)