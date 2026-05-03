from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from users.models import UserOrganization

from patients.models import Patient
from encounters.models import ScreeningEncounter
from uploads.models import ImageUpload
from reports.models import StructuredReport
from consents.models import ConsentRecord


def get_user_clinic(user):
    org = get_user_organization(user)

    if org:
        return org

    user_org = (
        UserOrganization.objects.select_related("organization")
        .filter(user=user)
        .first()
    )

    if user_org:
        return user_org.organization

    return None


def empty_dashboard():
    return {
        "total_patients": 0,
        "total_encounters": 0,
        "total_uploads": 0,
        "total_reports": 0,
        "total_consents": 0,
        "encounters_pending_review": 0,
        "completed_encounters": 0,
    }


class DashboardSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        org = get_user_clinic(user)

        if not org or org.organization_type != "clinic":
            return Response(empty_dashboard())

        patients = Patient.objects.select_related("assigned_clinic").filter(
            assigned_clinic=org
        )

        encounters = ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
        ).filter(patient__assigned_clinic=org)

        uploads = ImageUpload.objects.select_related(
            "encounter",
            "encounter__patient",
            "encounter__patient__assigned_clinic",
        ).filter(encounter__patient__assigned_clinic=org)

        reports = StructuredReport.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
        ).filter(patient__assigned_clinic=org)

        consents = ConsentRecord.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "encounter",
        ).filter(patient__assigned_clinic=org)

        return Response(
            {
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
        )