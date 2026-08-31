from datetime import date
from decimal import Decimal
import tempfile

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from unittest.mock import patch

from organizations.models import Organization, OrganizationBranch, OrganizationProfile
from patients.models import Patient
from users.models import UserBranchAccess, UserOrganization
from finance.models import (
    OrganizationWallet,
    PartnerContract,
    PricingRule,
    WalletLedgerEntry,
)
from finance.services import top_up_wallet
from consents.models import ConsentRecord
from audit.models import PatientTimelineEvent
from reports.models import StructuredReport, StructuredReportVersion

from .models import (
    OcularAIReview,
    OcularDiagnosticAssessment,
    OcularInvestigation,
    ScreeningEncounter,
)


class ClinicalIntakeAccessTests(TestCase):
    def setUp(self):
        self.clinic = Organization.objects.create(
            clinic_id="CLINIC-INTAKE", name="Clinical Intake Clinic",
            organization_type="clinic",
        )
        self.other_clinic = Organization.objects.create(
            clinic_id="CLINIC-INTAKE-OTHER", name="Other Intake Clinic",
            organization_type="clinic",
        )
        self.hospital = Organization.objects.create(
            clinic_id="HOSPITAL-INTAKE", name="Intake Hospital",
            organization_type="hospital",
        )
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="MAIN", name="Main",
            is_head_office=True,
        )
        self.other_branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="OTHER", name="Other",
        )
        self.patient = Patient.objects.create(
            patient_id="PAT-INTAKE-1", first_name="Synthetic", last_name="Intake",
            date_of_birth=date(1980, 1, 1), sex="female",
            assigned_clinic=self.clinic, assigned_branch=self.branch,
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-INTAKE-1", patient=self.patient,
            encounter_date=date(2026, 8, 30), encounter_type="retinal_assessment",
            programme="diabetic_screening", source_type="clinic_direct",
            workflow_route="clinic_managed", payment_responsibility="patient",
            originating_organization=self.clinic, service_branch=self.branch,
            diabetes_duration="Ten years", symptoms_notes="Synthetic symptoms",
            clinical_notes="Synthetic internal note",
        )
        self.client = APIClient()

    def user(self, username, roles=(), organization=None, branch=None, superuser=False):
        user = User.objects.create_user(username=username, password="test", is_superuser=superuser)
        for role in roles:
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        if organization:
            UserOrganization.objects.create(user=user, organization=organization)
        if branch:
            UserBranchAccess.objects.create(user=user, branch=branch, is_default=True)
        return user

    def patch(self, user, payload):
        self.client.force_authenticate(user)
        return self.client.patch(
            f"/api/encounters/{self.encounter.pk}/", payload, format="json"
        )

    def test_fresh_retrieval_returns_all_values_and_safe_blanks(self):
        optometrist = self.user("intake-retrieve", {"optometrist"}, self.clinic, self.branch)
        self.client.force_authenticate(optometrist)
        response = self.client.get(f"/api/encounters/{self.encounter.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["diabetes_duration"], "Ten years")
        self.assertEqual(response.data["symptoms_notes"], "Synthetic symptoms")
        self.assertEqual(response.data["clinical_notes"], "Synthetic internal note")
        self.encounter.diabetes_duration = ""
        self.encounter.symptoms_notes = ""
        self.encounter.clinical_notes = ""
        self.encounter.save()
        response = self.client.get(f"/api/encounters/{self.encounter.pk}/")
        self.assertEqual(
            [response.data[field] for field in ("diabetes_duration", "symptoms_notes", "clinical_notes")],
            ["", "", ""],
        )

    def test_exact_clinical_roles_can_update_and_partial_patch_preserves_other_fields(self):
        optometrist = self.user("intake-opto", {"optometrist"}, self.clinic, self.branch)
        response = self.patch(optometrist, {"clinical_notes": "Corrected synthetic note"})
        self.assertEqual(response.status_code, 200, response.data)
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.diabetes_duration, "Ten years")
        self.assertEqual(self.encounter.symptoms_notes, "Synthetic symptoms")
        self.assertEqual(self.encounter.clinical_notes, "Corrected synthetic note")

        master = self.user(
            "intake-master", {"clinic_admin", "optometrist"}, self.clinic, self.branch
        )
        self.assertEqual(self.patch(master, {"symptoms_notes": "Master correction"}).status_code, 200)
        reviewer = self.user("intake-reviewer", {"reviewer"}, self.clinic, self.branch)
        self.assertEqual(self.patch(reviewer, {"diabetes_duration": "Eleven years"}).status_code, 200)

    def test_nonclinical_and_out_of_scope_users_cannot_update(self):
        users = [
            self.user("intake-admin", {"clinic_admin"}, self.clinic, self.branch),
            self.user("intake-generic", {"admin"}, self.clinic, self.branch),
            self.user("intake-super", (), self.clinic, self.branch, superuser=True),
            self.user("intake-other-org", {"optometrist"}, self.other_clinic),
            self.user("intake-wrong-branch", {"optometrist"}, self.clinic, self.other_branch),
            self.user("intake-hospital", {"optometrist"}, self.hospital),
        ]
        for index, user in enumerate(users):
            response = self.patch(user, {"clinical_notes": f"Denied {index}"})
            self.assertIn(response.status_code, {403, 404})
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.clinical_notes, "Synthetic internal note")

    def test_mixed_patch_cannot_bypass_intake_authority(self):
        admin = self.user("intake-mixed-admin", {"clinic_admin"}, self.clinic, self.branch)
        response = self.patch(admin, {
            "clinical_notes": "Denied mixed update",
            "dilation_notes": "Must also roll back",
        })
        self.assertEqual(response.status_code, 403)
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.clinical_notes, "Synthetic internal note")
        self.assertEqual(self.encounter.dilation_notes, "")

    def test_safe_audit_names_changed_fields_without_note_content(self):
        optometrist = self.user("intake-audit", {"optometrist"}, self.clinic, self.branch)
        secret_text = "Distinctive synthetic private note"
        response = self.patch(optometrist, {"clinical_notes": secret_text})
        self.assertEqual(response.status_code, 200)
        event = PatientTimelineEvent.objects.get(event_type="clinical_intake_updated")
        self.assertEqual(event.metadata, {"changed_fields": ["clinical_notes"]})
        self.assertNotIn(secret_text, event.description)
        self.assertNotIn(secret_text, str(event.metadata))

    def test_issued_report_and_versions_are_unchanged(self):
        report = StructuredReport.objects.create(
            report_id="REP-INTAKE-1", encounter=self.encounter, patient=self.patient,
            review_date=date(2026, 8, 30), report_status="clinic_issued",
            notes="Controlled report note",
        )
        version = StructuredReportVersion.objects.create(
            report=report, version_number=1,
            clinical_snapshot={"notes": "Controlled report note"},
            checksum_sha256="a" * 64, purpose="initial",
        )
        StructuredReport.objects.filter(pk=report.pk).update(issued_version=version)
        optometrist = self.user("intake-issued", {"optometrist"}, self.clinic, self.branch)
        response = self.patch(optometrist, {"clinical_notes": "Updated after issue"})
        self.assertEqual(response.status_code, 200)
        report.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(report.notes, "Controlled report note")
        self.assertEqual(report.issued_version_id, version.pk)
        self.assertEqual(report.versions.count(), 1)
        self.assertEqual(version.clinical_snapshot, {"notes": "Controlled report note"})


class OcularDiagnosticWorkflowTests(TestCase):
    def setUp(self):
        self.clinical_temp = tempfile.TemporaryDirectory()
        self.storage_override = override_settings(
            CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE=False,
            PRIVATE_CLINICAL_ASSETS_ROOT=self.clinical_temp.name,
        )
        self.storage_override.enable()
        self.clinic = Organization.objects.create(
            clinic_id="CLINIC-OCULAR",
            name="Ocular Test Clinic",
            organization_type="clinic",
        )
        self.profile = OrganizationProfile.objects.create(
            organization=self.clinic,
            ocular_diagnostics_enabled=True,
            clinic_direct_screening_enabled=True,
            workflow_mode="clinic_managed",
            default_payment_responsibility="patient",
        )
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="MAIN", name="Main",
            is_head_office=True,
        )
        self.user = User.objects.create_user("ocular-clinician", password="test")
        self.user.groups.add(Group.objects.create(name="optometrist"))
        UserOrganization.objects.create(
            user=self.user, organization=self.clinic
        )
        UserBranchAccess.objects.create(user=self.user, branch=self.branch, is_default=True)
        self.patient = Patient.objects.create(
            patient_id="PAT-OCULAR-1",
            first_name="Test",
            last_name="Patient",
            date_of_birth=date(1980, 1, 1),
            sex="female",
            assigned_clinic=self.clinic,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.wallet = OrganizationWallet.objects.create(
            organization=self.clinic,
            currency="NGN",
        )
        top_up_wallet(
            self.wallet,
            "10000.00",
            "ocular-ai-test-opening-balance",
        )
        self.clinical_ai_consent = ConsentRecord.objects.create(
            consent_id="CNS-OCULAR-AI-1",
            patient=self.patient,
            consent_type="ai_clinical_review",
            consent_status="granted",
            consent_date=date(2026, 7, 26),
            captured_by="Test clinician",
        )

    def tearDown(self):
        self.storage_override.disable()
        self.clinical_temp.cleanup()

    def payload(self, programme):
        return {
            "encounter_id": f"ENC-{programme}",
            "patient": self.patient.id,
            "encounter_date": "2026-07-26",
            "programme": programme,
            "source_type": "clinic_direct",
            "workflow_route": "clinic_managed",
            "payment_responsibility": "patient",
        }

    def test_ocular_encounter_creates_separate_clinical_record(self):
        response = self.client.post(
            "/api/encounters/",
            self.payload("ocular_diagnostics"),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        encounter = ScreeningEncounter.objects.get(pk=response.data["id"])
        self.assertEqual(encounter.encounter_type, "ocular_assessment")
        self.assertIsNone(encounter.hospital_referral)
        self.assertTrue(
            OcularDiagnosticAssessment.objects.filter(
                encounter=encounter
            ).exists()
        )

    def test_combined_encounter_has_diabetic_and_eye_health_flags(self):
        response = self.client.post(
            "/api/encounters/",
            self.payload("combined_assessment"),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        encounter = ScreeningEncounter.objects.get(pk=response.data["id"])
        self.assertTrue(encounter.includes_diabetic_screening)
        self.assertTrue(encounter.includes_eye_health_screening)
        self.assertFalse(encounter.includes_ocular_diagnostics)
        self.assertFalse(hasattr(encounter, "ocular_assessment"))
        self.assertEqual(encounter.assessment_location_snapshot["site_name"], self.branch.name)
        self.assertEqual(encounter.assessment_location_snapshot["branch_code"], self.branch.branch_code)

    def test_mobile_location_is_snapshotted_without_rewriting_branch(self):
        payload = self.payload("eye_health_screening")
        payload.update({
            "assessment_location_type": "mobile",
            "assessment_location_name": "Synthetic community site",
            "assessment_location_address": "Synthetic district",
        })
        response = self.client.post("/api/encounters/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        encounter = ScreeningEncounter.objects.get(pk=response.data["id"])
        self.assertEqual(encounter.assessment_location_snapshot["location_type"], "mobile")
        self.assertEqual(encounter.assessment_location_snapshot["site_name"], "Synthetic community site")
        self.assertEqual(encounter.assessment_location_snapshot["branch_id"], self.branch.pk)
        self.branch.name = "Renamed after assessment"
        self.branch.save(update_fields=["name"])
        encounter.refresh_from_db()
        self.assertEqual(encounter.assessment_location_snapshot["site_name"], "Synthetic community site")

    def test_disabled_clinic_cannot_create_ocular_encounter(self):
        self.profile.ocular_diagnostics_enabled = False
        self.profile.save(update_fields=["ocular_diagnostics_enabled"])
        response = self.client.post(
            "/api/encounters/",
            self.payload("ocular_diagnostics"),
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def create_ocular_encounter(self, suffix=""):
        payload = self.payload("ocular_diagnostics")
        if suffix:
            payload["encounter_id"] = f"ENC-OCULAR-{suffix}"[:30]
        response = self.client.post(
            "/api/encounters/",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return ScreeningEncounter.objects.get(pk=response.data["id"])

    def upload_visual_field(self, encounter):
        visual_field = SimpleUploadedFile(
            "right-field.pdf",
            b"%PDF-1.4 test visual field",
            content_type="application/pdf",
        )
        return self.client.post(
            f"/api/encounters/{encounter.id}/ocular-investigations/",
            {
                "investigation_type": "visual_field",
                "laterality": "right",
                "test_type": "24-2 SITA Fast",
                "device_name": "Humphrey",
                "reliability": "reliable",
                "file": visual_field,
            },
            format="multipart",
        )

    def test_visual_field_pdf_is_linked_to_ocular_encounter(self):
        encounter = self.create_ocular_encounter()
        response = self.upload_visual_field(encounter)
        self.assertEqual(response.status_code, 201, response.data)
        investigation = OcularInvestigation.objects.get(
            pk=response.data["id"]
        )
        self.assertEqual(investigation.encounter, encounter)
        self.assertEqual(investigation.investigation_type, "visual_field")
        self.assertEqual(investigation.original_filename, "right-field.pdf")
        self.assertEqual(investigation.storage_kind, "private_clinical")
        self.assertEqual(investigation.file.name, "")
        self.assertNotIn("right-field", investigation.private_object_key)
        self.assertIn(
            f"/api/encounters/ocular-investigations/{investigation.pk}/content/",
            response.data["file"],
        )
        content = self.client.get(
            f"/api/encounters/ocular-investigations/{investigation.pk}/content/"
        )
        self.assertEqual(content.status_code, 200)
        self.assertNotIn(investigation.private_object_key, str(content.headers))
        content.close()

        unauthorized = User.objects.create_user("ocular-wrong-branch")
        unauthorized.groups.add(Group.objects.get(name="optometrist"))
        UserOrganization.objects.create(user=unauthorized, organization=self.clinic)
        other_branch = OrganizationBranch.objects.create(
            organization=self.clinic,
            branch_code="OTHER",
            name="Other",
        )
        UserBranchAccess.objects.create(user=unauthorized, branch=other_branch)
        self.client.force_authenticate(unauthorized)
        self.assertEqual(
            self.client.get(
                f"/api/encounters/ocular-investigations/{investigation.pk}/content/"
            ).status_code,
            403,
        )
        self.client.force_authenticate(self.user)

    def test_ai_review_requires_locked_clinician_assessment(self):
        encounter = self.create_ocular_encounter()
        self.upload_visual_field(encounter)
        response = self.client.post(
            f"/api/encounters/{encounter.id}/ocular-ai-reviews/",
            {"privacy_confirmed": True},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(OcularAIReview.objects.count(), 0)

    def test_ai_review_requires_separate_clinical_ai_consent_before_charge(self):
        encounter = self.create_ocular_encounter("NO-CONSENT")
        self.upload_visual_field(encounter)
        assessment = encounter.ocular_assessment
        assessment.impression = "Glaucoma suspect"
        assessment.management_plan = "Refer"
        assessment.completed_at = timezone.now()
        assessment.completed_by = self.user
        assessment.save()
        self.clinical_ai_consent.delete()

        response = self.client.post(
            f"/api/encounters/{encounter.id}/ocular-ai-reviews/",
            {"privacy_confirmed": True},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(OcularAIReview.objects.count(), 0)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal("10000.00"))

    def test_ai_review_requires_file_privacy_confirmation_before_charge(self):
        encounter = self.create_ocular_encounter("NO-PRIVACY")
        self.upload_visual_field(encounter)
        assessment = encounter.ocular_assessment
        assessment.impression = "Glaucoma suspect"
        assessment.management_plan = "Refer"
        assessment.completed_at = timezone.now()
        assessment.completed_by = self.user
        assessment.save()

        response = self.client.post(
            f"/api/encounters/{encounter.id}/ocular-ai-reviews/",
            {"privacy_confirmed": False},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(OcularAIReview.objects.count(), 0)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal("10000.00"))

    @patch("encounters.views.run_ocular_ai_review")
    def test_completed_assessment_can_request_structured_ai_review(self, run):
        encounter = self.create_ocular_encounter()
        self.upload_visual_field(encounter)
        assessment = encounter.ocular_assessment
        assessment.impression = "Suspicious glaucomatous optic neuropathy"
        assessment.management_plan = "Refer for glaucoma assessment"
        assessment.completed_at = timezone.now()
        assessment.completed_by = self.user
        assessment.save()
        run.return_value = (
            {
                "suspected_conditions": [
                    {"label": "Glaucoma suspect", "certainty": "probable", "eye": "right"}
                ],
                "supporting_findings": ["Repeatable arcuate field defect"],
                "differential_diagnoses": ["Neurological field defect"],
                "suggested_urgency": "priority",
                "suggested_management": "Specialist glaucoma assessment",
                "limitations": ["Fundus correlation required"],
                "agreement_status": "partial_agreement",
                "disagreement_reasons": [],
                "expert_review_required": False,
            },
            type("Response", (), {"model": "test-model"})(),
        )
        response = self.client.post(
            f"/api/encounters/{encounter.id}/ocular-ai-reviews/",
            {"privacy_confirmed": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["provider"], "openai")
        self.assertEqual(
            response.data["clinical_ai_consent"],
            self.clinical_ai_consent.pk,
        )
        self.assertFalse(
            response.data["transmitted_data_manifest"]["direct_identifiers_included"]
        )
        self.assertEqual(response.data["agreement_status"], "partial_agreement")
        self.assertEqual(response.data["clinician_decision"], "pending")
        self.assertEqual(response.data["fee_amount"], "0.00")
        self.assertEqual(response.data["payment_status"], "free")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal("10000.00"))

        second = self.client.post(
            f"/api/encounters/{encounter.id}/ocular-ai-reviews/",
            {"privacy_confirmed": True},
            format="json",
        )
        self.assertEqual(second.status_code, 409)
        self.assertEqual(
            WalletLedgerEntry.objects.filter(
                metadata__service_type="ocular_ai_review"
            ).count(),
            0,
        )

    @patch("encounters.views.run_ocular_ai_review")
    def test_failed_ai_review_is_refunded_and_cannot_be_retried(self, run):
        encounter = self.create_ocular_encounter()
        self.upload_visual_field(encounter)
        assessment = encounter.ocular_assessment
        assessment.impression = "Glaucoma suspect"
        assessment.management_plan = "Refer"
        assessment.completed_at = timezone.now()
        assessment.completed_by = self.user
        assessment.save()
        run.side_effect = RuntimeError("Provider unavailable")

        response = self.client.post(
            f"/api/encounters/{encounter.id}/ocular-ai-reviews/",
            {"privacy_confirmed": True},
            format="json",
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["payment_status"], "free_failed")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal("10000.00"))

        second = self.client.post(
            f"/api/encounters/{encounter.id}/ocular-ai-reviews/",
            {"privacy_confirmed": True},
            format="json",
        )
        self.assertEqual(second.status_code, 409)

    @patch("encounters.views.run_ocular_ai_review")
    def test_contract_price_applies_after_clinic_free_review(self, run):
        OcularAIReview.objects.create(
            review_id="OAI-PRIORFREE",
            encounter=self.create_ocular_encounter("PRIOR"),
            requested_by=self.user,
            status="completed",
            provider="hybrid",
            fee_amount=Decimal("0.00"),
            fee_currency="NGN",
            payment_status="free",
            clinician_impression_snapshot="Prior",
            clinician_management_snapshot="Prior",
        )
        contract = PartnerContract.objects.create(
            organization=self.clinic,
            name="Ocular contract",
            programme="ocular_diagnostics",
            status=PartnerContract.Status.ACTIVE,
            currency="NGN",
            effective_from=date(2026, 1, 1),
        )
        rule = PricingRule.objects.create(
            contract=contract,
            name="AI review price",
            service_type="ocular_ai_review",
            gross_amount=Decimal("2750.00"),
            effective_from=date(2026, 1, 1),
        )
        encounter = self.create_ocular_encounter("PAID")
        self.upload_visual_field(encounter)
        assessment = encounter.ocular_assessment
        assessment.impression = "Glaucoma suspect"
        assessment.management_plan = "Refer"
        assessment.completed_at = timezone.now()
        assessment.completed_by = self.user
        assessment.save()
        run.return_value = (
            {
                "suspected_conditions": [],
                "supporting_findings": [],
                "differential_diagnoses": [],
                "suggested_urgency": "routine",
                "suggested_management": "Review",
                "limitations": [],
                "agreement_status": "agreement",
                "disagreement_reasons": [],
                "expert_review_required": False,
            },
            type("Response", (), {"model": "test-model"})(),
        )

        quote = self.client.get(
            f"/api/encounters/{encounter.id}/ocular-ai-reviews/"
        )
        self.assertEqual(quote.data["pricing"]["amount_due"], "2750.00")
        self.assertFalse(quote.data["pricing"]["free_review_available"])
        self.assertEqual(quote.data["pricing"]["pricing_source"], "contract")

        response = self.client.post(
            f"/api/encounters/{encounter.id}/ocular-ai-reviews/",
            {"privacy_confirmed": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["fee_amount"], "2750.00")
        self.assertEqual(response.data["payment_status"], "charged")
        self.assertEqual(response.data["pricing_rule"], rule.pk)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal("7250.00"))

    def test_ocular_report_composition_rejects_foreign_investigation(self):
        first = self.create_ocular_encounter("REPORT-A")
        second = self.create_ocular_encounter("REPORT-B")
        investigation_response = self.upload_visual_field(second)
        response = self.client.patch(
            f"/api/encounters/{first.id}/ocular-assessment/",
            {
                "report_layout": "with_investigations",
                "selected_ocular_investigation_ids": [
                    investigation_response.data["id"]
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_ocular_pdf_is_clinic_branded_and_excludes_ai_output(self):
        self.clinic.report_footer_note = "Independent clinical eye report."
        self.clinic.save(update_fields=["report_footer_note"])
        encounter = self.create_ocular_encounter("REPORT")
        assessment = encounter.ocular_assessment
        assessment.impression = "Glaucoma suspect"
        assessment.management_plan = "Refer for specialist assessment"
        assessment.completed_at = timezone.now()
        assessment.completed_by = self.user
        assessment.save()
        OcularAIReview.objects.create(
            review_id="OAI-REPORT",
            encounter=encounter,
            requested_by=self.user,
            status="completed",
            provider="hybrid",
            fee_amount=Decimal("0.00"),
            payment_status="free",
            clinician_impression_snapshot=assessment.impression,
            clinician_management_snapshot=assessment.management_plan,
            suggested_management="RAW AI TEXT MUST NOT APPEAR",
        )

        response = self.client.get(
            f"/api/encounters/{encounter.id}/ocular-assessment/pdf/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertNotIn(b"RAW AI TEXT MUST NOT APPEAR", response.content)
        self.assertNotIn(b"Sentinel", response.content)

    def test_other_clinic_cannot_download_ocular_pdf(self):
        encounter = self.create_ocular_encounter("SCOPED")
        other = Organization.objects.create(
            clinic_id="CLINIC-OTHER",
            name="Other Clinic",
            organization_type="clinic",
        )
        other_user = User.objects.create_user("other-clinician", password="test")
        UserOrganization.objects.create(user=other_user, organization=other)
        self.client.force_authenticate(other_user)
        response = self.client.get(
            f"/api/encounters/{encounter.id}/ocular-assessment/pdf/"
        )
        self.assertEqual(response.status_code, 403)
