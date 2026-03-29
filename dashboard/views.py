from rest_framework.views import APIView
from rest_framework.response import Response

from patients.models import Patient
from encounters.models import ScreeningEncounter
from uploads.models import ImageUpload
from reports.models import StructuredReport
from consents.models import ConsentRecord


class DashboardSummaryView(APIView):
    def get(self, request):
        data = {
            "total_patients": Patient.objects.count(),
            "total_encounters": ScreeningEncounter.objects.count(),
            "total_uploads": ImageUpload.objects.count(),
            "total_reports": StructuredReport.objects.count(),
            "total_consents": ConsentRecord.objects.count(),
            "encounters_pending_review": ScreeningEncounter.objects.filter(
                screening_status__in=["images_uploaded", "under_review"]
            ).count(),
            "completed_encounters": ScreeningEncounter.objects.filter(
                screening_status="completed"
            ).count(),
        }
        return Response(data)