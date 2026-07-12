from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from patients.models import Patient
from referrals.models import HospitalReferral

from .models import PatientTimelineEvent
from .serializers import PatientTimelineEventSerializer


OPS_ROLES = {"ops_admin", "sentinel_ops", "super_admin"}


def is_ops(user):
    return user.is_superuser or bool(
        OPS_ROLES & set(user.groups.values_list("name", flat=True))
    )


class PatientTimelineView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, patient_id):
        patient = Patient.objects.select_related("assigned_clinic").filter(pk=patient_id).first()
        if not patient:
            return Response({"detail": "Patient not found."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        org = get_user_organization(user)
        portal = (request.query_params.get("portal") or "").strip().lower()

        if is_ops(user):
            allowed_visibilities = ["all", "clinic_ops", "hospital_ops", "ops_only"]
        elif org and org.organization_type == "clinic" and patient.assigned_clinic_id == org.id:
            allowed_visibilities = ["all", "clinic_ops"]
        elif org and org.organization_type == "hospital" and HospitalReferral.objects.filter(
            patient=patient,
            source_hospital=org,
        ).exists():
            allowed_visibilities = ["all", "hospital_ops"]
        else:
            return Response(
                {"detail": "You do not have permission to view this patient timeline."},
                status=status.HTTP_403_FORBIDDEN,
            )

        events = PatientTimelineEvent.objects.select_related(
            "actor", "organization"
        ).filter(
            patient=patient,
            visibility__in=allowed_visibilities,
        )

        category = (request.query_params.get("category") or "").strip()
        if category:
            events = events.filter(category=category)

        return Response(
            {
                "patient_id": patient.patient_id,
                "portal": portal,
                "count": events.count(),
                "events": PatientTimelineEventSerializer(events[:500], many=True).data,
            }
        )
