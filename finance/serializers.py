from rest_framework import serializers

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
)


class AllocationRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AllocationRule
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class PricingRuleSerializer(serializers.ModelSerializer):
    allocation_rules = AllocationRuleSerializer(many=True, read_only=True)

    class Meta:
        model = PricingRule
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


class PartnerContractSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)

    class Meta:
        model = PartnerContract
        fields = "__all__"
        read_only_fields = ("created_at", "updated_at")


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

    class Meta:
        model = SettlementBatch
        fields = "__all__"
        read_only_fields = (
            "status", "total_amount", "approved_by", "approved_at", "paid_at",
            "created_at", "updated_at",
        )


class BankTransferFundingRequestSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="wallet.organization.name", read_only=True)

    class Meta:
        model = BankTransferFundingRequest
        fields = "__all__"
        read_only_fields = (
            "request_reference", "status", "received_amount", "currency", "proof", "proof_submitted_at",
            "bank_transaction_reference", "value_date", "requester", "verified_by",
            "verified_at", "approved_by", "approved_at", "ledger_entry",
            "rejection_reason", "created_at", "updated_at",
        )

    def validate_requested_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Requested amount must be greater than zero.")
        return value


class PartnerFinanceSummarySerializer(serializers.Serializer):
    organization_id = serializers.IntegerField()
    organization_name = serializers.CharField()
    organization_type = serializers.CharField()
    wallet = OrganizationWalletSerializer(allow_null=True)
    active_contract = PartnerContractSerializer(allow_null=True)
    active_pricing_rules = PricingRuleSerializer(many=True)
    recent_ledger = WalletLedgerEntrySerializer(many=True)
    recent_financial_records = EncounterFinancialRecordSerializer(many=True)
