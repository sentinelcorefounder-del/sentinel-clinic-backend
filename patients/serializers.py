from rest_framework import serializers

from .models import Patient


class PatientSerializer(serializers.ModelSerializer):
    source_type = serializers.SerializerMethodField()
    referring_hospital_id = serializers.SerializerMethodField()
    referring_hospital_name = serializers.SerializerMethodField()
    referral_id_display = serializers.SerializerMethodField()
    referring_hospitals = serializers.SerializerMethodField()

    class Meta:
        model = Patient
        fields = [
            "id", "patient_id", "first_name", "last_name",
            "date_of_birth", "sex", "phone", "email",
            "address", "city", "state", "country",
            "consent_status", "source_type",
            "referring_hospital_id", "referring_hospital_name",
            "referral_id_display", "referring_hospitals",
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
