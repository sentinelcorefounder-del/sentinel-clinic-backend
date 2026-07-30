from django.db import IntegrityError, transaction
from rest_framework import serializers
from .models import Organization, OrganizationBranch, OrganizationProfile


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = [
            "id",
            "clinic_id",
            "name",
            "organization_type",
            "address",
            "contact_email",
            "phone",
            "logo",
            "report_signatory_name",
            "report_signatory_title",
            "report_signatory_odorbn",
            "report_footer_note",
            "screening_fee_amount",
            "hospital_commission_amount",
            "currency",
            "created_at",
        ]

    def validate_logo(self, logo):
        if logo.size > 2 * 1024 * 1024:
            raise serializers.ValidationError("Logo must be 2 MB or smaller.")
        content_type = getattr(logo, "content_type", "")
        if content_type and content_type not in {"image/png", "image/jpeg"}:
            raise serializers.ValidationError("Logo must be a PNG or JPEG image.")
        return logo



class OrganizationProfileSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(
        source="organization.name",
        read_only=True,
    )
    organization_code = serializers.CharField(
        source="organization.clinic_id",
        read_only=True,
    )
    organization_type = serializers.CharField(
        source="organization.organization_type",
        read_only=True,
    )

    class Meta:
        model = OrganizationProfile
        fields = [
            "id",
            "organization",
            "organization_name",
            "organization_code",
            "organization_type",
            "workflow_mode",
            "referral_requirement",
            "patient_ownership",
            "can_create_direct_patients",
            "can_issue_reports_directly",
            "electronic_signature_required",
            "sentinel_review_policy",
            "default_payment_responsibility",
            "branding_policy",
            "default_programme",
            "subscription_tier",
            "ai_enabled",
            "clinic_direct_screening_enabled",
            "ocular_diagnostics_enabled",
            "feature_flags",
            "settings_notes",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "organization",
            "organization_name",
            "organization_code",
            "organization_type",
            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        instance = self.instance

        workflow_mode = attrs.get(
            "workflow_mode",
            getattr(instance, "workflow_mode", "sentinel_managed"),
        )
        sentinel_review_policy = attrs.get(
            "sentinel_review_policy",
            getattr(instance, "sentinel_review_policy", "mandatory"),
        )
        can_issue_reports_directly = attrs.get(
            "can_issue_reports_directly",
            getattr(instance, "can_issue_reports_directly", False),
        )

        if workflow_mode == "sentinel_managed":
            if can_issue_reports_directly:
                raise serializers.ValidationError(
                    {
                        "can_issue_reports_directly": (
                            "Sentinel-managed clinics cannot issue reports directly."
                        )
                    }
                )
            if sentinel_review_policy != "mandatory":
                raise serializers.ValidationError(
                    {
                        "sentinel_review_policy": (
                            "Sentinel-managed clinics require mandatory Sentinel review."
                        )
                    }
                )

        if workflow_mode == "clinic_managed":
            if not can_issue_reports_directly:
                raise serializers.ValidationError(
                    {
                        "can_issue_reports_directly": (
                            "Clinic-managed clinics must be able to issue reports directly."
                        )
                    }
                )
            if sentinel_review_policy == "mandatory":
                raise serializers.ValidationError(
                    {
                        "sentinel_review_policy": (
                            "Clinic-managed clinics cannot require mandatory Sentinel review."
                        )
                    }
                )

        if workflow_mode == "hybrid":
            if not can_issue_reports_directly:
                raise serializers.ValidationError(
                    {
                        "can_issue_reports_directly": (
                            "Hybrid clinics must be able to issue reports directly."
                        )
                    }
                )
            if sentinel_review_policy != "optional":
                raise serializers.ValidationError(
                    {
                        "sentinel_review_policy": (
                            "Hybrid clinics must use optional Sentinel review."
                        )
                    }
                )

        return attrs


class OrganizationWithProfileSerializer(OrganizationSerializer):
    capability_profile = serializers.SerializerMethodField()

    class Meta(OrganizationSerializer.Meta):
        fields = OrganizationSerializer.Meta.fields + [
            "is_active",
            "capability_profile",
        ]

    def get_capability_profile(self, obj):
        profile, _ = OrganizationProfile.objects.get_or_create(
            organization=obj
        )
        return OrganizationProfileSerializer(profile).data


class OrganizationBranchSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(
        source="organization.name",
        read_only=True,
    )

    class Meta:
        model = OrganizationBranch
        fields = [
            "id", "organization", "organization_name", "branch_code", "name",
            "address", "contact_email", "phone", "is_head_office", "is_active",
            "inherits_branding", "inherits_contract", "inherits_wallet",
            "created_at", "updated_at",
        ]
        read_only_fields = ["organization", "created_at", "updated_at"]

    def validate_branch_code(self, value):
        return value.strip().upper().replace(" ", "-")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        organization = (
            self.instance.organization
            if self.instance
            else self.context["view"]._organization()
        )
        branch_code = attrs.get(
            "branch_code",
            getattr(self.instance, "branch_code", ""),
        )

        if organization and branch_code:
            duplicates = OrganizationBranch.objects.filter(
                organization=organization,
                branch_code=branch_code,
            )
            if self.instance:
                duplicates = duplicates.exclude(pk=self.instance.pk)
            if duplicates.exists():
                raise serializers.ValidationError(
                    {
                        "branch_code": (
                            "A branch with this code already exists for this "
                            "organisation."
                        )
                    }
                )

        return attrs

    def create(self, validated_data):
        try:
            with transaction.atomic():
                return super().create(validated_data)
        except IntegrityError as exc:
            organization = validated_data.get("organization")
            branch_code = validated_data.get("branch_code")
            if (
                organization
                and branch_code
                and OrganizationBranch.objects.filter(
                    organization=organization,
                    branch_code=branch_code,
                ).exists()
            ):
                raise serializers.ValidationError(
                    {
                        "branch_code": (
                            "A branch with this code already exists for this "
                            "organisation."
                        )
                    }
                ) from exc
            raise
