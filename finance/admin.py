from django.contrib import admin

from .models import (
    AllocationRule,
    EncounterAllocation,
    EncounterFinancialRecord,
    FinancialAuditLog,
    PartnerContract,
    PricingRule,
    OrganizationWallet,
    WalletLedgerEntry,
    WalletReservation,
    SettlementBatch,
    SettlementItem,
)


class AllocationRuleInline(admin.TabularInline):
    model = AllocationRule
    extra = 0


@admin.register(PartnerContract)
class PartnerContractAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "programme", "status", "effective_from", "effective_to")
    list_filter = ("status", "programme", "currency")
    search_fields = ("name", "organization__name")


@admin.register(PricingRule)
class PricingRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "contract", "service_type", "gross_amount", "priority", "is_active")
    list_filter = ("is_active", "service_type", "source_type", "workflow_route")
    inlines = [AllocationRuleInline]


@admin.register(EncounterFinancialRecord)
class EncounterFinancialRecordAdmin(admin.ModelAdmin):
    list_display = (
        "encounter", "service_pathway", "payer_type", "collector_type",
        "payment_method", "status", "gross_amount", "currency", "financially_releasable",
    )
    list_filter = (
        "service_pathway", "payer_type", "collector_type", "payment_method",
        "status", "currency", "financially_releasable",
    )
    search_fields = ("encounter__encounter_id", "encounter__patient__first_name", "encounter__patient__last_name")
    readonly_fields = ("pricing_snapshot", "created_at", "updated_at")


admin.site.register(AllocationRule)
admin.site.register(EncounterAllocation)
admin.site.register(FinancialAuditLog)


@admin.register(OrganizationWallet)
class OrganizationWalletAdmin(admin.ModelAdmin):
    list_display = ("organization", "currency", "is_active", "credit_limit", "available_balance_display", "reserved_balance_display")
    list_filter = ("is_active", "currency")
    search_fields = ("organization__name", "organization__clinic_id")

    @admin.display(description="Available")
    def available_balance_display(self, obj):
        return obj.available_balance

    @admin.display(description="Reserved")
    def reserved_balance_display(self, obj):
        return obj.reserved_balance


@admin.register(WalletReservation)
class WalletReservationAdmin(admin.ModelAdmin):
    list_display = ("id", "wallet", "financial_record", "amount", "captured_amount", "released_amount", "status")
    list_filter = ("status", "currency")
    search_fields = ("reference", "idempotency_key", "financial_record__encounter__encounter_id")
    readonly_fields = ("reserved_at", "captured_at", "released_at", "created_at", "updated_at")


@admin.register(WalletLedgerEntry)
class WalletLedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("id", "wallet", "entry_type", "available_delta", "reserved_delta", "currency", "created_at")
    list_filter = ("entry_type", "currency", "created_at")
    search_fields = ("reference", "idempotency_key", "description", "wallet__organization__name")
    readonly_fields = tuple(field.name for field in WalletLedgerEntry._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SettlementItemInline(admin.TabularInline):
    model = SettlementItem
    extra = 0
    readonly_fields = ("allocation", "amount", "currency", "created_at", "updated_at")
    can_delete = False


@admin.register(SettlementBatch)
class SettlementBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "beneficiary_organization", "period_start", "period_end", "total_amount", "currency", "status")
    list_filter = ("status", "currency", "period_end")
    search_fields = ("beneficiary_organization__name", "external_reference")
    readonly_fields = ("total_amount", "approved_by", "approved_at", "paid_at", "created_at", "updated_at")
    inlines = [SettlementItemInline]
