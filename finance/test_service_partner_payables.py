from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from encounters.models import AssessmentServiceSession, ScreeningEncounter
from finance.models import (
    EncounterFinancialRecord, FinanceControlAudit, ServicePartnerEarning,
    ServicePartnerSettlementBatch, ServicePartnerAdjustment,
    ServicePartnerCorrectionRequest, OrganizationWallet,
)
from finance.services import (
    attach_encounter_to_service_session, cancel_service_partner_settlement,
    decide_service_partner_settlement, mark_service_partner_settlement_paid,
    prepare_service_partner_settlement, recognize_service_partner_earning,
    service_partner_payable_summary,
    request_service_partner_correction, decide_service_partner_correction,
    refund_to_wallet,
)
from organizations.models import Organization, OrganizationBranch
from patients.models import Patient
from reports.models import StructuredReport
from users.models import UserSecurityProfile


class ServicePartnerPayablesTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = self.user("pay-admin", "finance_admin")
        self.operator = self.user("pay-operator", "finance_operator")
        self.approver = self.user("pay-approver", "finance_approver")
        self.clinic = Organization.objects.create(
            clinic_id="PAY-CLINIC", name="Payables Clinic", organization_type="clinic",
        )
        self.partner = Organization.objects.create(
            clinic_id="PAY-PARTNER", name="Synthetic Camera Partner",
            organization_type="service_partner",
        )
        self.other_partner = Organization.objects.create(
            clinic_id="PAY-OTHER", name="Other Partner", organization_type="service_partner",
        )
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="PAY", name="Payables Branch",
        )

    def user(self, username, role):
        user = get_user_model().objects.create_user(username)
        user.groups.add(Group.objects.get(name=role))
        UserSecurityProfile.objects.create(user=user, is_internal_sentinel_staff=True)
        return user

    def eligible_record(self, *, provider="service_partner", rate="5000.00", issued=True, paid=True, suffix="1"):
        session = AssessmentServiceSession.objects.create(
            service_date=date(2026, 8, 14), location_type="clinic",
            participating_organization=self.clinic, service_branch=self.branch,
            provider_type=provider,
            service_partner=self.partner if provider == "service_partner" else None,
            camera_team_rate=Decimal(rate), currency="NGN", status="active",
            created_by=self.admin, activated_by=self.admin, activated_at=timezone.now(),
        )
        patient = Patient.objects.create(
            patient_id=f"PAY-PAT-{suffix}", first_name="Synthetic", last_name="Only",
            date_of_birth=date(1980, 1, 1), sex="female", assigned_clinic=self.clinic,
        )
        encounter = ScreeningEncounter.objects.create(
            encounter_id=f"PAY-ENC-{suffix}", patient=patient,
            encounter_date=session.service_date, source_type="clinic_direct",
            payment_responsibility="clinic", originating_organization=self.clinic,
            service_branch=self.branch,
        )
        attach_encounter_to_service_session(encounter, session, self.admin)
        report = StructuredReport.objects.create(
            report_id=f"PAY-RPT-{suffix}", encounter=encounter, patient=patient,
            review_date=date.today(), ungradable=True, urgency_outcome="image_retake",
            report_status="issued" if issued else "draft",
        )
        record, _ = EncounterFinancialRecord.objects.get_or_create(encounter=encounter)
        record.status = "captured" if paid else "awaiting_payment"
        record.financially_releasable = paid
        record.captured_at = timezone.now() if paid else None
        record.gross_amount = Decimal("15000.00")
        record.allocated_amount = Decimal("15000.00")
        record.outstanding_amount = Decimal("0.00") if paid else Decimal("15000.00")
        record.currency = "NGN"
        record.service_pathway = "clinic_direct"
        record.save()
        return record, report, session

    def test_eligibility_snapshot_rate_and_exactly_once(self):
        record, _, session = self.eligible_record()
        first = recognize_service_partner_earning(record, "test")
        second = recognize_service_partner_earning(record, "retry")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ServicePartnerEarning.objects.count(), 1)
        self.assertEqual(first.amount, Decimal("5000.00"))
        self.assertEqual(first.service_partner, self.partner)
        session.camera_team_rate = Decimal("9000.00")
        self.assertEqual(first.amount, Decimal("5000.00"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ServicePartnerEarning.objects.create(
                financial_record=record, encounter=record.encounter,
                service_partner=self.other_partner, service_session=session,
                assessment_date=date.today(), session_reference="duplicate",
                provider_type="service_partner", amount=1, currency="NGN",
                rate_snapshot={}, trigger_source="bad", earned_at=timezone.now(),
            )

    def test_no_premature_or_sentinel_earning(self):
        unpaid, _, _ = self.eligible_record(paid=False, suffix="UNPAID")
        unissued, report, _ = self.eligible_record(issued=False, suffix="DRAFT")
        sentinel, _, _ = self.eligible_record(provider="sentinel", suffix="SENTINEL")
        self.assertIsNone(recognize_service_partner_earning(unpaid))
        self.assertIsNone(recognize_service_partner_earning(unissued))
        self.assertIsNone(recognize_service_partner_earning(sentinel))
        unissued.financially_releasable = True
        report.report_status = "issued"
        report.save(update_fields=["report_status"])
        self.assertIsNotNone(recognize_service_partner_earning(unissued, "release_after_payment"))

    def test_daily_workflow_totals_replacement_and_idempotent_paid(self):
        for suffix in ("A", "B"):
            record, _, _ = self.eligible_record(suffix=suffix)
            recognize_service_partner_earning(record)
        summary = list(service_partner_payable_summary())[0]
        self.assertEqual(summary["total_earned"], Decimal("10000.00"))
        first = prepare_service_partner_settlement(
            service_partner=self.partner, assessment_date=date(2026, 8, 14), actor=self.operator,
        )
        self.assertEqual(first.assessment_count, 2)
        with self.assertRaises(ValidationError):
            prepare_service_partner_settlement(
                service_partner=self.partner, assessment_date=date(2026, 8, 14), actor=self.operator,
            )
        cancel_service_partner_settlement(first, actor=self.operator, reason="Replace batch")
        replacement = prepare_service_partner_settlement(
            service_partner=self.partner, assessment_date=date(2026, 8, 14), actor=self.operator,
        )
        with self.assertRaises(ValidationError):
            decide_service_partner_settlement(replacement, actor=self.operator, approve=True)
        decide_service_partner_settlement(replacement, actor=self.approver, approve=True)
        paid = mark_service_partner_settlement_paid(
            replacement, actor=self.operator, payment_date=date.today(), external_reference="PAY-EXT-1",
        )
        repeated = mark_service_partner_settlement_paid(
            paid, actor=self.operator, payment_date=date.today(), external_reference="PAY-EXT-1",
        )
        self.assertEqual(paid.pk, repeated.pk)
        self.assertEqual(ServicePartnerSettlementBatch.objects.filter(status="paid").count(), 1)
        summary = list(service_partner_payable_summary())[0]
        self.assertEqual(summary["total_paid"], Decimal("10000.00"))
        self.assertEqual(summary["outstanding"], Decimal("0.00"))

    def test_audit_failure_rolls_back_prepare(self):
        record, _, _ = self.eligible_record()
        recognize_service_partner_earning(record)
        with patch.object(FinanceControlAudit.objects, "create", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                prepare_service_partner_settlement(
                    service_partner=self.partner, assessment_date=date(2026, 8, 14), actor=self.operator,
                )
        self.assertFalse(ServicePartnerSettlementBatch.objects.exists())
        self.assertEqual(ServicePartnerEarning.objects.get().status, "available")

    def test_api_permissions_and_no_patient_or_storage_data(self):
        record, _, _ = self.eligible_record()
        recognize_service_partner_earning(record)
        self.client.force_authenticate(self.approver)
        response = self.client.get("/api/finance/internal/service-partner-payables/")
        self.assertEqual(response.status_code, 200)
        payload = str(response.data).lower()
        self.assertNotIn("pay-pat-", payload)
        self.assertNotIn("last_name", payload)
        self.assertNotIn("date_of_birth", payload)
        self.assertNotIn("payment_evidence", payload)
        denied = self.client.post(
            "/api/finance/internal/service-partner-payables/prepare/",
            {"service_partner": self.partner.pk, "assessment_date": "2026-08-14"}, format="json",
        )
        self.assertEqual(denied.status_code, 403)

    def test_normal_refund_preserves_valid_earning_and_paid_settlement(self):
        record, _, _ = self.eligible_record()
        earning = recognize_service_partner_earning(record)
        batch = prepare_service_partner_settlement(
            service_partner=self.partner, assessment_date=date(2026, 8, 14), actor=self.operator,
        )
        decide_service_partner_settlement(batch, actor=self.approver, approve=True)
        mark_service_partner_settlement_paid(
            batch, actor=self.operator, payment_date=date.today(), external_reference="REFUND-PAID-1",
        )
        wallet = OrganizationWallet.objects.create(organization=self.clinic, currency="NGN")
        refund_to_wallet(
            wallet, Decimal("15000.00"), "normal-customer-refund", financial_record=record,
            actor=self.operator,
        )
        earning.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(earning.amount, Decimal("5000.00"))
        self.assertEqual(earning.status, ServicePartnerEarning.Status.PAID)
        self.assertEqual(batch.status, ServicePartnerSettlementBatch.Status.PAID)
        self.assertEqual(batch.final_amount, Decimal("5000.00"))
        self.assertFalse(ServicePartnerAdjustment.objects.exists())

    def test_invalid_unpaid_earning_uses_approved_append_only_offset(self):
        record, _, _ = self.eligible_record()
        earning = recognize_service_partner_earning(record)
        correction = request_service_partner_correction(
            earning=earning, amount="5000.00", reason="Invalid assessment", actor=self.operator,
        )
        decided = decide_service_partner_correction(correction, actor=self.approver, approve=True)
        earning.refresh_from_db()
        adjustment = decided.adjustment
        self.assertEqual(earning.amount, Decimal("5000.00"))
        self.assertEqual(earning.status, ServicePartnerEarning.Status.REVERSED)
        self.assertEqual(adjustment.amount, Decimal("-5000.00"))
        self.assertEqual(adjustment.status, ServicePartnerAdjustment.Status.OFFSET_WITHOUT_PAYMENT)
        summary = list(service_partner_payable_summary())[0]
        self.assertEqual(summary["outstanding"], Decimal("0.00"))

    def test_paid_invalid_earning_carries_adjustment_without_rewriting_history(self):
        record, _, _ = self.eligible_record()
        earning = recognize_service_partner_earning(record)
        original = prepare_service_partner_settlement(
            service_partner=self.partner, assessment_date=date(2026, 8, 14), actor=self.operator,
        )
        decide_service_partner_settlement(original, actor=self.approver, approve=True)
        mark_service_partner_settlement_paid(
            original, actor=self.operator, payment_date=date.today(), external_reference="ORIGINAL-PAID",
        )
        correction = request_service_partner_correction(
            earning=earning, amount="5000.00", reason="Wrong partner attribution", actor=self.operator,
        )
        decide_service_partner_correction(correction, actor=self.approver, approve=True)
        adjustment = ServicePartnerAdjustment.objects.get()
        original.refresh_from_db()
        earning.refresh_from_db()
        self.assertEqual(original.status, ServicePartnerSettlementBatch.Status.PAID)
        self.assertEqual(original.final_amount, Decimal("5000.00"))
        self.assertEqual(earning.amount, Decimal("5000.00"))
        self.assertEqual(adjustment.status, ServicePartnerAdjustment.Status.AVAILABLE)
        summary = list(service_partner_payable_summary())[0]
        self.assertEqual(summary["total_paid"], Decimal("5000.00"))
        self.assertEqual(summary["carried_forward_adjustment"], Decimal("-5000.00"))
        self.assertEqual(summary["outstanding"], Decimal("-5000.00"))

    def test_correction_roles_maker_checker_and_idempotent_approval(self):
        record, _, _ = self.eligible_record()
        earning = recognize_service_partner_earning(record)
        correction = request_service_partner_correction(
            earning=earning, amount="1000.00", reason="Rate correction", actor=self.operator,
        )
        with self.assertRaises(ValidationError):
            decide_service_partner_correction(correction, actor=self.operator, approve=True)
        decide_service_partner_correction(correction, actor=self.approver, approve=True)
        decide_service_partner_correction(correction, actor=self.approver, approve=True)
        self.assertEqual(ServicePartnerAdjustment.objects.count(), 1)
        self.client.force_authenticate(self.approver)
        denied = self.client.post(
            "/api/finance/internal/service-partner-payables/request-correction/",
            {"earning": earning.pk, "amount": "100.00", "reason": "Not allowed"}, format="json",
        )
        self.assertEqual(denied.status_code, 403)
        superuser = get_user_model().objects.create_superuser("pay-root", "root@example.test", "x")
        UserSecurityProfile.objects.create(user=superuser, is_internal_sentinel_staff=True)
        self.client.force_authenticate(superuser)
        bypass = self.client.post(
            "/api/finance/internal/service-partner-payables/request-correction/",
            {"earning": earning.pk, "amount": "100.00", "reason": "No bypass"}, format="json",
        )
        self.assertEqual(bypass.status_code, 403)

    def test_adjustments_reduce_future_batch_and_never_create_negative_cash_batch(self):
        paid_record, _, _ = self.eligible_record(suffix="PAID-CORR")
        paid_earning = recognize_service_partner_earning(paid_record)
        paid_batch = prepare_service_partner_settlement(
            service_partner=self.partner, assessment_date=date(2026, 8, 14), actor=self.operator,
        )
        decide_service_partner_settlement(paid_batch, actor=self.approver, approve=True)
        mark_service_partner_settlement_paid(
            paid_batch, actor=self.operator, payment_date=date.today(), external_reference="PAID-CORR",
        )
        correction = request_service_partner_correction(
            earning=paid_earning, amount="5000.00", reason="Invalid paid earning", actor=self.operator,
        )
        decide_service_partner_correction(correction, actor=self.approver, approve=True)
        future_record, _, _ = self.eligible_record(rate="7000.00", suffix="FUTURE")
        future_record.encounter.encounter_date = date(2026, 8, 14)
        recognize_service_partner_earning(future_record)
        replacement = prepare_service_partner_settlement(
            service_partner=self.partner, assessment_date=date(2026, 8, 14), actor=self.operator,
        )
        self.assertEqual(replacement.gross_amount, Decimal("2000.00"))
        self.assertEqual(replacement.final_amount, Decimal("2000.00"))
        self.assertTrue(replacement.items.filter(adjustment__isnull=False, amount=Decimal("-5000.00")).exists())

    def test_correction_audit_failure_rolls_back_request_and_posting(self):
        record, _, _ = self.eligible_record()
        earning = recognize_service_partner_earning(record)
        with patch.object(FinanceControlAudit.objects, "create", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                request_service_partner_correction(
                    earning=earning, amount="5000.00", reason="Invalid", actor=self.operator,
                )
        self.assertFalse(ServicePartnerCorrectionRequest.objects.exists())
        correction = request_service_partner_correction(
            earning=earning, amount="5000.00", reason="Invalid", actor=self.operator,
        )
        with patch.object(FinanceControlAudit.objects, "create", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaises(RuntimeError):
                decide_service_partner_correction(correction, actor=self.approver, approve=True)
        correction.refresh_from_db()
        self.assertEqual(correction.status, ServicePartnerCorrectionRequest.Status.PENDING)
        self.assertFalse(ServicePartnerAdjustment.objects.exists())
