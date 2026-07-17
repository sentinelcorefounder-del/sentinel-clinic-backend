from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.services import record_patient_event
from patients.identity_services import link_patient_to_master
from patients.models import (
    HistoricalRecordAccessRequest,
    MasterPatient,
    Patient,
    PatientIdentityReview,
)
from patients.serializers import (
    HistoricalRecordAccessRequestSerializer,
    MasterPatientSerializer,
    PatientIdentityReviewSerializer,
)

from .views import (
    OpsOnlyMixin,
    create_audit_log,
    create_ops_notification,
)


class OpsMasterPatientListView(OpsOnlyMixin, APIView):
    def get(self, request):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        queryset = MasterPatient.objects.prefetch_related(
            "organization_identities",
            "patient_records",
        ).all()

        search = (request.query_params.get("search") or "").strip()
        if search:
            from django.db import models
            queryset = queryset.filter(
                models.Q(
                    sentinel_patient_id__icontains=search
                )
                | models.Q(first_name__icontains=search)
                | models.Q(last_name__icontains=search)
                | models.Q(primary_phone__icontains=search)
                | models.Q(primary_email__icontains=search)
            )

        serializer = MasterPatientSerializer(
            queryset[:300],
            many=True,
            context={"request": request},
        )
        return Response(serializer.data)


class OpsIdentityReviewListView(OpsOnlyMixin, APIView):
    def get(self, request):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        queryset = PatientIdentityReview.objects.select_related(
            "candidate_patient",
            "possible_master_patient",
            "reviewed_by",
        ).filter(status="open")

        serializer = PatientIdentityReviewSerializer(
            queryset,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data)


class OpsIdentityReviewDecisionView(OpsOnlyMixin, APIView):
    @transaction.atomic
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        review = PatientIdentityReview.objects.select_related(
            "candidate_patient",
            "possible_master_patient",
        ).filter(pk=pk).first()

        if not review:
            return Response(
                {"detail": "Identity review not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        decision = (request.data.get("decision") or "").strip()
        note = (request.data.get("note") or "").strip()

        if decision == "link":
            patient = link_patient_to_master(
                review.candidate_patient,
                review.possible_master_patient,
                reviewed_by=request.user,
                note=note,
            )
            review.status = "linked"
            message = (
                f"{patient.patient_id} linked to "
                f"{review.possible_master_patient.sentinel_patient_id}."
            )
        elif decision == "keep_separate":
            review.status = "kept_separate"
            master = review.candidate_patient.master_patient
            if master and master.identity_status == "possible_duplicate":
                master.identity_status = "active"
                master.save(
                    update_fields=[
                        "identity_status",
                        "updated_at",
                    ]
                )
            message = "Patient records kept separate."
        else:
            return Response(
                {
                    "detail": (
                        "decision must be link or keep_separate."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        review.reviewed_by = request.user
        review.reviewed_at = timezone.now()
        review.decision_note = note
        review.save(
            update_fields=[
                "status",
                "reviewed_by",
                "reviewed_at",
                "decision_note",
            ]
        )

        create_audit_log(
            actor=request.user,
            action="patient_identity_reviewed",
            entity_type="patient_identity_review",
            entity_id=review.id,
            entity_label=(
                review.candidate_patient.patient_id
            ),
            message=message,
            metadata={
                "decision": decision,
                "master_patient": (
                    review.possible_master_patient
                    .sentinel_patient_id
                ),
                "note": note,
            },
        )

        return Response(
            {
                "message": message,
                "review": PatientIdentityReviewSerializer(
                    review
                ).data,
            }
        )


class OpsHistoricalAccessRequestListView(
    OpsOnlyMixin,
    APIView,
):
    def get(self, request):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        queryset = (
            HistoricalRecordAccessRequest.objects.select_related(
                "master_patient",
                "requesting_organization",
                "requested_by",
                "reviewed_by",
            )
        )

        status_filter = (
            request.query_params.get("status") or "pending"
        ).strip()

        if status_filter != "all":
            queryset = queryset.filter(status=status_filter)

        serializer = HistoricalRecordAccessRequestSerializer(
            queryset,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data)


class OpsHistoricalAccessDecisionView(
    OpsOnlyMixin,
    APIView,
):
    @transaction.atomic
    def post(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        access_request = (
            HistoricalRecordAccessRequest.objects.select_related(
                "master_patient",
                "requesting_organization",
            )
            .filter(pk=pk)
            .first()
        )

        if not access_request:
            return Response(
                {"detail": "Access request not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        decision = (request.data.get("decision") or "").strip()
        note = (request.data.get("note") or "").strip()

        if decision == "approve":
            days = int(request.data.get("days") or 30)
            days = min(max(days, 1), 90)
            access_request.status = "approved"
            access_request.expires_at = (
                timezone.now() + timedelta(days=days)
            )
            action = "historical_access_approved"
            level = "success"
        elif decision == "reject":
            access_request.status = "rejected"
            access_request.expires_at = None
            action = "historical_access_rejected"
            level = "warning"
        elif decision == "revoke":
            access_request.status = "revoked"
            access_request.revoked_at = timezone.now()
            action = "historical_access_revoked"
            level = "warning"
        else:
            return Response(
                {
                    "detail": (
                        "decision must be approve, reject or revoke."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        access_request.reviewed_by = request.user
        access_request.reviewed_at = timezone.now()
        access_request.review_note = note
        access_request.save(
            update_fields=[
                "status",
                "expires_at",
                "revoked_at",
                "reviewed_by",
                "reviewed_at",
                "review_note",
                "updated_at",
            ]
        )

        patient = (
            access_request.master_patient
            .patient_records.order_by("-created_at")
            .first()
        )
        if patient:
            record_patient_event(
                patient=patient,
                event_key=(
                    f"historical-access:{access_request.pk}:"
                    f"{access_request.status}"
                ),
                category="consent",
                event_type=action,
                title=action.replace("_", " ").title(),
                description=(
                    f"{access_request.requesting_organization.name}: "
                    f"{access_request.status}."
                ),
                source_type="historical_access_request",
                source_id=access_request.pk,
                actor=request.user,
                organization=(
                    access_request.requesting_organization
                ),
                visibility="clinic_ops",
                metadata={
                    "note": note,
                    "expires_at": (
                        str(access_request.expires_at)
                        if access_request.expires_at
                        else ""
                    ),
                },
            )

        create_audit_log(
            actor=request.user,
            action=action,
            entity_type="historical_access_request",
            entity_id=access_request.id,
            entity_label=(
                access_request.master_patient
                .sentinel_patient_id
            ),
            message=(
                f"Historical access {access_request.status} "
                f"for {access_request.requesting_organization.name}."
            ),
            metadata={
                "note": note,
                "expires_at": (
                    str(access_request.expires_at)
                    if access_request.expires_at
                    else ""
                ),
            },
        )

        create_ops_notification(
            title="Historical access updated",
            message=(
                f"{access_request.requesting_organization.name}: "
                f"{access_request.status}."
            ),
            level=level,
            entity_type="historical_access_request",
            entity_id=access_request.id,
            entity_label=(
                access_request.master_patient
                .sentinel_patient_id
            ),
            created_by=request.user,
        )

        return Response(
            HistoricalRecordAccessRequestSerializer(
                access_request,
                context={"request": request},
            ).data
        )
