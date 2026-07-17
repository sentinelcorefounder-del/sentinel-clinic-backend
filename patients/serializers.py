from rest_framework import serializers

from .models import (
    HistoricalRecordAccessRequest,
    MasterPatient,
    Patient,
    PatientIdentityReview,
    PatientOrganizationIdentity,
)


class PatientSerializer(serializers.ModelSerializer):
    source_type = serializers.SerializerMethodField()
    referring_hospital_id = serializers.SerializerMethodField()
    referring_hospital_name = serializers.SerializerMethodField()
    referral_id_display = serializers.SerializerMethodField()
    referring_hospitals = serializers.SerializerMethodField()
    sentinel_patient_id = serializers.CharField(
        source="master_patient.sentinel_patient_id",
        read_only=True,
    )
    master_patient_id = serializers.IntegerField(
        source="master_patient.id",
        read_only=True,
    )

    class Meta:
        model = Patient
        fields = [
            "id", "patient_id", "first_name", "last_name",
            "date_of_birth", "sex", "phone", "email",
            "address", "city", "state", "country",
            "consent_status", "source_type",
            "referring_hospital_id", "referring_hospital_name",
            "referral_id_display", "referring_hospitals",
            "sentinel_patient_id", "master_patient_id",
            "created_at", "updated_at",
        ]

    def _clinic_referrals(self, obj):
        prefetched = getattr(obj, "clinic_source_referrals", None)
        if prefetched is not None:
            return prefetched

        request = self.context.get("request")
        clinic = getattr(request, "clinic_organization", None)
        queryset = obj.hospital_referrals.select_related(
            "source_hospital", "matched_clinic"
        )
        if clinic:
            queryset = queryset.filter(matched_clinic=clinic)
        return list(queryset.order_by("-updated_at", "-id"))

    def get_source_type(self, obj):
        return (
            "hospital_referral"
            if self._clinic_referrals(obj)
            else "clinic_direct"
        )

    def get_referring_hospital_id(self, obj):
        referrals = self._clinic_referrals(obj)
        if not referrals:
            return None
        hospital = referrals[0].source_hospital
        return hospital.id if hospital else None

    def get_referring_hospital_name(self, obj):
        referrals = self._clinic_referrals(obj)
        if not referrals:
            return ""
        hospital = referrals[0].source_hospital
        return hospital.name if hospital else ""

    def get_referral_id_display(self, obj):
        referrals = self._clinic_referrals(obj)
        return referrals[0].referral_id if referrals else ""

    def get_referring_hospitals(self, obj):
        rows = []
        seen = set()
        for referral in self._clinic_referrals(obj):
            hospital = referral.source_hospital
            if not hospital or hospital.id in seen:
                continue
            seen.add(hospital.id)
            rows.append({
                "id": hospital.id,
                "name": hospital.name,
                "referral_id": referral.referral_id,
                "referral_status": referral.referral_status,
            })
        return rows


class ClinicDirectPatientCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Patient
        fields = [
            "first_name", "last_name", "date_of_birth", "sex",
            "phone", "email", "address", "city", "state", "country",
        ]

    def validate(self, attrs):
        duplicate = Patient.objects.filter(
            first_name__iexact=(attrs.get("first_name") or "").strip(),
            last_name__iexact=(attrs.get("last_name") or "").strip(),
            date_of_birth=attrs.get("date_of_birth"),
        ).first()
        if duplicate:
            raise serializers.ValidationError({
                "non_field_errors": [
                    "A patient with the same name and date of birth already exists. "
                    "Search for the existing patient first."
                ]
            })
        return attrs


class PatientOrganizationIdentitySerializer(
    serializers.ModelSerializer
):
    organization_name = serializers.CharField(
        source="organization.name",
        read_only=True,
    )

    class Meta:
        model = PatientOrganizationIdentity
        fields = [
            "id",
            "organization",
            "organization_name",
            "identity_type",
            "local_identifier",
            "is_verified",
            "first_seen_at",
            "last_seen_at",
        ]


class MasterPatientSerializer(serializers.ModelSerializer):
    organization_identities = (
        PatientOrganizationIdentitySerializer(
            many=True,
            read_only=True,
        )
    )
    patient_record_ids = serializers.SerializerMethodField()

    class Meta:
        model = MasterPatient
        fields = [
            "id",
            "sentinel_patient_id",
            "first_name",
            "middle_name",
            "last_name",
            "date_of_birth",
            "sex",
            "primary_phone",
            "primary_email",
            "identity_status",
            "organization_identities",
            "patient_record_ids",
            "created_at",
            "updated_at",
        ]

    def get_patient_record_ids(self, obj):
        return list(
            obj.patient_records.values_list("id", flat=True)
        )


class PatientIdentityReviewSerializer(
    serializers.ModelSerializer
):
    candidate_patient_id = serializers.CharField(
        source="candidate_patient.patient_id",
        read_only=True,
    )
    candidate_patient_name = serializers.SerializerMethodField()
    possible_master_patient_display = serializers.CharField(
        source="possible_master_patient.sentinel_patient_id",
        read_only=True,
    )

    class Meta:
        model = PatientIdentityReview
        fields = [
            "id",
            "candidate_patient",
            "candidate_patient_id",
            "candidate_patient_name",
            "possible_master_patient",
            "possible_master_patient_display",
            "match_score",
            "match_reasons",
            "status",
            "reviewed_by",
            "reviewed_at",
            "decision_note",
            "created_at",
        ]

    def get_candidate_patient_name(self, obj):
        patient = obj.candidate_patient
        return (
            f"{patient.first_name} {patient.last_name}"
        ).strip()


class HistoricalRecordAccessRequestSerializer(
    serializers.ModelSerializer
):
    master_patient_display = serializers.CharField(
        source="master_patient.sentinel_patient_id",
        read_only=True,
    )
    patient_name = serializers.SerializerMethodField()
    requesting_organization_name = serializers.CharField(
        source="requesting_organization.name",
        read_only=True,
    )
    requested_by_display = serializers.SerializerMethodField()
    reviewed_by_display = serializers.SerializerMethodField()
    is_currently_active = serializers.BooleanField(
        read_only=True,
    )

    class Meta:
        model = HistoricalRecordAccessRequest
        fields = [
            "id",
            "master_patient",
            "master_patient_display",
            "patient_name",
            "requesting_organization",
            "requesting_organization_name",
            "requested_by",
            "requested_by_display",
            "purpose",
            "consent_reference",
            "consent_record",
            "include_reports",
            "include_images",
            "status",
            "reviewed_by",
            "reviewed_by_display",
            "reviewed_at",
            "review_note",
            "expires_at",
            "revoked_at",
            "is_currently_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "requesting_organization",
            "requested_by",
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "expires_at",
            "revoked_at",
        ]

    def get_patient_name(self, obj):
        return (
            f"{obj.master_patient.first_name} "
            f"{obj.master_patient.last_name}"
        ).strip()

    def get_requested_by_display(self, obj):
        user = obj.requested_by
        if not user:
            return ""
        return user.get_full_name() or user.username or user.email

    def get_reviewed_by_display(self, obj):
        user = obj.reviewed_by
        if not user:
            return ""
        return user.get_full_name() or user.username or user.email
