from rest_framework.routers import DefaultRouter

from .views import (
    AllocationRuleViewSet,
    EncounterFinancialRecordViewSet,
    PartnerContractViewSet,
    PricingRuleViewSet,
    OrganizationWalletViewSet,
    WalletLedgerEntryViewSet,
    WalletReservationViewSet,
)

router = DefaultRouter()
router.register("contracts", PartnerContractViewSet, basename="finance-contract")
router.register("pricing-rules", PricingRuleViewSet, basename="finance-pricing-rule")
router.register("allocation-rules", AllocationRuleViewSet, basename="finance-allocation-rule")
router.register("financial-records", EncounterFinancialRecordViewSet, basename="finance-record")
router.register("wallets", OrganizationWalletViewSet, basename="finance-wallet")
router.register("wallet-ledger", WalletLedgerEntryViewSet, basename="finance-wallet-ledger")
router.register("wallet-reservations", WalletReservationViewSet, basename="finance-wallet-reservation")

urlpatterns = router.urls
