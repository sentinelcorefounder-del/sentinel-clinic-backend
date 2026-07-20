from decimal import Decimal

from django.core.exceptions import ValidationError

from finance.models import OrganizationWallet, WalletLedgerEntry
from finance.tests import FinanceEngineTests
from .models import PaymentTransaction
from .services.posting import post_verified_payment


class PaymentPostingTests(FinanceEngineTests):
    def setUp(self):
        super().setUp()
        self.wallet = OrganizationWallet.objects.create(
            organization=self.organization,
            currency="NGN",
        )

    def _verification(self, reference, amount_kobo):
        return {
            "status": True,
            "data": {
                "status": "success",
                "reference": reference,
                "amount": amount_kobo,
                "currency": "NGN",
            },
        }

    def test_verified_wallet_top_up_posts_once(self):
        payment = PaymentTransaction.objects.create(
            reference="PS-WALLET-001",
            purpose=PaymentTransaction.Purpose.WALLET_TOP_UP,
            email="finance@example.com",
            currency="NGN",
            expected_amount=Decimal("100000.00"),
            wallet=self.wallet,
        )
        payload = self._verification(payment.reference, 10000000)
        post_verified_payment(payment, payload)
        post_verified_payment(payment, payload)
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentTransaction.Status.POSTED)
        self.assertEqual(self.wallet.available_balance, Decimal("100000.00"))
        self.assertEqual(WalletLedgerEntry.objects.filter(reference=payment.reference).count(), 1)

    def test_amount_mismatch_is_rejected(self):
        payment = PaymentTransaction.objects.create(
            reference="PS-WALLET-002",
            purpose=PaymentTransaction.Purpose.WALLET_TOP_UP,
            email="finance@example.com",
            currency="NGN",
            expected_amount=Decimal("100000.00"),
            wallet=self.wallet,
        )
        with self.assertRaises(ValidationError):
            post_verified_payment(payment, self._verification(payment.reference, 9000000))
        self.assertEqual(self.wallet.available_balance, Decimal("0.00"))

    def test_direct_encounter_payment_uses_financial_record_amount(self):
        from finance.services import price_encounter
        record = price_encounter(self.encounter)
        payment = PaymentTransaction.objects.create(
            reference="PS-ENC-001",
            purpose=PaymentTransaction.Purpose.ENCOUNTER_PAYMENT,
            email="patient@example.com",
            currency="NGN",
            expected_amount=record.outstanding_amount,
            financial_record=record,
        )
        post_verified_payment(payment, self._verification(payment.reference, 1500000))
        record.refresh_from_db()
        self.assertEqual(record.outstanding_amount, Decimal("0.00"))
        self.assertTrue(record.financially_releasable)
