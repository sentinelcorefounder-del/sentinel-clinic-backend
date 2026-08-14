from rest_framework import serializers
from django.db.models import Count, Q
from organizations.models import Organization
from encounters.models import AssessmentServiceSession

from .models import (
    AllocationRule,
    EncounterAllocation,
    EncounterFinancialRecord,
    PartnerContract,
    PricingRule,
    OrganizationWallet,
    WalletLedgerEntry,
    WalletReservation,
    SettlementBatch,
    SettlementItem,
    BankTransferFundingRequest,
    ServiceAllowance,
    ServiceAllowanceReservation,
    FinanceActionRequest,
    FinanceControlAudit,
    BillingProfile,
    ServicePartnerEarning,
    ServicePartnerSettlementBatch,
    ServicePartnerAdjustment,
    ServicePartnerCorrectionRequest,
)


class ServicePartnerEarningSerializer(serializers.ModelSerializer):
    service_partner_name = serializers.CharField(source="service_partner.name", read_only=True)
    encounter_reference = serializers.CharField(source="encounter.encounter_id", read_only=True)
    settlement_id = serializers.IntegerField(source="active_settlement_id", read_only=True)

    class Meta:
        model = ServicePartnerEarning
        fields = (
            "id", "earning_reference", "service_partner", "service_partner_name",
            "encounter_reference", "service_session", "session_reference",
            "assessment_date", "provider_type", "amount", "currency", "rate_snapshot",
            "trigger_source", "earned_at", "status", "settlement_id",
        )
        read_only_fields = fields


class ServicePartnerSettlementSerializer(serializers.ModelSerializer):
    service_partner_name = serializers.CharField(source="service_partner.name", read_only=True)
    prepared_by_name = serializers.CharField(source="prepared_by.username", read_only=True)
    approved_by_name = serializers.CharField(source="approved_by.username", read_only=True)
    paid_by_name = serializers.CharField(source="paid_by.username", read_only=True)
    included_sessions = serializers.SerializerMethodField()
    rate_breakdown = serializers.SerializerMethodField()
    has_payment_evidence = serializers.SerializerMethodField()

    class Meta:
        model = ServicePartnerSettlementBatch
        fields = (
            "id", "service_partner", "service_partner_name", "assessment_date", "currency",
            "status", "assessment_count", "gross_amount", "final_amount", "included_sessions",
            "rate_breakdown", "prepared_by_name", "approved_by_name", "approved_at",
            "rejected_at", "rejection_reason", "cancelled_at", "cancellation_reason",
            "paid_by_name", "paid_at", "payment_date", "external_reference",
            "has_payment_evidence", "created_at",
        )
        read_only_fields = fields

    def get_included_sessions(self, obj):
        return sorted(set(obj.items.filter(earning__isnull=False).values_list("earning__session_reference", flat=True)))

    def get_rate_breakdown(self, obj):
        rows = obj.items.values("amount", "currency").annotate(count=Count("id")).order_by("amount", "currency")
        return [{"rate": str(row["amount"]), "currency": row["currency"], "count": row["count"]} for row in rows]

    def get_has_payment_evidence(self, obj):
        return bool(obj.payment_evidence)


class ServicePartnerAdjustmentSerializer(serializers.ModelSerializer):
    earning_reference = serializers.CharField(source="original_earning.earning_reference", read_only=True)
    service_partner_name = serializers.CharField(source="service_partner.name", read_only=True)

    class Meta:
        model = ServicePartnerAdjustment
        fields = (
            "id", "earning_reference", "service_partner", "service_partner_name", "amount",
            "currency", "reason", "status", "active_settlement", "posted_at", "applied_at",
        )
        read_only_fields = fields


class ServicePartnerCorrectionSerializer(serializers.ModelSerializer):
    earning_reference = serializers.CharField(source="earning.earning_reference", read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.username", read_only=True)
    decided_by_name = serializers.CharField(source="decided_by.username", read_only=True)
    adjustment = serializers.SerializerMethodField()

    class Meta:
        model = ServicePartnerCorrectionRequest
        fields = (
            "id", "earning", "earning_reference", "amount", "reason", "status",
            "requested_by_name", "decided_by_name", "decided_at", "decision_reason",
            "adjustment", "created_at",
        )
        read_only_fields = fields

    def get_adjustment(self, obj):
        try:
            adjustment = obj.adjustment
        except ServicePartnerAdjustment.DoesNotExist:
            return None
        return ServicePartnerAdjustmentSerializer(adjustment).data


class AllocationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AllocationRule
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")

    def update(self, instance, validated_data):
        if instance.generated_allocations.exists():
            raise serializers.ValidationError(
                "This allocation rule has historical transactions. Create a new pricing-rule version instead."
            )
        return super().update(instance, validated_data)


class PricingRuleSerializer(serializers.ModelSerializer):
    allocation_rules = AllocationRuleSerializer(many=True, read_only=True)

    class Meta:
        model = PricingRule
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")

    def validate(self, attrs):
        contract = attrs.get("contract", getattr(self.instance, "contract", None))
        supersedes = attrs.get("supersedes", getattr(self.instance, "supersedes", None))
        if supersedes and contract and supersedes.contract_id != contract.id:
            raise serializers.ValidationError({"supersedes": "Must belong to the same contract."})
        service_type = attrs.get(
            "service_type", getattr(self.instance, "service_type", "retinal_assessment")
        )
        if contract:
            organization_type = contract.organization.organization_type
            if organization_type == "hospital" and service_type in {
                "ocular_ai_review", "sentinel_ai_analysis"
            }:
                raise serializers.ValidationError({
                    "service_type": "Clinic AI charges cannot be added to a hospital contract."
                })
            if service_type == "ocular_ai_review" and organization_type != "clinic":
                raise serializers.ValidationError({
                    "service_type": "Ocular AI review pricing is available only for clinic contracts."
                })
        return attrs

    def update(self, instance, validated_data):
        if instance.financial_records.exists():
            raise serializers.ValidationError(
                "This pricing rule has historical transactions. Create a new version instead."
            )
        return super().update(instance, validated_data)


class PartnerContractSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    organization_type = serializers.CharField(
        source="organization.organization_type", read_only=True
    )
    pricing_rules = PricingRuleSerializer(many=True, read_only=True)
    has_financial_history = serializers.SerializerMethodField()

    class Meta:
        model = PartnerContract
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")

    def get_has_financial_history(self, obj):
        return obj.financial_records.exists()

    def validate(self, attrs):
        organization = attrs.get(
            "organization", getattr(self.instance, "organization", None)
        )
        if organization and organization.organization_type == "service_partner":
            raise serializers.ValidationError({
                "organization": "Service partners cannot receive clinical pricing contracts."
            })
        programme = attrs.get(
            "programme", getattr(self.instance, "programme", "diabetic_screening")
        )
        contract_status = attrs.get(
            "status", getattr(self.instance, "status", PartnerContract.Status.DRAFT)
        )
        effective_from = attrs.get(
            "effective_from", getattr(self.instance, "effective_from", None)
        )
        effective_to = attrs.get(
            "effective_to", getattr(self.instance, "effective_to", None)
        )
        if effective_from and effective_to and effective_to < effective_from:
            raise serializers.ValidationError({
                "effective_to": "Effective-to cannot precede effective-from."
            })
        if contract_status == PartnerContract.Status.ACTIVE and organization and effective_from:
            overlapping = PartnerContract.objects.filter(
                organization=organization,
                programme=programme,
                status=PartnerContract.Status.ACTIVE,
            ).exclude(pk=getattr(self.instance, "pk", None))
            if effective_to:
                overlapping = overlapping.filter(effective_from__lte=effective_to)
            overlapping = overlapping.filter(
                Q(effective_to__isnull=True) | Q(effective_to__gte=effective_from)
            )
            if overlapping.exists():
                raise serializers.ValidationError(
                    "Only one active contract may cover an organisation, programme, and date."
                )
        return attrs


class EncounterAllocationSerializer(serializers.ModelSerializer):
    beneficiary_organization_name = serializers.CharField(
        source="beneficiary_organization.name", read_only=True
    )

    class Meta:
        model = EncounterAllocation
        fields = "__all__"
        read_only_fields = (
            "id", "financial_record", "allocation_rule", "beneficiary_role",
            "beneficiary_organization", "beneficiary_source", "label", "amount", "currency",
            "rule_snapshot", "status", "earned_at", "reversed_at", "settled_at",
            "created_at", "updated_at",
        )


class EncounterFinancialRecordSerializer(serializers.ModelSerializer):
    encounter_id = serializers.CharField(source="encounter.encounter_id", read_only=True)
    organization_name = serializers.CharField(
        source="encounter.originating_organization.name", read_only=True
    )
    payer_organization_name = serializers.CharField(
        source="payer_organization.name", read_only=True, allow_null=True
    )
    payment_responsibility = serializers.CharField(
        source="encounter.payment_responsibility", read_only=True
    )
    clinical_status = serializers.CharField(
        source="encounter.screening_status", read_only=True
    )
    contract_name = serializers.CharField(source="contract.name", read_only=True)
    pricing_rule_name = serializers.CharField(source="pricing_rule.name", read_only=True)
    allocations = EncounterAllocationSerializer(many=True, read_only=True)

    class Meta:
        model = EncounterFinancialRecord
        fields = "__all__"
        read_only_fields = (
            "id", "encounter", "contract", "pricing_rule", "status", "currency",
            "service_pathway", "payer_type", "payer_organization", "collector_type",
            "collecting_organization", "payment_method",
            "gross_amount", "allocated_amount", "outstanding_amount",
            "financially_releasable", "pricing_snapshot", "exception_reason",
            "priced_at", "secured_at", "captured_at", "settled_at",
            "created_at", "updated_at",
        )


class OrganizationWalletSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    available_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    reserved_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    spendable_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = OrganizationWallet
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")

    def validate_organization(self, organization):
        if organization.organization_type == "service_partner":
            raise serializers.ValidationError("Service partners cannot have organization wallets.")
        return organization


class WalletLedgerEntrySerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="wallet.organization.name", read_only=True)

    class Meta:
        model = WalletLedgerEntry
        fields = "__all__"
        read_only_fields = tuple(field.name for field in WalletLedgerEntry._meta.fields)


class WalletReservationSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="wallet.organization.name", read_only=True)
    encounter_id = serializers.CharField(source="financial_record.encounter.encounter_id", read_only=True)
    remaining_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = WalletReservation
        fields = "__all__"
        read_only_fields = (
            "wallet", "financial_record", "amount", "captured_amount", "released_amount",
            "currency", "status", "idempotency_key", "reference", "reserved_at",
            "captured_at", "released_at", "created_at", "updated_at",
        )


class SettlementItemSerializer(serializers.ModelSerializer):
    encounter_id = serializers.CharField(source="allocation.financial_record.encounter.encounter_id", read_only=True)

    class Meta:
        model = SettlementItem
        fields = "__all__"
        read_only_fields = tuple(field.name for field in SettlementItem._meta.fields)


class SettlementBatchSerializer(serializers.ModelSerializer):
    beneficiary_organization_name = serializers.CharField(source="beneficiary_organization.name", read_only=True)
    items = SettlementItemSerializer(many=True, read_only=True)
    payment_evidence_available = serializers.SerializerMethodField()

    class Meta:
        model = SettlementBatch
        exclude = ("payment_evidence",)
        read_only_fields = (
            "status", "total_amount", "external_reference",
            "prepared_by", "approved_by", "approved_at", "paid_by", "paid_at", "cancelled_by",
            "cancelled_at", "cancellation_reason",
            "created_at", "updated_at",
        )

    def get_payment_evidence_available(self, obj):
        return bool(obj.payment_evidence)

    def validate(self, attrs):
        if self.instance and self.instance.status != SettlementBatch.Status.DRAFT:
            raise serializers.ValidationError("Only draft settlement batches can be edited.")
        return attrs


class BankTransferFundingRequestSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="wallet.organization.name", read_only=True)
    proof_available = serializers.SerializerMethodField()

    class Meta:
        model = BankTransferFundingRequest
        exclude = ("proof",)
        read_only_fields = (
            "request_reference", "status", "received_amount", "currency", "proof_submitted_at",
            "bank_transaction_reference", "value_date", "requester", "verified_by",
            "verified_at", "approved_by", "approved_at", "ledger_entry",
            "rejection_reason", "created_at", "updated_at",
            "billing_snapshot", "customer_snapshot", "receipt_reference",
        )

    def get_proof_available(self, obj):
        return bool(obj.proof)

    def validate_requested_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Requested amount must be greater than zero.")
        return value


class BillingProfileSerializer(serializers.ModelSerializer):
    is_complete = serializers.BooleanField(read_only=True)

    class Meta:
        model = BillingProfile
        fields = "__all__"
        read_only_fields = ("updated_by", "created_at", "updated_at")

    def validate(self, attrs):
        import re
        active = attrs.get("is_active", getattr(self.instance, "is_active", True))
        if active:
            required = ("legal_entity_name", "bank_name", "bank_account_name",
                        "bank_account_number", "currency")
            missing = [name for name in required if not str(
                attrs.get(name, getattr(self.instance, name, "")) or ""
            ).strip()]
            if missing:
                raise serializers.ValidationError({name: "Required for an active billing profile." for name in missing})
        for name in ("funding_request_prefix", "receipt_prefix"):
            if name in attrs:
                value = attrs[name]
            elif self.instance is not None:
                value = getattr(self.instance, name, "")
            else:
                value = BillingProfile._meta.get_field(name).get_default()
            value = str(value or "")
            if not re.fullmatch(r"[A-Z0-9-]{2,20}", value):
                raise serializers.ValidationError({name: "Use 2–20 uppercase letters, numbers or hyphens."})
        return attrs


class ServiceAllowanceSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    reserved_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    reserved_patients = serializers.IntegerField(read_only=True)

    class Meta:
        model = ServiceAllowance
        fields = "__all__"
        read_only_fields = ("status", "approved_by", "approved_at", "created_at", "updated_at")

    def validate(self, attrs):
        monetary_limit = attrs.get("monetary_limit", getattr(self.instance, "monetary_limit", None))
        patient_limit = attrs.get("patient_limit", getattr(self.instance, "patient_limit", None))
        if monetary_limit is None and patient_limit is None:
            raise serializers.ValidationError("Provide a monetary limit, a patient limit, or both.")
        if monetary_limit is not None and monetary_limit <= 0:
            raise serializers.ValidationError({"monetary_limit": "Must be greater than zero."})
        if patient_limit is not None and patient_limit <= 0:
            raise serializers.ValidationError({"patient_limit": "Must be greater than zero."})
        organization = attrs.get("organization", getattr(self.instance, "organization", None))
        contract = attrs.get("contract", getattr(self.instance, "contract", None))
        if contract and organization and contract.organization_id != organization.id:
            raise serializers.ValidationError({"contract": "Contract and allowance organisation must match."})
        return attrs


class ServiceAllowanceReservationSerializer(serializers.ModelSerializer):
    encounter_id = serializers.CharField(source="financial_record.encounter.encounter_id", read_only=True)

    class Meta:
        model = ServiceAllowanceReservation
        fields = "__all__"
        read_only_fields = tuple(field.name for field in ServiceAllowanceReservation._meta.fields)


class PartnerFinanceSummarySerializer(serializers.Serializer):
    organization_id = serializers.IntegerField()
    organization_name = serializers.CharField()
    organization_type = serializers.CharField()
    wallet = OrganizationWalletSerializer(allow_null=True)
    active_contract = PartnerContractSerializer(allow_null=True)
    active_pricing_rules = PricingRuleSerializer(many=True)
    recent_ledger = WalletLedgerEntrySerializer(many=True)
    recent_financial_records = EncounterFinancialRecordSerializer(many=True)


class FinanceActionRequestSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="wallet.organization.name", read_only=True)
    requested_by_username = serializers.CharField(source="requested_by.username", read_only=True)
    decided_by_username = serializers.CharField(source="decided_by.username", read_only=True)
    evidence_available = serializers.SerializerMethodField()

    class Meta:
        model = FinanceActionRequest
        exclude = ("evidence",)
        read_only_fields = (
            "currency", "status", "requested_by", "decided_by", "decided_at",
            "decision_reason", "posted_entry", "created_at", "updated_at",
        )

    def get_evidence_available(self, obj):
        return bool(obj.evidence)


class FinanceControlAuditSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(source="actor.username", read_only=True)

    class Meta:
        model = FinanceControlAudit
        fields = "__all__"
        read_only_fields = tuple(field.name for field in FinanceControlAudit._meta.fields)


class ServicePartnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = (
            "id", "clinic_id", "name", "contact_email", "address", "phone",
            "currency", "is_active", "created_at",
        )
        read_only_fields = ("created_at",)

    def create(self, validated_data):
        return Organization.objects.create(
            organization_type="service_partner", **validated_data
        )

    def update(self, instance, validated_data):
        if instance.organization_type != "service_partner":
            raise serializers.ValidationError("Only service partners may be managed here.")
        return super().update(instance, validated_data)


class AssessmentServiceSessionSerializer(serializers.ModelSerializer):
    participating_organization_name = serializers.CharField(
        source="participating_organization.name", read_only=True
    )
    service_branch_name = serializers.CharField(source="service_branch.name", read_only=True)
    service_partner_name = serializers.CharField(source="service_partner.name", read_only=True)
    linked_encounter_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AssessmentServiceSession
        fields = "__all__"
        read_only_fields = (
            "session_reference", "status", "configuration_version", "created_by",
            "activated_by", "activated_at", "completed_by", "completed_at",
            "cancelled_by", "cancelled_at", "cancellation_reason",
            "created_at", "updated_at", "linked_encounter_count",
        )

    def validate(self, attrs):
        instance = self.instance
        if instance and instance.status != AssessmentServiceSession.Status.DRAFT:
            material = set(AssessmentServiceSession.IMMUTABLE_TERMS) & set(attrs)
            if material:
                raise serializers.ValidationError("Material session terms are frozen after activation.")
        return attrs

    def create(self, validated_data):
        session = AssessmentServiceSession(
            created_by=self.context["request"].user,
            status=AssessmentServiceSession.Status.DRAFT,
            **validated_data,
        )
        session.save()
        return session
