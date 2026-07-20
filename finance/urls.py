from rest_framework.routers import DefaultRouter

from .views import (
    AllocationRuleViewSet,
    EncounterFinancialRecordViewSet,
    PartnerContractViewSet,
    PricingRuleViewSet,
    OrganizationWalletViewSet,
    WalletLedgerEntryViewSet,
    WalletReservationViewSet,
    SettlementBatchViewSet,
)

router = DefaultRouter()
router.register("contracts", PartnerContractViewSet, basename="finance-contract")
router.register("pricing-rules", PricingRuleViewSet, basename="finance-pricing-rule")
router.register("allocation-rules", AllocationRuleViewSet, basename="finance-allocation-rule")
router.register("financial-records", EncounterFinancialRecordViewSet, basename="finance-record")
router.register("wallets", OrganizationWalletViewSet, basename="finance-wallet")
router.register("wallet-ledger", WalletLedgerEntryViewSet, basename="finance-wallet-ledger")
router.register("wallet-reservations", WalletReservationViewSet, basename="finance-wallet-reservation")
router.register("settlements", SettlementBatchViewSet, basename="finance-settlement")

urlpatterns = router.urls

from django.urls import path
from .views import FinanceDashboardSummaryView, PartnerFinanceView, FinanceOrganizationOptionsView

urlpatterns += [
    path("summary/", FinanceDashboardSummaryView.as_view(), name="finance-summary"),
    path("me/", PartnerFinanceView.as_view(), name="finance-me"),
    path("organization-options/", FinanceOrganizationOptionsView.as_view(), name="finance-organization-options"),
]
