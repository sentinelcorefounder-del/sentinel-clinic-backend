from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase

from finance.models import OrganizationWallet, WalletLedgerEntry, canonical_finance_service_code
from finance.services import eligible_sentinel_treasury_wallets, infer_financial_identity, post_opening_balance
from organizations.models import Organization


class FinanceArchitectureCorrectionTests(TestCase):
    def setUp(self):
        self.treasury = Organization.objects.create(
            clinic_id="SNT-TREASURY", name="Project Sentinel Treasury",
            organization_type="sentinel", is_sentinel_treasury=True,
        )
        self.treasury_wallet = OrganizationWallet.objects.create(organization=self.treasury)
        self.clinic = Organization.objects.create(
            clinic_id="SNT-CLINIC", name="Sentinel Clinic", organization_type="clinic"
        )
        self.clinic_wallet = OrganizationWallet.objects.create(organization=self.clinic)

    def test_clinic_code_does_not_make_clinic_treasury(self):
        self.assertEqual(list(eligible_sentinel_treasury_wallets()), [self.treasury_wallet])
        self.assertNotIn(self.clinic_wallet, eligible_sentinel_treasury_wallets())

    def test_opening_balance_is_idempotent_and_clinic_only(self):
        first = post_opening_balance(
            wallet=self.clinic_wallet, amount="180140.00",
            idempotency_key="sentinel-clinic-opening-balance-v1",
            reference="PRELIVE-OPENING",
        )
        second = post_opening_balance(
            wallet=self.clinic_wallet, amount="180140.00",
            idempotency_key="sentinel-clinic-opening-balance-v1",
            reference="PRELIVE-OPENING",
        )
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(self.clinic_wallet.available_balance, Decimal("180140.00"))
        self.assertEqual(self.treasury_wallet.available_balance, Decimal("0.00"))
        self.assertEqual(
            WalletLedgerEntry.objects.filter(entry_type=WalletLedgerEntry.EntryType.OPENING_BALANCE).count(), 1
        )

    def test_canonical_services_cover_deployed_pathways(self):
        cases = [
            (SimpleNamespace(service_package="diabetic_retinal_assessment", programme="diabetic_screening", encounter_type="retinal_assessment"), "diabetic_retinal_assessment"),
            (SimpleNamespace(service_package="combined_diabetic_eye_health", programme="combined_assessment", encounter_type="combined_assessment"), "combined_diabetic_eye_health"),
            (SimpleNamespace(service_package="eye_health_screening", programme="eye_health_screening", encounter_type="eye_health_screening"), "eye_health_screening"),
            (SimpleNamespace(service_package=None, programme="ocular_diagnostics", encounter_type="ocular_assessment"), "ocular_assessment"),
        ]
        for encounter, expected in cases:
            self.assertEqual(canonical_finance_service_code(encounter), expected)

    def test_patient_collector_is_operational_clinic_not_treasury(self):
        encounter = SimpleNamespace(
            source_type="clinic_direct", payment_responsibility="patient",
            originating_organization=self.clinic,
        )
        _, payer_type, collector_type, payment_method = infer_financial_identity(encounter)
        self.assertEqual(payer_type, "patient")
        self.assertEqual(collector_type, "clinic")
        self.assertEqual(payment_method, "paystack")
