from datetime import date
from decimal import Decimal
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from encounters.models import AssessmentServiceSession
from finance.models import (
    FounderFundedExpense,
    OrganizationWallet,
    TreasuryExpenseCategory,
    TreasuryTransfer,
    WalletLedgerEntry,
)
from finance.services import (
    create_founder_funded_expense,
    create_treasury_transfer,
    decide_founder_funded_expense,
    decide_treasury_transfer,
    eligible_sentinel_treasury_wallets,
    record_treasury_transfer_execution,
    submit_founder_funded_expense,
    submit_treasury_transfer,
    top_up_wallet,
)
from organizations.models import Organization, OrganizationBranch
from users.models import UserSecurityProfile


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class OperationalFinanceControlsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.sentinel_clinic = Organization.objects.create(
            clinic_id="SNT-CLINIC", name="Project Sentinel", organization_type="clinic"
        )
        self.sentinel_wallet = OrganizationWallet.objects.create(organization=self.sentinel_clinic)
        self.clinic = Organization.objects.create(
            clinic_id="FIN-CLINIC", name="Ordinary Clinic", organization_type="clinic"
        )
        self.hospital = Organization.objects.create(
            clinic_id="FIN-HOSP", name="Ordinary Hospital", organization_type="hospital"
        )
        self.afri = Organization.objects.create(
            clinic_id="AFRI-FIN", name="Afriophthalmics", organization_type="clinic"
        )
        self.clinic_wallet = OrganizationWallet.objects.create(organization=self.clinic)
        self.hospital_wallet = OrganizationWallet.objects.create(organization=self.hospital)
        self.afri_wallet = OrganizationWallet.objects.create(organization=self.afri)
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="MAIN", name="Main"
        )
        self.operator = self._user("finance-op", "finance_operator", True)
        self.approver = self._user("finance-approve", "finance_approver", True)
        self.viewer = self._user("finance-view", "finance_viewer", True)

    def _user(self, username, role="", internal=False, superuser=False):
        user = get_user_model().objects.create_user(
            username=username, is_superuser=superuser, is_staff=superuser
        )
        if role:
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        UserSecurityProfile.objects.create(user=user, is_internal_sentinel_staff=internal)
        return user

    def _fund(self, amount="20000.00"):
        return top_up_wallet(
            self.sentinel_wallet, amount,
            idempotency_key=f"operational-fund-{amount}",
            reference="SYNTHETIC-FUNDING",
        )

    def _evidence(self, name="evidence.pdf"):
        return SimpleUploadedFile(name, b"synthetic evidence", content_type="application/pdf")

    def test_existing_clinical_project_sentinel_wallet_is_authoritative_without_duplication(self):
        self._fund()
        wallets = list(eligible_sentinel_treasury_wallets())
        self.assertEqual([wallet.pk for wallet in wallets], [self.sentinel_wallet.pk])
        self.assertEqual(self.sentinel_wallet.available_balance, Decimal("20000.00"))
        self.assertEqual(OrganizationWallet.objects.filter(organization=self.sentinel_clinic).count(), 1)
        self.assertEqual(WalletLedgerEntry.objects.filter(wallet=self.sentinel_wallet).count(), 1)
        self.assertNotIn(self.clinic_wallet, wallets)
        self.assertNotIn(self.hospital_wallet, wallets)
        self.assertNotIn(self.afri_wallet, wallets)

    def test_shared_wallet_api_returns_only_eligible_sentinel_treasury_wallet(self):
        self._fund()
        self.client.force_authenticate(self.viewer)
        response = self.client.get("/api/finance/wallets/sentinel-treasury/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data], [self.sentinel_wallet.pk])
        self.assertEqual(response.data[0]["available_balance"], "20000.00")
        self.assertEqual(response.data[0]["reserved_balance"], "0.00")
        self.assertEqual(response.data[0]["transferable_balance"], "20000.00")

    def test_service_session_operator_can_edit_activate_and_activation_posts_nothing(self):
        session = AssessmentServiceSession.objects.create(
            service_date=date(2026, 9, 3), location_type="clinic",
            participating_organization=self.clinic, service_branch=self.branch,
            provider_type="sentinel", created_by=self.operator,
        )
        self.client.force_authenticate(self.operator)
        edit = self.client.patch(
            f"/api/finance/internal/service-sessions/{session.pk}/",
            {"camera_team_rate": "5500.00", "logistics_allocation_rate": "2000.00"},
            format="json",
        )
        self.assertEqual(edit.status_code, 200)
        activate = self.client.post(
            f"/api/finance/internal/service-sessions/{session.pk}/activate/", {}, format="json"
        )
        self.assertEqual(activate.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, AssessmentServiceSession.Status.ACTIVE)
        self.assertEqual(WalletLedgerEntry.objects.count(), 0)
        retry = self.client.post(
            f"/api/finance/internal/service-sessions/{session.pk}/activate/", {}, format="json"
        )
        self.assertEqual(retry.status_code, 200)
        stale_edit = self.client.patch(
            f"/api/finance/internal/service-sessions/{session.pk}/",
            {"camera_team_rate": "6000.00"}, format="json",
        )
        self.assertEqual(stale_edit.status_code, 400)

    def test_service_session_wrong_roles_and_superuser_only_are_denied(self):
        session = AssessmentServiceSession.objects.create(
            service_date=date(2026, 9, 3), location_type="clinic",
            participating_organization=self.clinic, service_branch=self.branch,
            provider_type="sentinel", created_by=self.operator,
        )
        for user in (
            self._user("generic-admin", "ops_admin", True),
            self._user("clinic-user", "clinic_admin", False),
            self._user("root-only", "", False, superuser=True),
        ):
            self.client.force_authenticate(user)
            response = self.client.post(
                f"/api/finance/internal/service-sessions/{session.pk}/activate/", {}, format="json"
            )
            self.assertEqual(response.status_code, 403)

    def test_every_treasury_category_persists_and_approval_does_not_debit(self):
        self._fund()
        for index, (category, _label) in enumerate(TreasuryExpenseCategory.choices):
            transfer = create_treasury_transfer(
                wallet=self.sentinel_wallet, amount="100.00", category=category,
                purpose=f"Synthetic category {category}", destination_label="Synthetic destination",
                idempotency_key=f"category-{index}", actor=self.operator,
            )
            self.assertEqual(transfer.category, category)
        transfer = submit_treasury_transfer(
            create_treasury_transfer(
                wallet=self.sentinel_wallet, amount="1000.00",
                category=TreasuryExpenseCategory.HOSTING_SOFTWARE,
                purpose="Synthetic hosting", destination_label="Synthetic vendor",
                idempotency_key="approval-no-debit", actor=self.operator,
            ), actor=self.operator,
        )
        before = self.sentinel_wallet.available_balance
        transfer = decide_treasury_transfer(transfer, actor=self.approver, approve=True)
        self.assertEqual(self.sentinel_wallet.available_balance, before)
        with self.assertRaisesMessage(ValidationError, "creator cannot decide"):
            own = submit_treasury_transfer(create_treasury_transfer(
                wallet=self.sentinel_wallet, amount="100.00", purpose="Own approval",
                destination_label="Synthetic", idempotency_key="own-approval", actor=self.operator,
            ), actor=self.operator)
            decide_treasury_transfer(own, actor=self.operator, approve=True)

    def test_execution_debits_once_and_insufficient_transferable_funds_are_rejected(self):
        self._fund("2000.00")
        transfer = decide_treasury_transfer(
            submit_treasury_transfer(create_treasury_transfer(
                wallet=self.sentinel_wallet, amount="1500.00", purpose="Synthetic transfer",
                destination_label="Synthetic destination", idempotency_key="execute-once",
                actor=self.operator,
            ), actor=self.operator), actor=self.approver, approve=True,
        )
        executed = record_treasury_transfer_execution(
            transfer, actor=self.operator, execution_date=date.today(), external_reference="BANK-1", evidence=self._evidence()
        )
        repeated = record_treasury_transfer_execution(
            executed, actor=self.operator, execution_date=date.today(), external_reference="BANK-1", evidence=self._evidence("retry.pdf")
        )
        self.assertEqual(repeated.ledger_entry_id, executed.ledger_entry_id)
        self.assertEqual(self.sentinel_wallet.available_balance, Decimal("500.00"))
        self.assertEqual(
            WalletLedgerEntry.objects.filter(idempotency_key=f"treasury-transfer:{transfer.pk}:executed").count(), 1
        )
        too_large = submit_treasury_transfer(create_treasury_transfer(
            wallet=self.sentinel_wallet, amount="600.00", purpose="Too large",
            destination_label="Synthetic", idempotency_key="insufficient", actor=self.operator,
        ), actor=self.operator)
        with self.assertRaisesMessage(ValidationError, "transferable surplus"):
            decide_treasury_transfer(too_large, actor=self.approver, approve=True)

    def test_founder_expense_recording_has_no_wallet_effect_and_contribution_is_not_repayable(self):
        self._fund()
        before = self.sentinel_wallet.available_balance
        contribution = create_founder_funded_expense(
            expense_date=date(2026, 9, 3), category=TreasuryExpenseCategory.HOSTING_SOFTWARE,
            supplier_payee="Synthetic SaaS", description="Synthetic hosting cost", amount="1200.00",
            currency="NGN", evidence=self._evidence(),
            funding_treatment=FounderFundedExpense.FundingTreatment.CONTRIBUTION,
            idempotency_key="founder-contribution", actor=self.operator,
        )
        self.assertEqual(self.sentinel_wallet.available_balance, before)
        contribution = decide_founder_funded_expense(
            submit_founder_funded_expense(contribution, actor=self.operator),
            actor=self.approver, approve=True,
        )
        with self.assertRaisesMessage(ValidationError, "not reimbursable"):
            create_treasury_transfer(
                wallet=self.sentinel_wallet, amount=contribution.amount,
                category=TreasuryExpenseCategory.FOUNDER_REIMBURSEMENT,
                purpose="Invalid reimbursement", destination_label="Founder",
                founder_expense=contribution, idempotency_key="invalid-contribution-repay",
                actor=self.operator,
            )

    def test_founder_reimbursement_settles_full_amount_exactly_once(self):
        self._fund()
        expense = create_founder_funded_expense(
            expense_date=date(2026, 9, 3), category=TreasuryExpenseCategory.FIELD_OPERATIONS,
            supplier_payee="Founder", description="Synthetic field cost", amount="1500.00",
            currency="NGN", evidence=self._evidence(),
            funding_treatment=FounderFundedExpense.FundingTreatment.REIMBURSABLE,
            idempotency_key="founder-reimbursable", actor=self.operator,
        )
        expense = decide_founder_funded_expense(
            submit_founder_funded_expense(expense, actor=self.operator),
            actor=self.approver, approve=True,
        )
        with self.assertRaisesMessage(ValidationError, "settle the approved expense in full"):
            create_treasury_transfer(
                wallet=self.sentinel_wallet, amount="1000.00",
                category=TreasuryExpenseCategory.FOUNDER_REIMBURSEMENT,
                purpose="Partial reimbursement", destination_label="Founder",
                founder_expense=expense, idempotency_key="partial-founder-repay", actor=self.operator,
            )
        transfer = decide_treasury_transfer(
            submit_treasury_transfer(create_treasury_transfer(
                wallet=self.sentinel_wallet, amount=expense.amount,
                category=TreasuryExpenseCategory.FOUNDER_REIMBURSEMENT,
                purpose="Full reimbursement", destination_label="Founder",
                founder_expense=expense, idempotency_key="full-founder-repay", actor=self.operator,
            ), actor=self.operator), actor=self.approver, approve=True,
        )
        transfer = record_treasury_transfer_execution(
            transfer, actor=self.operator, execution_date=date.today(), external_reference="BANK-FOUNDER", evidence=self._evidence("bank.pdf")
        )
        expense.refresh_from_db()
        self.assertEqual(expense.status, FounderFundedExpense.Status.SETTLED)
        self.assertEqual(expense.reimbursement_transfers.filter(status=TreasuryTransfer.Status.EXECUTED).count(), 1)
        record_treasury_transfer_execution(
            transfer, actor=self.operator, execution_date=date.today(), external_reference="BANK-FOUNDER", evidence=self._evidence("bank-retry.pdf")
        )
        self.assertEqual(expense.reimbursement_transfers.filter(status=TreasuryTransfer.Status.EXECUTED).count(), 1)
