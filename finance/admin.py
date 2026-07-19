from django.contrib import admin

from .models import (
    AllocationRule,
    EncounterAllocation,
    EncounterFinancialRecord,
    FinancialAuditLog,
    PartnerContract,
    PricingRule,
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
    list_display = ("encounter", "status", "gross_amount", "currency", "financially_releasable")
    list_filter = ("status", "currency", "financially_releasable")
    search_fields = ("encounter__encounter_id", "encounter__patient__first_name", "encounter__patient__last_name")
    readonly_fields = ("pricing_snapshot", "created_at", "updated_at")


admin.site.register(AllocationRule)
admin.site.register(EncounterAllocation)
admin.site.register(FinancialAuditLog)
