from rest_framework.routers import DefaultRouter

from .views import (
    AllocationRuleViewSet,
    EncounterFinancialRecordViewSet,
    PartnerContractViewSet,
    PricingRuleViewSet,
)

router = DefaultRouter()
router.register("contracts", PartnerContractViewSet, basename="finance-contract")
router.register("pricing-rules", PricingRuleViewSet, basename="finance-pricing-rule")
router.register("allocation-rules", AllocationRuleViewSet, basename="finance-allocation-rule")
router.register("financial-records", EncounterFinancialRecordViewSet, basename="finance-record")

urlpatterns = router.urls
