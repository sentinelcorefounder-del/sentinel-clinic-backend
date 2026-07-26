from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
import uuid

from audit.services import record_patient_event
from common.tenant import get_user_organization
from organizations.models import OrganizationProfile
from referrals.models import HospitalReferral
from users.models import UserOrganization
from finance.models import (
    OrganizationWallet,
    PartnerContract,
    PricingRule,
    WalletLedgerEntry,
)
from consents.models import ConsentRecord

from .models import (
    OcularAIReview,
    OcularDiagnosticAssessment,
    OcularInvestigation,
    ScreeningEncounter,
)
from .ocular_ai_service import run_ocular_ai_review
from .serializers import (
    OcularAIReviewSerializer,
    OcularDiagnosticAssessmentSerializer,
    OcularInvestigationSerializer,
    ScreeningEncounterSerializer,
)


CLOSED_REFERRAL_STATUSES = {"completed", "cancelled"}


def latest_valid_consent(encounter, consent_type):
    today = timezone.localdate()
    consent = (
        ConsentRecord.objects.filter(
            patient=encounter.patient,
            consent_type=consent_type,
        )
        .filter(Q(encounter=encounter) | Q(encounter__isnull=True))
        .order_by("-consent_date", "-created_at")
        .first()
    )
    if (
        not consent
        or consent.consent_status != "granted"
        or consent.withdrawal_date
        or (consent.expiry_date and consent.expiry_date < today)
    ):
        return None
    return consent


def ocular_ai_price_for(clinic, encounter):
    today = timezone.localdate()
    rule = (
        PricingRule.objects.select_related("contract")
        .filter(
            contract__organization=clinic,
            contract__status=PartnerContract.Status.ACTIVE,
            contract__currency="NGN",
            contract__effective_from__lte=today,
            service_type="ocular_ai_review",
            is_active=True,
            effective_from__lte=today,
        )
        .filter(
            Q(contract__effective_to__isnull=True)
            | Q(contract__effective_to__gte=today)
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today))
        .filter(
            Q(contract__programme=encounter.programme)
            | Q(contract__programme="ocular_ai_review")
            | Q(contract__programme="all")
        )
        .order_by("priority", "-effective_from", "-version", "-id")
        .first()
    )
    if rule:
        return rule.gross_amount.quantize(Decimal("0.01")), rule
    fallback = Decimal(
        str(getattr(settings, "OCULAR_AI_REVIEW_PRICE_NGN", "4000.00"))
    ).quantize(Decimal("0.01"))
    return fallback, None


def clinic_has_free_ocular_ai_review(clinic):
    return not OcularAIReview.objects.filter(
        encounter__patient__assigned_clinic=clinic,
        payment_status__in=["pending", "free"],
    ).exists()


def get_user_clinic(user):
    org = get_user_organization(user)
    if org:
        return org

    user_org = (
        UserOrganization.objects.select_related("organization")
        .filter(user=user)
        .first()
    )
    return user_org.organization if user_org else None


def active_referrals_for_patient(patient, clinic):
    return (
        HospitalReferral.objects.select_related(
            "source_hospital",
            "matched_clinic",
            "patient",
        )
        .filter(patient=patient, matched_clinic=clinic)
        .exclude(referral_status__in=CLOSED_REFERRAL_STATUSES)
        .order_by("-referral_date", "-created_at", "-id")
    )


def user_can_override_source(user):
    if user.is_superuser:
        return True
    roles = set(user.groups.values_list("name", flat=True))
    return bool({"clinic_admin", "reviewer", "optometrist"} & roles)


class PatientActiveReferralListView(APIView):
    def get(self, request, patient_id):
        org = get_user_clinic(request.user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")

        from patients.models import Patient

        patient = Patient.objects.filter(
            pk=patient_id,
            assigned_clinic=org,
        ).first()
        if not patient:
            return Response(
                {"detail": "Patient not found in this clinic."},
                status=status.HTTP_404_NOT_FOUND,
            )

        referrals = active_referrals_for_patient(patient, org)
        return Response({
            "patient_id": patient.id,
            "sentinel_patient_id": patient.patient_id,
            "active_referrals": [
                {
                    "id": referral.id,
                    "referral_id": referral.referral_id,
                    "referral_status": referral.referral_status,
                    "referral_date": referral.referral_date,
                    "reason_for_referral": referral.reason_for_referral or "",
                    "hospital_mrn": referral.hospital_mrn or "",
                    "source_hospital_id": referral.source_hospital_id,
                    "source_hospital_name": (
                        referral.source_hospital.name
                        if referral.source_hospital
                        else ""
                    ),
                }
                for referral in referrals
            ],
            "clinic_direct_override_allowed": user_can_override_source(
                request.user
            ),
        })


class ScreeningEncounterListCreateView(generics.ListCreateAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            return ScreeningEncounter.objects.none()

        queryset = ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "hospital_referral",
            "hospital_referral__source_hospital",
        ).filter(patient__assigned_clinic=org)

        search = self.request.query_params.get("search")
        status_value = self.request.query_params.get("status")
        encounter_date = self.request.query_params.get("date")

        if search:
            queryset = queryset.filter(
                Q(encounter_id__icontains=search)
                | Q(patient__patient_id__icontains=search)
                | Q(patient__first_name__icontains=search)
                | Q(patient__last_name__icontains=search)
                | Q(hospital_referral__referral_id__icontains=search)
                | Q(hospital_referral__source_hospital__name__icontains=search)
            )
        if status_value:
            queryset = queryset.filter(screening_status=status_value)
        if encounter_date:
            queryset = queryset.filter(encounter_date=encounter_date)

        return queryset.order_by("-created_at")

    @transaction.atomic
    def perform_create(self, serializer):
        user = self.request.user
        org = get_user_clinic(user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")

        patient = serializer.validated_data.get("patient")
        if not patient:
            raise PermissionDenied("A patient is required.")
        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied(
                "You cannot create encounters outside your clinic."
            )

        profile, _ = OrganizationProfile.objects.get_or_create(
            organization=org
        )
        requested_source = (
            serializer.validated_data.get("source_type") or ""
        ).strip()
        requested_programme = (
            serializer.validated_data.get("programme") or "diabetic_screening"
        ).strip()
        valid_programmes = {
            "diabetic_screening",
            "ocular_diagnostics",
            "combined_assessment",
        }
        if requested_programme not in valid_programmes:
            raise PermissionDenied("Choose a valid assessment type.")
        if (
            requested_programme in {"ocular_diagnostics", "combined_assessment"}
            and not profile.ocular_diagnostics_enabled
        ):
            raise PermissionDenied(
                "Ocular diagnostics is not enabled for this clinic."
            )
        if (
            requested_programme == "ocular_diagnostics"
            and requested_source != "clinic_direct"
        ):
            raise PermissionDenied(
                "A general ocular assessment must be a clinic-direct episode."
            )
        selected_referral = serializer.validated_data.get(
            "hospital_referral"
        )
        override_reason = (
            serializer.validated_data.get("source_override_reason") or ""
        ).strip()
        active_referrals = list(
            active_referrals_for_patient(patient, org)
        )

        if requested_source == "hospital_referral":
            referral = selected_referral
            if referral is None:
                if len(active_referrals) == 1:
                    referral = active_referrals[0]
                elif len(active_referrals) > 1:
                    raise PermissionDenied(
                        "This patient has more than one active hospital "
                        "referral. Select the exact referral."
                    )
                else:
                    raise PermissionDenied(
                        "No active hospital referral is available for "
                        "this patient and clinic."
                    )

            if referral.patient_id != patient.id:
                raise PermissionDenied(
                    "The selected referral belongs to a different patient."
                )
            if referral.matched_clinic_id != org.id:
                raise PermissionDenied(
                    "The selected referral is not assigned to this clinic."
                )
            if referral.referral_status in CLOSED_REFERRAL_STATUSES:
                raise PermissionDenied(
                    "The selected referral is closed or cancelled."
                )

            values = {
                "originating_organization": org,
                "programme": requested_programme,
                "encounter_type": (
                    "combined_assessment"
                    if requested_programme == "combined_assessment"
                    else "retinal_assessment"
                ),
                "source_type": "hospital_referral",
                "workflow_route": "sentinel_managed",
                "payment_responsibility": "hospital",
                "hospital_referral": referral,
                "source_override_reason": "",
                "source_overridden_by": None,
                "source_overridden_at": None,
            }

        elif requested_source == "clinic_direct":
            if (
                requested_programme != "ocular_diagnostics"
                and not profile.clinic_direct_screening_enabled
            ):
                raise PermissionDenied(
                    "Clinic-direct diabetic retinal assessment is not "
                    "enabled for this clinic."
                )

            if active_referrals and requested_programme != "ocular_diagnostics":
                if not override_reason:
                    raise PermissionDenied(
                        "This patient has an active hospital referral. "
                        "Continue under it or provide a reason for a "
                        "separate clinic-direct episode."
                    )
                if not user_can_override_source(user):
                    raise PermissionDenied(
                        "Only an authorised clinic clinician or "
                        "administrator can override an active referral."
                    )

            requested_route = (
                serializer.validated_data.get("workflow_route") or ""
            )
            if requested_programme == "ocular_diagnostics":
                route = "clinic_managed"
            elif profile.workflow_mode == "sentinel_managed":
                route = "sentinel_managed"
            elif profile.workflow_mode == "clinic_managed":
                route = "clinic_managed"
            elif requested_route in {
                "clinic_managed",
                "sentinel_managed",
            }:
                route = requested_route
            else:
                raise PermissionDenied(
                    "Hybrid clinics must select a workflow."
                )

            values = {
                "originating_organization": org,
                "programme": requested_programme,
                "encounter_type": (
                    "ocular_assessment"
                    if requested_programme == "ocular_diagnostics"
                    else "combined_assessment"
                    if requested_programme == "combined_assessment"
                    else "retinal_assessment"
                ),
                "source_type": "clinic_direct",
                "workflow_route": route,
                "payment_responsibility": (
                    serializer.validated_data.get(
                        "payment_responsibility"
                    )
                    or profile.default_payment_responsibility
                ),
                "hospital_referral": None,
                "source_override_reason": (
                    override_reason
                    if active_referrals
                    and requested_programme != "ocular_diagnostics"
                    else ""
                ),
                "source_overridden_by": (
                    user
                    if active_referrals
                    and requested_programme != "ocular_diagnostics"
                    else None
                ),
                "source_overridden_at": (
                    timezone.now()
                    if active_referrals
                    and requested_programme != "ocular_diagnostics"
                    else None
                ),
            }
        else:
            raise PermissionDenied(
                "Choose either a hospital referral or a clinic-direct "
                "assessment pathway."
            )

        encounter = serializer.save(**values)
        if encounter.includes_ocular_diagnostics:
            OcularDiagnosticAssessment.objects.get_or_create(
                encounter=encounter
            )

        if (
            active_referrals
            and requested_source == "clinic_direct"
            and requested_programme != "ocular_diagnostics"
        ):
            record_patient_event(
                patient=patient,
                event_key=f"encounter:{encounter.pk}:source_override",
                category="encounter",
                event_type="encounter_source_overridden",
                title="Active referral bypassed",
                description=(
                    "An authorised clinic user created a separate "
                    "clinic-direct episode despite an active referral."
                ),
                source_type="encounter",
                source_id=encounter.pk,
                encounter_id=encounter.encounter_id,
                actor=user,
                organization=org,
                visibility="clinic_ops",
                metadata={
                    "override_reason": override_reason,
                    "active_referral_ids": [
                        referral.referral_id
                        for referral in active_referrals
                    ],
                },
                occurred_at=encounter.source_overridden_at,
            )

        record_patient_event(
            patient=patient,
            event_key=f"encounter:{encounter.pk}:created",
            category="encounter",
            event_type="screening_encounter_created",
            title=f"{encounter.get_programme_display()} created",
            description=(
                f"{encounter.get_source_type_display()} encounter "
                f"{encounter.encounter_id} was created."
            ),
            source_type="encounter",
            source_id=encounter.pk,
            encounter_id=encounter.encounter_id,
            referral_id=(
                encounter.hospital_referral.referral_id
                if encounter.hospital_referral
                else ""
            ),
            actor=user,
            organization=org,
            visibility="all" if encounter.hospital_referral else "clinic_ops",
            metadata={
                "source_type": encounter.source_type,
                "workflow_route": encounter.workflow_route,
                "payment_responsibility": encounter.payment_responsibility,
                "source_override_reason": encounter.source_override_reason,
                "programme": encounter.programme,
            },
            occurred_at=encounter.created_at,
        )


class ScreeningEncounterDetailView(
    generics.RetrieveUpdateDestroyAPIView
):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            return ScreeningEncounter.objects.none()

        return ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "hospital_referral",
            "hospital_referral__source_hospital",
        ).filter(patient__assigned_clinic=org)

    def perform_update(self, serializer):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")

        patient = serializer.validated_data.get(
            "patient", serializer.instance.patient
        )
        if patient.assigned_clinic_id != org.id:
            raise PermissionDenied(
                "You cannot update encounters outside your clinic."
            )
        serializer.save()


class PatientEncounterListView(generics.ListAPIView):
    serializer_class = ScreeningEncounterSerializer

    def get_queryset(self):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            return ScreeningEncounter.objects.none()

        return ScreeningEncounter.objects.select_related(
            "patient",
            "patient__assigned_clinic",
            "hospital_referral",
            "hospital_referral__source_hospital",
        ).filter(
            patient_id=self.kwargs["patient_id"],
            patient__assigned_clinic=org,
        ).order_by("-created_at")


class OcularDiagnosticAssessmentDetailView(
    generics.RetrieveUpdateAPIView
):
    serializer_class = OcularDiagnosticAssessmentSerializer
    lookup_field = "encounter_id"

    def get_queryset(self):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            return OcularDiagnosticAssessment.objects.none()
        return OcularDiagnosticAssessment.objects.select_related(
            "encounter", "encounter__patient", "completed_by"
        ).filter(encounter__patient__assigned_clinic=org)

    def perform_update(self, serializer):
        assessment = serializer.instance
        if not assessment.encounter.includes_ocular_diagnostics:
            raise PermissionDenied(
                "This encounter does not include ocular diagnostics."
            )
        complete = bool(self.request.data.get("mark_complete", False))
        had_ai_review = assessment.encounter.ocular_ai_reviews.exists()
        previous_impression = assessment.impression
        previous_management = assessment.management_plan
        serializer.save(
            completed_at=timezone.now() if complete else assessment.completed_at,
            completed_by=self.request.user if complete else assessment.completed_by,
        )
        assessment.refresh_from_db()
        if had_ai_review and (
            assessment.impression != previous_impression
            or assessment.management_plan != previous_management
        ):
            record_patient_event(
                patient=assessment.encounter.patient,
                event_key=(
                    f"encounter:{assessment.encounter.pk}:ocular-amendment:"
                    f"{timezone.now().timestamp()}"
                ),
                category="encounter",
                event_type="ocular_assessment_amended_after_ai_review",
                title="Ocular assessment amended after AI review",
                description=(
                    "The clinician amended the clinical impression or management "
                    "plan after the one-time AI review."
                ),
                source_type="encounter",
                source_id=assessment.encounter.pk,
                encounter_id=assessment.encounter.encounter_id,
                actor=self.request.user,
                organization=get_user_clinic(self.request.user),
                visibility="clinic_ops",
                metadata={
                    "previous_impression": previous_impression,
                    "previous_management_plan": previous_management,
                    "new_impression": assessment.impression,
                    "new_management_plan": assessment.management_plan,
                },
            )
        if complete:
            assessment.encounter.update_status_from_related_records()


class OcularInvestigationListCreateView(generics.ListCreateAPIView):
    serializer_class = OcularInvestigationSerializer
    parser_classes = [MultiPartParser, FormParser]

    def _encounter(self):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")
        encounter = ScreeningEncounter.objects.filter(
            pk=self.kwargs["encounter_id"],
            patient__assigned_clinic=org,
        ).first()
        if not encounter or not encounter.includes_ocular_diagnostics:
            raise PermissionDenied(
                "This encounter does not include ocular diagnostics."
            )
        return encounter

    def get_queryset(self):
        return OcularInvestigation.objects.filter(
            encounter=self._encounter()
        ).select_related("uploaded_by")

    def perform_create(self, serializer):
        encounter = self._encounter()
        uploaded_file = serializer.validated_data["file"]
        serializer.save(
            encounter=encounter,
            investigation_id=f"INV-{uuid.uuid4().hex[:10].upper()}",
            original_filename=uploaded_file.name,
            uploaded_by=self.request.user,
        )


class OcularInvestigationDetailView(generics.RetrieveDestroyAPIView):
    serializer_class = OcularInvestigationSerializer

    def get_queryset(self):
        org = get_user_clinic(self.request.user)
        if not org or org.organization_type != "clinic":
            return OcularInvestigation.objects.none()
        return OcularInvestigation.objects.filter(
            encounter__patient__assigned_clinic=org,
            encounter__programme__in=[
                "ocular_diagnostics", "combined_assessment"
            ],
        )

    def perform_destroy(self, instance):
        if instance.encounter.ocular_ai_reviews.exists():
            raise PermissionDenied(
                "This investigation is part of an AI review audit record "
                "and cannot be deleted."
            )
        instance.delete()


class OcularAIReviewListCreateView(APIView):
    def _encounter(self, request, encounter_id):
        org = get_user_clinic(request.user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")
        encounter = ScreeningEncounter.objects.select_related(
            "patient", "ocular_assessment"
        ).filter(
            pk=encounter_id,
            patient__assigned_clinic=org,
        ).first()
        if not encounter or not encounter.includes_ocular_diagnostics:
            raise PermissionDenied(
                "This encounter does not include ocular diagnostics."
            )
        return encounter

    def get(self, request, encounter_id):
        encounter = self._encounter(request, encounter_id)
        clinic = get_user_clinic(request.user)
        reviews = encounter.ocular_ai_reviews.select_related(
            "requested_by", "decided_by"
        )
        amount, pricing_rule = ocular_ai_price_for(clinic, encounter)
        free_available = clinic_has_free_ocular_ai_review(clinic)
        clinical_ai_consent = latest_valid_consent(
            encounter, "ai_clinical_review"
        )
        training_consent = latest_valid_consent(encounter, "ai_training")
        return Response({
            "reviews": OcularAIReviewSerializer(reviews, many=True).data,
            "pricing": {
                "amount": str(amount),
                "currency": "NGN",
                "one_review_per_encounter": True,
                "free_review_available": free_available,
                "amount_due": "0.00" if free_available else str(amount),
                "pricing_source": "contract" if pricing_rule else "default",
            },
            "consent": {
                "clinical_ai_review_granted": bool(clinical_ai_consent),
                "ai_training_granted": bool(training_consent),
                "ai_training_optional": True,
            },
        })

    def post(self, request, encounter_id):
        encounter = self._encounter(request, encounter_id)
        assessment = encounter.ocular_assessment
        if not assessment.completed_at:
            return Response(
                {
                    "detail": (
                        "Complete and lock the optometrist's clinical "
                        "impression and management plan before AI review."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        investigations = list(encounter.ocular_investigations.all())
        fundus_images = list(encounter.image_uploads.filter(image_type="fundus"))
        if not investigations and not fundus_images:
            return Response(
                {
                    "detail": (
                        "Upload at least one fundus photograph or additional "
                        "ocular investigation first."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        clinical_ai_consent = latest_valid_consent(
            encounter, "ai_clinical_review"
        )
        training_consent = latest_valid_consent(encounter, "ai_training")
        if not clinical_ai_consent:
            return Response(
                {
                    "detail": (
                        "A current, granted AI Clinical Review consent is "
                        "required before any charge or external processing."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if request.data.get("privacy_confirmed") is not True:
            return Response(
                {
                    "detail": (
                        "Confirm that every selected file has been checked and "
                        "contains no visible patient name, date of birth, patient "
                        "number, contact detail, or other identifying label."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        clinic = get_user_clinic(request.user)
        try:
            with transaction.atomic():
                clinic = type(clinic).objects.select_for_update().get(pk=clinic.pk)
                locked_encounter = ScreeningEncounter.objects.select_for_update().get(
                    pk=encounter.pk
                )
                existing = locked_encounter.ocular_ai_reviews.first()
                if existing:
                    return Response(
                        {
                            "detail": (
                                "This encounter has already used its one-time "
                                "Sentinel AI Clinical Review."
                            ),
                            "review": OcularAIReviewSerializer(existing).data,
                        },
                        status=status.HTTP_409_CONFLICT,
                    )
                contracted_fee, pricing_rule = ocular_ai_price_for(
                    clinic, locked_encounter
                )
                is_free = clinic_has_free_ocular_ai_review(clinic)
                fee = Decimal("0.00") if is_free else contracted_fee
                review_id = f"OAI-{uuid.uuid4().hex[:10].upper()}"
                deidentified_reference = f"REV-{uuid.uuid4().hex.upper()}"
                charge = None
                if not is_free:
                    wallet = OrganizationWallet.objects.select_for_update().filter(
                        organization=clinic,
                        currency="NGN",
                        is_active=True,
                    ).first()
                    if not wallet:
                        return Response(
                            {"detail": "This clinic has no active NGN wallet."},
                            status=status.HTTP_402_PAYMENT_REQUIRED,
                        )
                    if wallet.spendable_balance < fee:
                        return Response(
                            {
                                "detail": (
                                    "Insufficient clinic wallet balance. Fund the "
                                    "wallet through the existing Paystack flow, then retry."
                                ),
                                "required_amount": str(fee),
                                "currency": "NGN",
                                "available_amount": str(wallet.spendable_balance),
                            },
                            status=status.HTTP_402_PAYMENT_REQUIRED,
                        )
                    charge = WalletLedgerEntry.objects.create(
                        wallet=wallet,
                        entry_type=WalletLedgerEntry.EntryType.ADJUSTMENT,
                        available_delta=-fee,
                        reserved_delta=Decimal("0.00"),
                        currency="NGN",
                        idempotency_key=f"ocular-ai-review:{encounter.pk}:charge",
                        reference=review_id,
                        description="Sentinel AI Clinical Review",
                        metadata={
                            "service_type": "ocular_ai_review",
                            "encounter_id": encounter.encounter_id,
                            "one_review_per_encounter": True,
                            "pricing_rule_id": pricing_rule.pk if pricing_rule else None,
                        },
                        actor=request.user,
                    )
                review = OcularAIReview.objects.create(
                    review_id=review_id,
                    encounter=locked_encounter,
                    requested_by=request.user,
                    clinician_impression_snapshot=assessment.impression,
                    clinician_management_snapshot=assessment.management_plan,
                    provider="openai",
                    fee_amount=fee,
                    fee_currency="NGN",
                    payment_status="free" if is_free else "charged",
                    pricing_rule=pricing_rule,
                    charge_ledger_entry=charge,
                    clinical_ai_consent=clinical_ai_consent,
                    training_consent=training_consent,
                    consent_checked_at=timezone.now(),
                    privacy_verified_by=request.user,
                    privacy_verified_at=timezone.now(),
                    deidentified_review_reference=deidentified_reference,
                    transmitted_data_manifest={
                        "direct_identifiers_included": False,
                        "age_instead_of_date_of_birth": True,
                        "source_filenames_included": False,
                        "sentinel_patient_or_encounter_ids_included": False,
                        "image_metadata_removed": True,
                        "visible_identifiers_checked_by_clinician": True,
                        "investigation_count": len(investigations),
                        "linked_fundus_count": len(fundus_images),
                        "training_consent_granted": bool(training_consent),
                    },
                )
        except (IntegrityError, ValidationError):
            existing = encounter.ocular_ai_reviews.first()
            return Response(
                {
                    "detail": (
                        "This encounter has already used its one-time "
                        "Sentinel AI Clinical Review."
                    ),
                    "review": (
                        OcularAIReviewSerializer(existing).data if existing else None
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )
        try:
            result, api_response = run_ocular_ai_review(
                encounter,
                assessment,
                investigations,
                fundus_images,
                review.deidentified_review_reference,
            )
            agreement = result.get(
                "agreement_status", "insufficient_data"
            )
            allowed_agreements = {
                key for key, _ in OcularAIReview.AGREEMENT_CHOICES
            }
            if agreement not in allowed_agreements:
                agreement = "insufficient_data"
            review.status = "completed"
            review.model_version = getattr(api_response, "model", "") or ""
            review.suspected_conditions = result.get(
                "suspected_conditions", []
            )
            review.supporting_findings = result.get(
                "supporting_findings", []
            )
            review.differential_diagnoses = result.get(
                "differential_diagnoses", []
            )
            review.suggested_urgency = result.get(
                "suggested_urgency", ""
            )
            review.suggested_management = result.get(
                "suggested_management", ""
            )
            review.limitations = result.get("limitations", [])
            review.agreement_status = agreement
            review.disagreement_reasons = result.get(
                "disagreement_reasons", []
            )
            review.expert_review_required = bool(
                result.get("expert_review_required")
                or agreement == "material_disagreement"
            )
            review.raw_response_json = result
            review.completed_at = timezone.now()
        except Exception as exc:
            review.status = "failed"
            review.error_message = str(exc)
            review.agreement_status = "insufficient_data"
            review.expert_review_required = True
            review.completed_at = timezone.now()
            if review.payment_status == "free":
                review.payment_status = "free_failed"
            else:
                with transaction.atomic():
                    wallet = OrganizationWallet.objects.select_for_update().get(
                        pk=review.charge_ledger_entry.wallet_id
                    )
                    refund, _ = WalletLedgerEntry.objects.get_or_create(
                        idempotency_key=f"ocular-ai-review:{encounter.pk}:refund",
                        defaults={
                            "wallet": wallet,
                            "entry_type": WalletLedgerEntry.EntryType.REFUND,
                            "available_delta": fee,
                            "reserved_delta": Decimal("0.00"),
                            "currency": "NGN",
                            "related_entry": review.charge_ledger_entry,
                            "reference": review.review_id,
                            "description": "Refund: Sentinel AI Clinical Review failed",
                            "metadata": {
                                "service_type": "ocular_ai_review",
                                "encounter_id": encounter.encounter_id,
                            },
                            "actor": request.user,
                        },
                    )
                    review.refund_ledger_entry = refund
                    review.payment_status = "refunded"
        review.save()

        http_status = (
            status.HTTP_201_CREATED
            if review.status == "completed"
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return Response(
            OcularAIReviewSerializer(review).data,
            status=http_status,
        )


class OcularAIReviewDecisionView(APIView):
    def post(self, request, pk):
        org = get_user_clinic(request.user)
        if not org or org.organization_type != "clinic":
            raise PermissionDenied("You are not linked to a clinic.")
        review = OcularAIReview.objects.filter(
            pk=pk,
            encounter__patient__assigned_clinic=org,
        ).first()
        if not review:
            return Response(status=status.HTTP_404_NOT_FOUND)
        decision = (request.data.get("decision") or "").strip()
        allowed = {"accepted", "modified", "rejected"}
        if decision not in allowed:
            return Response(
                {"detail": "Choose accepted, modified, or rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        notes = (request.data.get("notes") or "").strip()
        if decision in {"modified", "rejected"} and not notes:
            return Response(
                {"detail": "Decision notes are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        review.clinician_decision = decision
        review.clinician_decision_notes = notes
        review.decided_by = request.user
        review.decided_at = timezone.now()
        review.save(update_fields=[
            "clinician_decision", "clinician_decision_notes",
            "decided_by", "decided_at",
        ])
        return Response(OcularAIReviewSerializer(review).data)
