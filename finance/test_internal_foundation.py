import shutil
import tempfile
from unittest.mock import patch
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib import admin as django_admin
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from encounters.models import AssessmentServiceSession, ScreeningEncounter
from finance.models import (
    BankTransferFundingRequest, EncounterAllocation, FinanceControlAudit,
    OrganizationWallet, SettlementBatch, WalletLedgerEntry,
)
from finance.services import attach_encounter_to_service_session
from organizations.models import Organization, OrganizationBranch, OrganizationProfile
from patients.models import Patient
from referrals.models import HospitalReferral
from users.models import UserOrganization, UserSecurityProfile
from users.admin import UserSecurityProfileAdmin


TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="sentinel-finance-evidence-")


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class InternalFinanceFoundationTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.client = APIClient()
        self.admin_group = Group.objects.get(name="finance_admin")
        self.operator_group = Group.objects.get(name="finance_operator")
        self.approver_group = Group.objects.get(name="finance_approver")
        self.admin = get_user_model().objects.create_user("finance-foundation-admin")
        self.admin.groups.add(self.admin_group)
        UserSecurityProfile.objects.create(
            user=self.admin, is_internal_sentinel_staff=True
        )
        self.clinic = Organization.objects.create(
            clinic_id="CLINIC-FOUNDATION", name="Foundation Clinic",
            organization_type="clinic",
        )
        self.hospital = Organization.objects.create(
            clinic_id="HOSP-FOUNDATION", name="Foundation Hospital",
            organization_type="hospital",
        )
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="MAIN", name="Main"
        )
        self.partner = Organization.objects.create(
            clinic_id="PARTNER-FOUNDATION", name="Foundation Partner",
            organization_type="service_partner",
        )

    def session(self, **overrides):
        values = {
            "service_date": date(2026, 8, 14),
            "location_type": "clinic",
            "participating_organization": self.clinic,
            "service_branch": self.branch,
            "provider_type": "service_partner",
            "service_partner": self.partner,
            "created_by": self.admin,
        }
        values.update(overrides)
        return AssessmentServiceSession.objects.create(**values)

    def encounter(self):
        patient = Patient.objects.create(
            patient_id="PAT-FOUNDATION", first_name="Synthetic", last_name="Patient",
            date_of_birth=date(1980, 1, 1), sex="female", assigned_clinic=self.clinic,
        )
        return ScreeningEncounter.objects.create(
            encounter_id="ENC-FOUNDATION", patient=patient,
            encounter_date=date(2026, 8, 14), source_type="clinic_direct",
            payment_responsibility="clinic", originating_organization=self.clinic,
            service_branch=self.branch,
        )

    def authenticate(self, user=None):
        self.client.force_authenticate(user=user or self.admin)

    def test_existing_types_and_non_login_service_partner_are_valid(self):
        self.assertEqual(self.clinic.organization_type, "clinic")
        self.assertEqual(self.hospital.organization_type, "hospital")
        self.assertFalse(UserOrganization.objects.filter(organization=self.partner).exists())
        self.assertFalse(OrganizationWallet.objects.filter(organization=self.partner).exists())
        self.assertFalse(OrganizationProfile.objects.filter(organization=self.partner).exists())
        self.assertFalse(OrganizationBranch.objects.filter(organization=self.partner).exists())

    def test_service_partner_is_excluded_from_clinical_organization_api(self):
        superuser = get_user_model().objects.create_superuser("clinical-root", "root@example.test", "x")
        self.client.force_authenticate(superuser)
        response = self.client.get("/api/organizations/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.partner.id, {item["id"] for item in response.data})
        self.assertEqual(self.client.get(f"/api/organizations/{self.partner.id}/").status_code, 404)

    def test_referral_sync_matches_only_active_clinics_by_code(self):
        patient = Patient.objects.create(
            patient_id="PAT-REF-CODE", first_name="Referral", last_name="Patient",
            date_of_birth=date(1980, 1, 1), sex="female", assigned_clinic=self.hospital,
        )
        referral = HospitalReferral.objects.create(
            referral_id="REF-CLINIC-CODE", source_hospital=self.hospital, patient=patient,
            first_name="Referral", last_name="Patient", reason_for_referral="Assessment",
        )
        with patch.dict("os.environ", {"SENTINEL_SYNC_TOKEN": "foundation-token"}):
            response = self.client.post(
                "/api/referrals/hospital/sync-status/",
                {"referral_id": referral.referral_id, "matched_clinic_code": self.clinic.clinic_id},
                format="json", HTTP_X_SENTINEL_SYNC_TOKEN="foundation-token",
            )
        self.assertEqual(response.status_code, 200)
        referral.refresh_from_db()
        patient.refresh_from_db()
        self.assertEqual(referral.matched_clinic, self.clinic)
        self.assertEqual(patient.assigned_clinic, self.clinic)

    def test_referral_sync_ignores_service_partner_code_and_name(self):
        patient = Patient.objects.create(
            patient_id="PAT-REF-PARTNER", first_name="Referral", last_name="Protected",
            date_of_birth=date(1980, 1, 1), sex="female", assigned_clinic=self.clinic,
        )
        referral = HospitalReferral.objects.create(
            referral_id="REF-PARTNER-GUARD", source_hospital=self.hospital, patient=patient,
            first_name="Referral", last_name="Protected", reason_for_referral="Assessment",
        )
        with patch.dict("os.environ", {"SENTINEL_SYNC_TOKEN": "foundation-token"}):
            for payload in (
                {"matched_clinic_code": self.partner.clinic_id},
                {"matched_clinic_name": self.partner.name},
            ):
                response = self.client.post(
                    "/api/referrals/hospital/sync-status/",
                    {"referral_id": referral.referral_id, **payload},
                    format="json", HTTP_X_SENTINEL_SYNC_TOKEN="foundation-token",
                )
                self.assertEqual(response.status_code, 200)
        referral.refresh_from_db()
        patient.refresh_from_db()
        self.assertIsNone(referral.matched_clinic)
        self.assertEqual(patient.assigned_clinic, self.clinic)

    def test_patient_sync_accepts_active_clinic_and_rejects_non_clinical_types(self):
        base = {
            "patient_id": "PAT-SYNC-FOUNDATION", "first_name": "Sync", "last_name": "Patient",
            "date_of_birth": "1980-01-01", "sex": "female",
        }
        sentinel = Organization.objects.create(
            clinic_id="SENTINEL-FOUNDATION", name="Sentinel Foundation",
            organization_type="sentinel",
        )
        inactive = Organization.objects.create(
            clinic_id="INACTIVE-FOUNDATION", name="Inactive Foundation Clinic",
            organization_type="clinic", is_active=False,
        )
        with patch.dict("os.environ", {"SENTINEL_SYNC_TOKEN": "foundation-token"}):
            valid = self.client.post(
                "/api/patients/sync/", {**base, "assigned_clinic_id": self.clinic.clinic_id},
                format="json", HTTP_X_SENTINEL_SYNC_TOKEN="foundation-token",
            )
            self.assertEqual(valid.status_code, 200)
            for organization in (self.partner, self.hospital, sentinel, inactive):
                rejected = self.client.post(
                    "/api/patients/sync/",
                    {**base, "assigned_clinic_id": organization.clinic_id},
                    format="json", HTTP_X_SENTINEL_SYNC_TOKEN="foundation-token",
                )
                self.assertEqual(rejected.status_code, 400)
        self.assertEqual(
            Patient.objects.get(patient_id=base["patient_id"]).assigned_clinic,
            self.clinic,
        )

    def test_service_partner_wallet_creation_and_reassignment_are_rejected(self):
        self.authenticate()
        clinic_response = self.client.post(
            "/api/finance/wallets/", {"organization": self.clinic.id, "currency": "NGN"},
            format="json",
        )
        hospital_response = self.client.post(
            "/api/finance/wallets/", {"organization": self.hospital.id, "currency": "NGN"},
            format="json",
        )
        self.assertEqual(clinic_response.status_code, 201)
        self.assertEqual(hospital_response.status_code, 201)
        rejected = self.client.post(
            "/api/finance/wallets/", {"organization": self.partner.id, "currency": "NGN"},
            format="json",
        )
        self.assertEqual(rejected.status_code, 400)
        wallet = OrganizationWallet.objects.get(pk=clinic_response.data["id"])
        reassigned = self.client.patch(
            f"/api/finance/wallets/{wallet.id}/", {"organization": self.partner.id},
            format="json",
        )
        self.assertEqual(reassigned.status_code, 400)
        wallet.refresh_from_db()
        self.assertEqual(wallet.organization, self.clinic)
        self.assertFalse(OrganizationWallet.objects.filter(organization=self.partner).exists())
        self.assertFalse(WalletLedgerEntry.objects.filter(wallet__organization=self.partner).exists())

    def test_clinical_provisioning_and_branch_endpoints_cannot_create_partner_resources(self):
        ops = get_user_model().objects.create_user("foundation-ops")
        ops.groups.add(Group.objects.get_or_create(name="ops_admin")[0])
        self.client.force_authenticate(ops)
        response = self.client.post(
            "/api/ops/organizations/create/",
            {"organization_type": "service_partner", "org_code": "FORBIDDEN", "name": "Forbidden"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Organization.objects.filter(clinic_id="FORBIDDEN").exists())
        response = self.client.post(
            f"/api/organizations/{self.partner.id}/branches/",
            {"branch_code": "NO", "name": "No branch"}, format="json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(OrganizationBranch.objects.filter(organization=self.partner).exists())

    def test_only_explicit_internal_finance_admin_can_configure_partners(self):
        url = "/api/finance/internal/service-partners/"
        payload = {"clinic_id": "PARTNER-NEW", "name": "New Partner", "currency": "NGN"}
        denied_roles = [
            "ops_admin", "sentinel_ops", "finance_viewer", "finance_operator",
            "finance_approver",
        ]
        for index, role in enumerate(denied_roles):
            user = get_user_model().objects.create_user(f"denied-{index}")
            user.groups.add(Group.objects.get_or_create(name=role)[0])
            UserSecurityProfile.objects.create(
                user=user, is_internal_sentinel_staff=True
            )
            self.client.force_authenticate(user)
            self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)
        superuser = get_user_model().objects.create_superuser("internal-root", "internal@example.test", "x")
        self.client.force_authenticate(superuser)
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)
        UserSecurityProfile.objects.create(
            user=superuser, is_internal_sentinel_staff=True
        )
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)
        linked_admin = get_user_model().objects.create_user("linked-finance-admin")
        linked_admin.groups.add(self.admin_group)
        UserOrganization.objects.create(user=linked_admin, organization=self.clinic)
        self.client.force_authenticate(linked_admin)
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)
        UserSecurityProfile.objects.create(
            user=linked_admin, is_internal_sentinel_staff=True
        )
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, 201)
        created = Organization.objects.get(clinic_id="PARTNER-NEW")
        self.assertEqual(created.organization_type, "service_partner")
        self.assertFalse(UserOrganization.objects.filter(organization=created).exists())

    def test_service_partner_endpoint_does_not_allow_delete(self):
        self.authenticate()
        self.assertEqual(
            self.client.delete(f"/api/finance/internal/service-partners/{self.partner.id}/").status_code,
            405,
        )

    def test_only_superuser_can_edit_internal_staff_marker_in_admin(self):
        model_admin = UserSecurityProfileAdmin(
            UserSecurityProfile, django_admin.site
        )
        request = RequestFactory().get("/admin/users/usersecurityprofile/")
        ordinary_staff = get_user_model().objects.create_user(
            "ordinary-admin", is_staff=True
        )
        request.user = ordinary_staff
        self.assertIn(
            "is_internal_sentinel_staff",
            model_admin.get_readonly_fields(request),
        )
        request.user = get_user_model().objects.create_superuser(
            "profile-root", "profile-root@example.test", "x"
        )
        self.assertNotIn(
            "is_internal_sentinel_staff",
            model_admin.get_readonly_fields(request),
        )

    def test_session_provider_participant_and_branch_validation(self):
        with self.assertRaises(ValidationError):
            self.session(provider_type="sentinel", service_partner=self.partner)
        with self.assertRaises(ValidationError):
            self.session(participating_organization=self.partner, service_branch=None)
        other_branch = OrganizationBranch.objects.create(
            organization=self.hospital, branch_code="OTHER", name="Other"
        )
        with self.assertRaises(ValidationError):
            self.session(service_branch=other_branch)
        inactive = Organization.objects.create(
            clinic_id="PARTNER-INACTIVE", name="Inactive Partner",
            organization_type="service_partner", is_active=False,
        )
        with self.assertRaises(ValidationError):
            self.session(service_partner=inactive)

    def test_draft_edit_then_terms_freeze_after_activation(self):
        session = self.session()
        session.camera_team_rate = Decimal("5500.00")
        session.save()
        session.status = AssessmentServiceSession.Status.ACTIVE
        session.activated_by = self.admin
        session.save()
        session.camera_team_rate = Decimal("6000.00")
        with self.assertRaisesMessage(ValidationError, "frozen"):
            session.save()

    def test_completed_and_cancelled_sessions_cannot_reopen(self):
        for terminal in (AssessmentServiceSession.Status.COMPLETED, AssessmentServiceSession.Status.CANCELLED):
            session = self.session()
            if terminal == AssessmentServiceSession.Status.COMPLETED:
                session.status = AssessmentServiceSession.Status.ACTIVE
                session.save()
            session.status = terminal
            session.save()
            session.status = AssessmentServiceSession.Status.ACTIVE
            with self.assertRaisesMessage(ValidationError, "cannot be reopened"):
                session.save()

    def test_historical_encounter_unassigned_and_snapshot_attachment_is_immutable_idempotent(self):
        encounter = self.encounter()
        session = self.session(status=AssessmentServiceSession.Status.ACTIVE)
        self.assertIsNone(encounter.service_session_id)
        allocations_before = EncounterAllocation.objects.count()
        ledger_before = WalletLedgerEntry.objects.count()
        attached = attach_encounter_to_service_session(encounter, session, self.admin)
        snapshot = dict(attached.service_delivery_snapshot)
        attached_again = attach_encounter_to_service_session(attached, session, self.admin)
        self.assertEqual(attached_again.service_delivery_snapshot, snapshot)
        attached.service_delivery_snapshot = {"tampered": True}
        with self.assertRaisesMessage(ValidationError, "immutable"):
            attached.save()
        attached.service_delivery_snapshot = snapshot
        other = self.session(
            status=AssessmentServiceSession.Status.ACTIVE,
            provider_type="sentinel", service_partner=None,
        )
        with self.assertRaisesMessage(ValidationError, "immutable"):
            attach_encounter_to_service_session(attached, other, self.admin)
        self.assertEqual(EncounterAllocation.objects.count(), allocations_before)
        self.assertEqual(WalletLedgerEntry.objects.count(), ledger_before)
        self.assertEqual(
            FinanceControlAudit.objects.filter(action="service_session_encounter_attached").count(), 1
        )

    def test_cancelled_session_rejects_attachment_and_mixed_terms_use_separate_sessions(self):
        encounter = self.encounter()
        cancelled = self.session(status=AssessmentServiceSession.Status.CANCELLED)
        with self.assertRaisesMessage(ValidationError, "active"):
            attach_encounter_to_service_session(encounter, cancelled, self.admin)
        sentinel = self.session(provider_type="sentinel", service_partner=None)
        self.assertNotEqual(cancelled.id, sentinel.id)

    def test_attachment_requires_exact_branch_and_branchless_session(self):
        encounter = self.encounter()
        other_branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="OTHER", name="Other"
        )
        other_branch_session = self.session(
            status=AssessmentServiceSession.Status.ACTIVE,
            service_branch=other_branch,
        )
        with self.assertRaisesMessage(ValidationError, "branch must match"):
            attach_encounter_to_service_session(encounter, other_branch_session, self.admin)

        branchless_session = self.session(
            status=AssessmentServiceSession.Status.ACTIVE,
            service_branch=None,
        )
        with self.assertRaisesMessage(ValidationError, "branch-specific encounter"):
            attach_encounter_to_service_session(encounter, branchless_session, self.admin)

        encounter.service_branch = None
        encounter.save(update_fields=["service_branch"])
        attached = attach_encounter_to_service_session(
            encounter, branchless_session, self.admin
        )
        self.assertEqual(attached.service_session_id, branchless_session.id)

    def test_manual_attachment_endpoint_is_not_exposed(self):
        session = self.session(status=AssessmentServiceSession.Status.ACTIVE)
        self.authenticate()
        response = self.client.post(
            f"/api/finance/internal/service-sessions/{session.id}/attach-encounter/",
            {"encounter_id": self.encounter().id}, format="json",
        )
        self.assertEqual(response.status_code, 404)

    def test_session_api_denies_operator_approver_and_clinical_users(self):
        url = "/api/finance/internal/service-sessions/"
        finance_users = []
        for index, group in enumerate((self.operator_group, self.approver_group)):
            user = get_user_model().objects.create_user(f"finance-role-{index}")
            user.groups.add(group)
            UserSecurityProfile.objects.create(
                user=user, is_internal_sentinel_staff=True
            )
            finance_users.append(user)
        for user in finance_users:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(url).status_code, 200)

        denied_users = []
        clinic_user = get_user_model().objects.create_user("clinic-foundation-user")
        clinic_user.groups.add(self.admin_group)
        UserOrganization.objects.create(user=clinic_user, organization=self.clinic)
        denied_users.append(clinic_user)
        partner_user = get_user_model().objects.create_user("partner-foundation-user")
        partner_user.groups.add(self.admin_group)
        UserOrganization.objects.create(user=partner_user, organization=self.partner)
        denied_users.append(partner_user)
        for user in denied_users:
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_internal_capabilities_are_explicit_and_do_not_inherit(self):
        self.authenticate()
        capabilities = self.client.get("/api/finance/capabilities/").data["internal_finance"]
        self.assertTrue(capabilities["can_administer"])
        self.assertFalse(capabilities["can_operate"])
        self.assertFalse(capabilities["can_approve"])

    def test_approved_two_account_mutation_permissions_and_capabilities(self):
        self.admin.is_superuser = True
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_superuser", "is_staff"])
        self.admin.groups.add(self.operator_group)
        UserOrganization.objects.create(user=self.admin, organization=self.clinic)

        approver = get_user_model().objects.create_user("foundation-approval-account")
        approver.groups.add(self.approver_group)
        UserSecurityProfile.objects.create(
            user=approver, is_internal_sentinel_staff=True
        )

        wallet = OrganizationWallet.objects.create(organization=self.clinic)
        funding = BankTransferFundingRequest.objects.create(
            wallet=wallet, requested_amount=Decimal("1000.00"),
            status=BankTransferFundingRequest.Status.PROOF_SUBMITTED,
            proof=SimpleUploadedFile("proof.pdf", b"proof", content_type="application/pdf"),
            proof_submitted_at=timezone.now(), expires_at=timezone.now() + timedelta(days=1),
        )
        self.authenticate()
        capabilities = self.client.get("/api/finance/capabilities/").data
        self.assertTrue(capabilities["can_administer"])
        self.assertTrue(capabilities["can_verify"])
        self.assertTrue(capabilities["can_prepare_settlements"])
        self.assertTrue(capabilities["can_mark_settlements_paid"])
        self.assertFalse(capabilities["can_approve"])
        self.assertFalse(capabilities["can_approve_settlements"])
        self.assertFalse(capabilities["can_decide_corrections"])
        verify = self.client.post(
            f"/api/finance/bank-transfer-funding/{funding.id}/verify/",
            {"received_amount": "1000.00", "bank_transaction_reference": "EXACT-ROLE-VERIFY",
             "value_date": "2026-08-14"}, format="json",
        )
        self.assertEqual(verify.status_code, 200)
        self.assertEqual(
            self.client.post(
                f"/api/finance/bank-transfer-funding/{funding.id}/approve/", {}, format="json"
            ).status_code, 403
        )

        self.client.force_authenticate(approver)
        approver_capabilities = self.client.get("/api/finance/capabilities/").data
        self.assertTrue(approver_capabilities["can_approve"])
        self.assertTrue(approver_capabilities["can_approve_settlements"])
        self.assertTrue(approver_capabilities["can_decide_corrections"])
        self.assertFalse(approver_capabilities["can_verify"])
        self.assertFalse(approver_capabilities["can_prepare_settlements"])
        self.assertFalse(approver_capabilities["can_mark_settlements_paid"])
        self.assertFalse(approver_capabilities["can_administer"])
        self.assertEqual(
            self.client.post(
                f"/api/finance/bank-transfer-funding/{funding.id}/approve/", {}, format="json"
            ).status_code, 200
        )

        approved_batch = SettlementBatch.objects.create(
            beneficiary_organization=self.clinic, period_start=date(2026, 8, 14),
            period_end=date(2026, 8, 14), status=SettlementBatch.Status.APPROVED,
            prepared_by=self.admin, approved_by=approver, approved_at=timezone.now(),
        )
        self.assertEqual(
            self.client.post(
                f"/api/finance/settlements/{approved_batch.id}/mark-paid/",
                {"external_reference": "DENIED", "payment_evidence": SimpleUploadedFile(
                    "denied.pdf", b"denied", content_type="application/pdf"
                )}, format="multipart",
            ).status_code, 403
        )
        self.authenticate()
        self.assertEqual(
            self.client.post(
                f"/api/finance/settlements/{approved_batch.id}/cancel/",
                {"reason": "Operator cannot undo approval"}, format="json",
            ).status_code, 400
        )
        self.assertEqual(
            self.client.patch(
                f"/api/finance/settlements/{approved_batch.id}/",
                {"beneficiary_organization": self.hospital.id}, format="json",
            ).status_code, 400
        )
        approved_batch.refresh_from_db()
        self.assertEqual(approved_batch.status, SettlementBatch.Status.APPROVED)
        self.assertEqual(approved_batch.beneficiary_organization, self.clinic)
        self.assertEqual(
            self.client.post(
                f"/api/finance/settlements/{approved_batch.id}/mark-paid/",
                {"external_reference": "PAID-BY-OPERATOR", "payment_evidence": SimpleUploadedFile(
                    "paid.pdf", b"paid", content_type="application/pdf"
                )}, format="multipart",
            ).status_code, 200
        )

        correction = self.client.post(
            "/api/finance/action-requests/",
            {"wallet": wallet.id, "action_type": "adjustment", "amount": "10.00",
             "reason": "Controlled correction", "external_reference": "CORR-EXACT",
             "idempotency_key": "CORR-EXACT-1"}, format="json",
        )
        self.assertEqual(correction.status_code, 201)
        self.assertEqual(
            self.client.post(
                f"/api/finance/action-requests/{correction.data['id']}/approve/", {}, format="json"
            ).status_code, 403
        )
        self.client.force_authenticate(approver)
        self.assertEqual(
            self.client.post(
                f"/api/finance/action-requests/{correction.data['id']}/approve/", {}, format="json"
            ).status_code, 200
        )
        self.assertEqual(
            self.client.post(
                "/api/finance/action-requests/",
                {"wallet": wallet.id, "action_type": "adjustment", "amount": "10.00",
                 "reason": "Denied", "external_reference": "DENIED",
                 "idempotency_key": "DENIED-APPROVER"}, format="json",
            ).status_code, 403
        )

    def test_legacy_privileged_groups_and_missing_marker_cannot_mutate(self):
        wallet = OrganizationWallet.objects.create(organization=self.clinic)
        funding = BankTransferFundingRequest.objects.create(
            wallet=wallet, requested_amount=Decimal("1000.00"),
            status=BankTransferFundingRequest.Status.PROOF_SUBMITTED,
            proof=SimpleUploadedFile("proof.pdf", b"proof", content_type="application/pdf"),
            proof_submitted_at=timezone.now(), expires_at=timezone.now() + timedelta(days=1),
        )
        group_names = [
            "super_admin", "finance_tester", "ops_admin", "sentinel_ops", "finance_viewer",
        ]
        for index, group_name in enumerate(group_names):
            user = get_user_model().objects.create_user(f"legacy-mutation-{index}")
            user.groups.add(Group.objects.get_or_create(name=group_name)[0])
            self.client.force_authenticate(user)
            self.assertEqual(
                self.client.post(
                    f"/api/finance/bank-transfer-funding/{funding.id}/verify/",
                    {"received_amount": "1000.00", "bank_transaction_reference": f"DENIED-{index}",
                     "value_date": "2026-08-14"}, format="json",
                ).status_code, 403
            )
        superuser = get_user_model().objects.create_superuser(
            "mutation-superuser", "mutation-superuser@example.test", "x"
        )
        self.client.force_authenticate(superuser)
        self.assertEqual(
            self.client.post(
                f"/api/finance/bank-transfer-funding/{funding.id}/verify/",
                {"received_amount": "1000.00", "bank_transaction_reference": "DENIED-SUPER",
                 "value_date": "2026-08-14"}, format="json",
            ).status_code, 403
        )
        missing_marker = get_user_model().objects.create_user("missing-marker-operator")
        missing_marker.groups.add(self.operator_group)
        self.client.force_authenticate(missing_marker)
        self.assertEqual(
            self.client.post(
                f"/api/finance/bank-transfer-funding/{funding.id}/verify/",
                {"received_amount": "1000.00", "bank_transaction_reference": "DENIED-MARKER",
                 "value_date": "2026-08-14"}, format="json",
            ).status_code, 403
        )
        ops = get_user_model().objects.create_user("capability-ops")
        ops.groups.add(Group.objects.get_or_create(name="ops_admin")[0])
        self.client.force_authenticate(ops)
        capabilities = self.client.get("/api/finance/capabilities/").data["internal_finance"]
        self.assertFalse(any(capabilities.values()))

        linked_admin = get_user_model().objects.create_user("capability-linked-admin")
        linked_admin.groups.add(self.admin_group)
        UserOrganization.objects.create(user=linked_admin, organization=self.clinic)
        UserSecurityProfile.objects.create(
            user=linked_admin, is_internal_sentinel_staff=True
        )
        self.client.force_authenticate(linked_admin)
        response = self.client.get("/api/auth/me/")
        self.assertTrue(response.data["is_internal_sentinel_staff"])
        self.assertIn("finance_admin", response.data["roles"])
        capabilities = self.client.get("/api/finance/capabilities/").data["internal_finance"]
        self.assertTrue(capabilities["can_administer"])
        self.assertFalse(capabilities["can_operate"])
        self.assertFalse(capabilities["can_approve"])

    def test_session_and_partner_api_lifecycle_records_append_only_audits(self):
        self.authenticate()
        partner_response = self.client.post(
            "/api/finance/internal/service-partners/",
            {"clinic_id": "AUDIT-PARTNER", "name": "Audit Partner", "currency": "NGN"},
            format="json",
        )
        self.assertEqual(partner_response.status_code, 201)
        partner_id = partner_response.data["id"]
        self.assertEqual(
            self.client.patch(
                f"/api/finance/internal/service-partners/{partner_id}/",
                {"is_active": False}, format="json",
            ).status_code,
            200,
        )
        session_response = self.client.post(
            "/api/finance/internal/service-sessions/",
            {
                "service_date": "2026-08-14", "location_type": "clinic",
                "participating_organization": self.clinic.id,
                "provider_type": "sentinel", "sentinel_arranged_transport": True,
                "camera_team_rate": "5000.00", "logistics_allocation_rate": "2500.00",
                "currency": "NGN",
            }, format="json",
        )
        self.assertEqual(session_response.status_code, 201)
        session_id = session_response.data["id"]
        self.assertEqual(
            self.client.patch(
                f"/api/finance/internal/service-sessions/{session_id}/",
                {"camera_team_rate": "5100.00"}, format="json",
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                f"/api/finance/internal/service-sessions/{session_id}/activate/", {}, format="json"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(
                f"/api/finance/internal/service-sessions/{session_id}/complete/", {}, format="json"
            ).status_code,
            200,
        )
        actions = set(FinanceControlAudit.objects.values_list("action", flat=True))
        self.assertTrue({
            "service_partner_created", "service_partner_deactivated",
            "service_session_created", "service_session_draft_edited",
            "service_session_activated", "service_session_completed",
        }.issubset(actions))

    def test_audit_failure_rolls_back_partner_and_session_creation(self):
        self.authenticate()
        with patch("finance.views._audit_internal", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.client.post(
                    "/api/finance/internal/service-partners/",
                    {"clinic_id": "ROLLBACK-PARTNER", "name": "Rollback Partner", "currency": "NGN"},
                    format="json",
                )
        self.assertFalse(Organization.objects.filter(clinic_id="ROLLBACK-PARTNER").exists())

        with patch("finance.views._audit_internal", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.client.post(
                    "/api/finance/internal/service-sessions/",
                    {
                        "service_date": "2026-08-14", "location_type": "clinic",
                        "participating_organization": self.clinic.id,
                        "service_branch": self.branch.id, "provider_type": "sentinel",
                        "camera_team_rate": "5000.00", "logistics_allocation_rate": "2500.00",
                        "currency": "NGN",
                    }, format="json",
                )
        self.assertFalse(AssessmentServiceSession.objects.exists())

    def test_audit_failure_rolls_back_partner_update_and_deactivation(self):
        self.authenticate()
        url = f"/api/finance/internal/service-partners/{self.partner.id}/"
        with patch("finance.views._audit_internal", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.client.patch(url, {"name": "Changed Partner"}, format="json")
        self.partner.refresh_from_db()
        self.assertEqual(self.partner.name, "Foundation Partner")

        with patch("finance.views._audit_internal", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.client.patch(url, {"is_active": False}, format="json")
        self.partner.refresh_from_db()
        self.assertTrue(self.partner.is_active)

    def test_audit_failure_rolls_back_draft_edit_and_activation(self):
        self.authenticate()
        session = self.session()
        with patch("finance.views._audit_internal", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.client.patch(
                    f"/api/finance/internal/service-sessions/{session.id}/",
                    {"camera_team_rate": "9000.00"}, format="json",
                )
        session.refresh_from_db()
        self.assertEqual(session.camera_team_rate, Decimal("5000.00"))
        self.assertEqual(session.configuration_version, 1)

        with patch("finance.views._audit_internal", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.client.post(
                    f"/api/finance/internal/service-sessions/{session.id}/activate/", {}, format="json"
                )
        session.refresh_from_db()
        self.assertEqual(session.status, AssessmentServiceSession.Status.DRAFT)
        self.assertIsNone(session.activated_by_id)
        self.assertIsNone(session.activated_at)

    def test_repeated_transitions_are_conflicts_without_duplicate_audits(self):
        self.authenticate()
        session = self.session()
        activate_url = f"/api/finance/internal/service-sessions/{session.id}/activate/"
        self.assertEqual(self.client.post(activate_url, {}, format="json").status_code, 200)
        session.refresh_from_db()
        activated_by_id = session.activated_by_id
        activated_at = session.activated_at
        self.assertEqual(self.client.post(activate_url, {}, format="json").status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.activated_by_id, activated_by_id)
        self.assertEqual(session.activated_at, activated_at)
        self.assertEqual(
            FinanceControlAudit.objects.filter(
                action="service_session_activated", metadata__session_id=session.id
            ).count(), 1
        )

        complete_url = f"/api/finance/internal/service-sessions/{session.id}/complete/"
        self.assertEqual(self.client.post(complete_url, {}, format="json").status_code, 200)
        session.refresh_from_db()
        completed_by_id = session.completed_by_id
        completed_at = session.completed_at
        self.assertEqual(self.client.post(complete_url, {}, format="json").status_code, 409)
        session.refresh_from_db()
        self.assertEqual(session.completed_by_id, completed_by_id)
        self.assertEqual(session.completed_at, completed_at)
        self.assertEqual(
            FinanceControlAudit.objects.filter(
                action="service_session_completed", metadata__session_id=session.id
            ).count(), 1
        )

    def test_repeated_cancellation_and_stale_draft_edit_are_rejected(self):
        self.authenticate()
        cancelled = self.session()
        cancel_url = f"/api/finance/internal/service-sessions/{cancelled.id}/cancel/"
        self.assertEqual(
            self.client.post(cancel_url, {"reason": "Cancelled once"}, format="json").status_code, 200
        )
        cancelled.refresh_from_db()
        cancelled_at = cancelled.cancelled_at
        self.assertEqual(
            self.client.post(cancel_url, {"reason": "Again"}, format="json").status_code, 409
        )
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.cancelled_at, cancelled_at)
        self.assertEqual(
            FinanceControlAudit.objects.filter(
                action="service_session_cancelled", metadata__session_id=cancelled.id
            ).count(), 1
        )

        active = self.session()
        self.assertEqual(
            self.client.post(
                f"/api/finance/internal/service-sessions/{active.id}/activate/", {}, format="json"
            ).status_code, 200
        )
        self.assertEqual(
            self.client.patch(
                f"/api/finance/internal/service-sessions/{active.id}/",
                {"camera_team_rate": "9000.00"}, format="json",
            ).status_code, 400
        )
        active.refresh_from_db()
        self.assertEqual(active.camera_team_rate, Decimal("5000.00"))

    def test_protected_bank_transfer_evidence_owner_and_internal_roles_only(self):
        wallet = OrganizationWallet.objects.create(organization=self.clinic)
        request = BankTransferFundingRequest.objects.create(
            wallet=wallet, requested_amount=Decimal("10000.00"),
            proof=SimpleUploadedFile("proof.pdf", b"proof", content_type="application/pdf"),
        )
        url = f"/api/finance/bank-transfer-funding/{request.id}/proof-download/"
        clinic_user = get_user_model().objects.create_user("proof-owner")
        UserOrganization.objects.create(user=clinic_user, organization=self.clinic)
        self.client.force_authenticate(clinic_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()
        other_user = get_user_model().objects.create_user("proof-other")
        UserOrganization.objects.create(user=other_user, organization=self.hospital)
        self.client.force_authenticate(other_user)
        self.assertEqual(self.client.get(url).status_code, 404)
        ops = get_user_model().objects.create_user("proof-ops")
        ops.groups.add(Group.objects.get_or_create(name="ops_admin")[0])
        self.client.force_authenticate(ops)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.authenticate()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get(request.proof.url).status_code, 404)

    def test_protected_settlement_evidence_and_missing_file(self):
        batch = SettlementBatch.objects.create(
            beneficiary_organization=self.partner, period_start=date(2026, 8, 14),
            period_end=date(2026, 8, 14),
            payment_evidence=SimpleUploadedFile("settlement.pdf", b"paid", content_type="application/pdf"),
        )
        url = f"/api/finance/settlements/{batch.id}/evidence-download/"
        viewer = get_user_model().objects.create_user("evidence-viewer")
        viewer.groups.add(Group.objects.get_or_create(name="finance_viewer")[0])
        self.client.force_authenticate(viewer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.authenticate()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()
        batch.payment_evidence.delete(save=False)
        batch.payment_evidence = "finance/settlements/missing.pdf"
        batch.save(update_fields=["payment_evidence"])
        self.assertEqual(self.client.get(url).status_code, 404)
