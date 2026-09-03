from datetime import date
from io import BytesIO
import base64
import hashlib
import tempfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from rest_framework.test import APIClient

from encounters.models import OcularDiagnosticAssessment, OcularInvestigation, ScreeningEncounter
from organizations.models import Organization, OrganizationBranch
from patients.models import Patient
from reports.eye_health import LIMITATION, generate_suggested_wording
from reports.models import EyeHealthScreeningReport, StructuredReport, StructuredReportVersion
from finance.models import EncounterFinancialRecord
from referrals.models import HospitalReferral
from uploads.storage import get_private_clinical_storage
from users.models import ClinicalProfessionalProfile, UserBranchAccess, UserOrganization, UserSecurityProfile
from uploads.models import ImageUpload


def synthetic_pdf(page_count=1):
    output = BytesIO()
    writer = PdfWriter()
    for _index in range(page_count):
        writer.add_blank_page(width=612, height=792)
    writer.write(output)
    return output.getvalue()


def labelled_pdf(label):
    output = BytesIO()
    document = canvas.Canvas(output)
    document.drawString(72, 720, label)
    document.save()
    return output.getvalue()


class EyeHealthScreeningWorkflowTests(TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.override = override_settings(
            MEDIA_ROOT=self.storage.name,
            CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE=False,
            PRIVATE_CLINICAL_ASSETS_ROOT=self.storage.name,
        )
        self.override.enable()
        self.client = APIClient()
        self.clinic = Organization.objects.create(
            clinic_id="CLINIC-EYE-HEALTH", name="Synthetic Eye Clinic", organization_type="clinic"
        )
        self.other_clinic = Organization.objects.create(
            clinic_id="CLINIC-EYE-OTHER", name="Other Synthetic Clinic", organization_type="clinic"
        )
        self.hospital = Organization.objects.create(
            clinic_id="HOSPITAL-EYE", name="Synthetic Hospital", organization_type="hospital"
        )
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="MAIN", name="Synthetic Main", address="Synthetic address",
            is_head_office=True,
        )
        self.other_branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="OTHER", name="Synthetic Other"
        )
        self.patient = Patient.objects.create(
            patient_id="PAT-EYE-HEALTH", first_name="Synthetic", last_name="Person",
            date_of_birth=date(1980, 1, 1), sex="female", assigned_clinic=self.clinic,
            assigned_branch=self.branch, consent_status="completed",
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-EYE-HEALTH", patient=self.patient, encounter_date=date(2026, 8, 30),
            encounter_type="eye_health_screening", programme="eye_health_screening",
            service_package=ScreeningEncounter.ServicePackage.EYE_HEALTH_SCREENING,
            source_type="clinic_direct", workflow_route="clinic_managed",
            payment_responsibility="patient", originating_organization=self.clinic,
            service_branch=self.branch,
            assessment_location_snapshot={
                "location_type": "mobile", "site_name": "Synthetic client site",
                "address": "Synthetic district", "branch_id": self.branch.pk,
                "branch_code": self.branch.branch_code, "branch_name": self.branch.name,
            },
            left_unaided_va="6/6", right_unaided_va="6/9",
            iop_before_dilation_left="14", iop_before_dilation_right="15",
        )
        self.optometrist = self.make_user("eye-opto", {"optometrist"}, self.clinic, self.branch, profile=True)

    def tearDown(self):
        self.override.disable()
        self.storage.cleanup()
        super().tearDown()

    def make_user(self, name, roles, organization=None, branch=None, *, profile=False, superuser=False):
        user = get_user_model().objects.create_user(
            username=name, password="synthetic", is_superuser=superuser, is_staff=superuser
        )
        for role in roles:
            user.groups.add(Group.objects.get_or_create(name=role)[0])
        if organization:
            UserOrganization.objects.create(user=user, organization=organization)
        if branch:
            UserBranchAccess.objects.create(user=user, branch=branch, is_default=True)
        if profile:
            ClinicalProfessionalProfile.objects.create(
                user=user, display_name="Dr Synthetic", professional_role="Optometrist",
                registration_number="SYN-001", qualifications="OD", is_verified=True,
                verified_at=timezone.now(),
            )
        return user

    def save_draft(self, **extra):
        self.client.force_authenticate(self.optometrist)
        payload = {
            "outcome": "routine_eye_examination",
            "selected_advice": ["Arrange a routine comprehensive eye examination."],
            "advice": "Arrange a synthetic routine examination.",
            "clinical_summary": "No immediate concern was identified within the areas assessed.",
            "right_visual_field_result": "Clinician confirmed right result",
            "left_visual_field_result": "Clinician confirmed left result",
            "right_fundus_result": "Clinician confirmed right fundus",
            "left_fundus_result": "Clinician confirmed left fundus",
        }
        payload.update(extra)
        existing = EyeHealthScreeningReport.objects.filter(encounter=self.encounter).first()
        if existing:
            payload["expected_version"] = existing.lock_version
        response = self.client.post(
            f"/api/reports/eye-health/encounter/{self.encounter.pk}/", payload, format="json"
        )
        self.assertIn(response.status_code, {200, 201}, response.data)
        return EyeHealthScreeningReport.objects.get(encounter=self.encounter)

    def preview_and_finalize(self, report):
        preview = self.client.post(f"/api/reports/eye-health/{report.pk}/preview/", {}, format="json")
        self.assertEqual(preview.status_code, 200, getattr(preview, "data", None))
        report.refresh_from_db()
        finalized = self.client.post(
            f"/api/reports/eye-health/{report.pk}/finalize/",
            {"expected_version": report.lock_version, "signoff_confirmed": True}, format="json",
        )
        self.assertEqual(finalized.status_code, 200, finalized.data)
        report.refresh_from_db()
        return report

    def test_draft_preview_finalization_freezes_outcome_advice_location_and_identity(self):
        report = self.preview_and_finalize(self.save_draft())
        version = report.finalized_version
        self.assertEqual(version.clinical_snapshot["outcome"], "routine_eye_examination")
        self.assertEqual(version.clinical_snapshot["advice"], "Arrange a synthetic routine examination.")
        self.assertEqual(version.clinical_snapshot["assessment_location"]["site_name"], "Synthetic client site")
        self.assertEqual(version.clinician_snapshot["user_id"], self.optometrist.pk)
        self.assertEqual(version.clinical_snapshot["limitation"], LIMITATION)
        self.assertNotIn("no glaucoma", str(version.clinical_snapshot).lower())
        with get_private_clinical_storage().open(version.pdf_object_key, "rb") as source:
            self.assertEqual(len(PdfReader(BytesIO(source.read())).pages), 1)
        denied = self.client.post(
            f"/api/reports/eye-health/encounter/{self.encounter.pk}/",
            {"advice": "Silent overwrite"}, format="json",
        )
        self.assertEqual(denied.status_code, 409)
        repeated = self.client.post(
            f"/api/reports/eye-health/{report.pk}/finalize/",
            {"expected_version": report.lock_version - 1, "signoff_confirmed": True}, format="json",
        )
        self.assertEqual(repeated.status_code, 200, repeated.data)
        self.assertEqual(report.versions.count(), 1)
        admin = self.make_user("eye-final-admin", {"clinic_admin"}, self.clinic, self.branch, profile=True)
        self.client.force_authenticate(admin)
        unauthorized_retry = self.client.post(
            f"/api/reports/eye-health/{report.pk}/finalize/",
            {"expected_version": report.lock_version, "signoff_confirmed": True}, format="json",
        )
        self.assertEqual(unauthorized_retry.status_code, 403)

    def test_patient_and_clinician_formats_share_version_and_clean_gate(self):
        report = self.save_draft(structured_findings={
            "fundus_quality": "good",
            "right": {"visual_field_reliability": "reliable", "visual_field_result": "within_expected_limits", "ght": "within_normal_limits", "vfi": "98"},
            "left": {"visual_field_reliability": "reliable", "visual_field_result": "within_expected_limits", "ght": "within_normal_limits", "vfi": "97"},
        })
        for audience in ("patient", "clinician"):
            preview = self.client.post(
                f"/api/reports/eye-health/{report.pk}/preview/",
                {"report_format": audience}, format="json",
            )
            self.assertEqual(preview.status_code, 200)
            text = " ".join(page.extract_text() or "" for page in PdfReader(BytesIO(preview.content)).pages)
            self.assertIn("DRAFT — NOT FOR DISTRIBUTION", text)
            if audience == "patient":
                self.assertNotIn("Detailed clinical findings", text)
                self.assertNotIn("Visual field index", text)
            else:
                self.assertIn("Clinician Report", text)
                self.assertIn("Detailed clinical findings", text)
                self.assertIn("Visual field index", text)
        report.refresh_from_db()
        report = self.preview_and_finalize(report)
        version_id = report.finalized_version_id
        blocked_clean = self.client.get(
            f"/api/reports/eye-health/{report.pk}/pdf/?report_format=patient"
        )
        self.assertEqual(blocked_clean.status_code, 200)
        blocked_text = " ".join(page.extract_text() or "" for page in PdfReader(BytesIO(blocked_clean.content)).pages)
        self.assertIn("DRAFT — NOT FOR DISTRIBUTION", blocked_text)
        EncounterFinancialRecord.objects.update_or_create(
            encounter=self.encounter,
            defaults={
                "status": EncounterFinancialRecord.Status.CAPTURED,
                "financially_releasable": True,
                "captured_at": timezone.now(),
            },
        )
        clean_patient = self.client.get(
            f"/api/reports/eye-health/{report.pk}/pdf/?report_format=patient"
        )
        clean_clinician = self.client.get(
            f"/api/reports/eye-health/{report.pk}/pdf/?report_format=clinician"
        )
        for response in (clean_patient, clean_clinician):
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(
                "DRAFT — NOT FOR DISTRIBUTION",
                " ".join(page.extract_text() or "" for page in PdfReader(BytesIO(response.content)).pages),
            )
        report.refresh_from_db()
        self.assertEqual(report.finalized_version_id, version_id)
        self.assertEqual(report.versions.count(), 1)

    def test_hospital_requires_exact_release_and_repeated_release_does_not_charge(self):
        referral = HospitalReferral.objects.create(
            referral_id="SNT-REF-EYE-RELEASE", source_hospital=self.hospital,
            patient=self.patient, first_name=self.patient.first_name, last_name=self.patient.last_name,
            reason_for_referral="Synthetic targeted screening", matched_clinic=self.clinic,
        )
        self.encounter.source_type = "hospital_referral"
        self.encounter.payment_responsibility = "hospital"
        self.encounter.originating_organization = self.hospital
        self.encounter.hospital_referral = referral
        self.encounter.save()
        report = self.preview_and_finalize(self.save_draft())
        EncounterFinancialRecord.objects.update_or_create(
            encounter=self.encounter,
            defaults={
                "service_pathway": EncounterFinancialRecord.ServicePathway.HOSPITAL_REFERRED,
                "status": EncounterFinancialRecord.Status.CAPTURED,
                "financially_releasable": True,
                "captured_at": timezone.now(),
            },
        )
        hospital_user = self.make_user("eye-hospital-release", {"hospital_admin"}, self.hospital)
        self.client.force_authenticate(hospital_user)
        denied = self.client.get(f"/api/reports/eye-health/{report.pk}/pdf/?report_format=clinician")
        self.assertEqual(denied.status_code, 403)
        ops = self.make_user("eye-release-ops", {"ops_admin"})
        UserSecurityProfile.objects.create(user=ops, is_internal_sentinel_staff=True)
        self.client.force_authenticate(ops)
        first = self.client.post(f"/api/reports/eye-health/{report.pk}/release-hospital/", {}, format="json")
        self.assertEqual(first.status_code, 200, getattr(first, "data", None))
        second = self.client.post(f"/api/reports/eye-health/{report.pk}/release-hospital/", {}, format="json")
        self.assertEqual(second.status_code, 200)
        report.refresh_from_db()
        self.assertEqual(report.hospital_released_version_id, report.finalized_version_id)
        self.client.force_authenticate(hospital_user)
        allowed = self.client.get(f"/api/reports/eye-health/{report.pk}/pdf/?report_format=clinician")
        self.assertEqual(allowed.status_code, 200)

    def test_controlled_correction_preserves_first_version(self):
        report = self.preview_and_finalize(self.save_draft())
        original = report.finalized_version
        opened = self.client.post(
            f"/api/reports/eye-health/{report.pk}/correction/",
            {"reason": "Synthetic correction reason"}, format="json",
        )
        self.assertEqual(opened.status_code, 200, opened.data)
        report.refresh_from_db()
        self.assertEqual(report.correction_source_version_id, original.pk)
        repeated = self.client.post(
            f"/api/reports/eye-health/{report.pk}/correction/",
            {"reason": "Synthetic correction reason"}, format="json",
        )
        self.assertEqual(repeated.status_code, 200, repeated.data)
        report = self.save_draft(advice="Corrected synthetic advice.")
        report = self.preview_and_finalize(report)
        self.assertEqual(report.versions.count(), 2)
        corrected = report.finalized_version
        self.assertEqual(corrected.source_version_id, original.pk)
        self.assertEqual(corrected.purpose, "correction")
        self.assertEqual(corrected.correction_note, "Synthetic correction reason")
        original.refresh_from_db()
        self.assertEqual(original.clinical_snapshot["advice"], "Arrange a synthetic routine examination.")

    def test_selected_visual_field_pdfs_preserve_order_pages_and_invalid_blocks(self):
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        selected_image = ImageUpload.objects.create(
            image_upload_id="IMG-SELECTED", encounter=self.encounter, patient=self.patient,
            eye_laterality="right", image_file=SimpleUploadedFile("selected.png", png, content_type="image/png"),
        )
        unselected_image = ImageUpload.objects.create(
            image_upload_id="IMG-UNSELECTED", encounter=self.encounter, patient=self.patient,
            eye_laterality="left", image_file=SimpleUploadedFile("unselected.png", png, content_type="image/png"),
        )
        first = OcularInvestigation.objects.create(
            investigation_id="INV-VF-1", encounter=self.encounter, investigation_type="visual_field",
            laterality="right", original_filename="right.pdf",
            file=SimpleUploadedFile("right.pdf", synthetic_pdf(2), content_type="application/pdf"),
        )
        second = OcularInvestigation.objects.create(
            investigation_id="INV-VF-2", encounter=self.encounter, investigation_type="visual_field",
            laterality="left", original_filename="left.pdf",
            file=SimpleUploadedFile("left.pdf", synthetic_pdf(1), content_type="application/pdf"),
        )
        report = self.preview_and_finalize(self.save_draft(
            selected_fundus_upload_ids=[selected_image.pk],
            selected_visual_field_investigation_ids=[second.pk, first.pk],
        ))
        manifest = report.finalized_version.attachment_manifest
        fundus = [item for item in manifest if item["kind"] == "fundus"]
        self.assertEqual([item["id"] for item in fundus], [selected_image.pk])
        self.assertNotIn(unselected_image.pk, [item["id"] for item in fundus])
        visual = [item for item in manifest if item["kind"] == "visual_field_pdf"]
        self.assertEqual([item["id"] for item in visual], [second.pk, first.pk])
        self.assertEqual([item["page_count"] for item in visual], [1, 2])
        with get_private_clinical_storage().open(report.finalized_version.pdf_object_key, "rb") as source:
            merged = PdfReader(BytesIO(source.read()))
        self.assertEqual(len(merged.pages), 5)
        other = ScreeningEncounter.objects.create(
            encounter_id="ENC-EYE-BAD-PDF", patient=self.patient, encounter_date=date.today(),
            programme="eye_health_screening", service_package=ScreeningEncounter.ServicePackage.EYE_HEALTH_SCREENING,
            service_branch=self.branch,
        )
        bad = OcularInvestigation.objects.create(
            investigation_id="INV-BAD", encounter=other, investigation_type="visual_field",
            laterality="right", original_filename="bad.pdf",
            file=SimpleUploadedFile("bad.pdf", b"not a pdf", content_type="application/pdf"),
        )
        response = self.client.post(
            f"/api/reports/eye-health/encounter/{other.pk}/",
            {"outcome": "inconclusive_repeat", "advice": "Repeat.",
             "selected_visual_field_investigation_ids": [bad.pk]}, format="json",
        )
        self.assertIn(response.status_code, {200, 201})
        bad_report = EyeHealthScreeningReport.objects.get(encounter=other)
        blocked = self.client.post(f"/api/reports/eye-health/{bad_report.pk}/preview/", {}, format="json")
        self.assertEqual(blocked.status_code, 400)
        self.assertFalse(bad_report.versions.exists())

    def test_exact_role_clinic_and_branch_boundaries(self):
        report = self.save_draft()
        users = [
            self.make_user("eye-admin", {"clinic_admin"}, self.clinic, self.branch, profile=True),
            self.make_user("eye-wrong-branch", {"optometrist"}, self.clinic, self.other_branch, profile=True),
            self.make_user("eye-super", set(), self.clinic, self.branch, profile=True, superuser=True),
            self.make_user("eye-other", {"optometrist"}, self.other_clinic, profile=True),
            self.make_user("eye-hospital", {"hospital_admin"}, self.hospital, profile=True),
        ]
        for user in users:
            self.client.force_authenticate(user)
            response = self.client.post(
                f"/api/reports/eye-health/encounter/{self.encounter.pk}/",
                {"advice": "Denied"}, format="json",
            )
            self.assertIn(response.status_code, {400, 403, 404})
        combined = self.make_user(
            "eye-combined-role", {"clinic_admin", "optometrist"}, self.clinic, self.branch, profile=True
        )
        self.client.force_authenticate(combined)
        allowed = self.client.post(
            f"/api/reports/eye-health/encounter/{self.encounter.pk}/",
            {"advice": "Combined-role correction", "expected_version": report.lock_version}, format="json",
        )
        self.assertEqual(allowed.status_code, 200, allowed.data)
        report.refresh_from_db()

        super_optometrist = self.make_user(
            "eye-super-opto", {"clinic_admin", "ops_admin", "finance_admin", "optometrist"},
            self.clinic, self.branch, profile=True, superuser=True,
        )
        self.client.force_authenticate(super_optometrist)
        allowed_super = self.client.post(
            f"/api/reports/eye-health/encounter/{self.encounter.pk}/",
            {"advice": "Explicit superuser clinical role", "expected_version": report.lock_version}, format="json",
        )
        self.assertEqual(allowed_super.status_code, 200, allowed_super.data)

        self.client.force_authenticate(user=None)
        anonymous = self.client.get(f"/api/reports/eye-health/encounter/{self.encounter.pk}/")
        self.assertIn(anonymous.status_code, {401, 403})

    def test_stale_draft_save_is_a_controlled_conflict(self):
        report = self.save_draft()
        response = self.client.post(
            f"/api/reports/eye-health/encounter/{self.encounter.pk}/",
            {"advice": "Stale overwrite", "expected_version": report.lock_version - 1},
            format="json",
        )
        self.assertEqual(response.status_code, 409)
        report.refresh_from_db()
        self.assertEqual(report.advice, "Arrange a synthetic routine examination.")

    def test_combined_bundle_keeps_issued_diabetic_report_first(self):
        self.encounter.service_package = ScreeningEncounter.ServicePackage.COMBINED
        self.encounter.programme = "combined_assessment"
        self.encounter.save()
        eye_report = self.preview_and_finalize(self.save_draft())
        diabetic_pdf = labelled_pdf("UNCHANGED DIABETIC REPORT COMPONENT")
        key = "clinical-documents/reports/synthetic-diabetic.pdf"
        get_private_clinical_storage().save(key, ContentFile(diabetic_pdf))
        diabetic_report = StructuredReport.objects.create(
            report_id="RPT-COMBINED-DIABETIC", encounter=self.encounter, patient=self.patient,
            review_date=date.today(), report_status="issued",
        )
        version = StructuredReportVersion.objects.create(
            report=diabetic_report, version_number=1, clinical_snapshot={"synthetic": True},
            checksum_sha256=hashlib.sha256(b"synthetic").hexdigest(), purpose="initial",
            pdf_object_key=key, pdf_checksum_sha256=hashlib.sha256(diabetic_pdf).hexdigest(),
            pdf_size=len(diabetic_pdf), editor=self.optometrist,
        )
        StructuredReport.objects.filter(pk=diabetic_report.pk).update(issued_version=version)
        self.encounter.refresh_from_db()
        self.encounter.update_status_from_related_records()
        EncounterFinancialRecord.objects.update_or_create(
            encounter=self.encounter,
            defaults={
                "status": EncounterFinancialRecord.Status.CAPTURED,
                "financially_releasable": True,
                "captured_at": timezone.now(),
            },
        )
        response = self.client.get(
            f"/api/reports/eye-health/combined/{self.encounter.pk}/bundle/"
        )
        self.assertEqual(response.status_code, 200)
        bundle = PdfReader(BytesIO(response.content))
        self.assertIn("UNCHANGED DIABETIC REPORT COMPONENT", bundle.pages[0].extract_text())
        self.assertIn("Targeted Retinal and Glaucoma-Risk Screening Report", bundle.pages[1].extract_text())
        eye_report.refresh_from_db()

    def test_targeted_title_limitation_and_safe_deterministic_wording(self):
        findings = {
            "fundus_quality": "mildly_limited",
            "optic_disc": ["possible_physiological_cupping"],
            "retinal_vessels": ["no_concerning_feature"],
            "retina_macula": ["no_visible_abnormality"],
            "right": {"visual_field_reliability": "reduced_reliability", "visual_field_result": "essentially_full", "ght": "borderline"},
            "left": {"visual_field_reliability": "reliable", "visual_field_result": "within_expected_limits", "ght": "within_normal_limits"},
            "iop_interpretation": "within_expected_range",
            "visual_acuity_interpretation": "within_expected_range",
            "clinical_interpretation": "no_immediate_concern",
        }
        wording = generate_suggested_wording(findings)
        self.assertIn("subtle changes may not be detectable", wording)
        self.assertIn("does not exclude glaucoma", wording)
        self.assertIn("No immediate concern was identified within the areas assessed", wording)
        for prohibited in ("Eyes normal", "No ocular disease", "No glaucoma", "No diabetic retinopathy"):
            self.assertNotIn(prohibited, wording)
        report = self.preview_and_finalize(self.save_draft(
            outcome="no_immediate_concern", structured_findings=findings,
            generated_suggestion="Client text must not be trusted",
        ))
        snapshot = report.finalized_version.clinical_snapshot
        self.assertEqual(snapshot["structured_findings"]["fundus_quality"], "mildly_limited")
        self.assertEqual(snapshot["clinical_summary"], "No immediate concern was identified within the areas assessed.")
        self.assertNotEqual(snapshot["generated_suggestion"], "Client text must not be trusted")
        with get_private_clinical_storage().open(report.finalized_version.pdf_object_key, "rb") as source:
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(source.read())).pages)
        self.assertIn("Targeted Retinal and Glaucoma-Risk Screening Report", text)
        self.assertIn(LIMITATION, text.replace("\n", " "))
        self.assertNotIn("No diabetic retinopathy", text)

    def test_explicit_regeneration_does_not_happen_on_ordinary_save(self):
        report = self.save_draft(
            structured_findings={"fundus_quality": "good"},
            clinical_summary="Clinician-edited wording must survive.",
        )
        response = self.client.post(
            f"/api/reports/eye-health/encounter/{self.encounter.pk}/",
            {
                "expected_version": report.lock_version,
                "structured_findings": {"fundus_quality": "ungradable"},
                "clinical_summary": "Clinician-edited wording must survive.",
            }, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["clinical_summary"], "Clinician-edited wording must survive.")
        response = self.client.post(
            f"/api/reports/eye-health/encounter/{self.encounter.pk}/",
            {
                "expected_version": response.data["lock_version"],
                "structured_findings": {"fundus_quality": "ungradable"},
                "regenerate_suggested_wording": True,
            }, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn("could not be assessed reliably", response.data["clinical_summary"])

    def test_contradictory_or_ungradable_reassurance_is_blocked(self):
        self.client.force_authenticate(self.optometrist)
        contradictory = self.client.post(
            f"/api/reports/eye-health/encounter/{self.encounter.pk}/",
            {"structured_findings": {"optic_disc": ["no_concerning_feature", "disc_haemorrhage"]}},
            format="json",
        )
        self.assertEqual(contradictory.status_code, 400)
        report = self.save_draft(
            outcome="no_immediate_concern",
            structured_findings={"fundus_quality": "ungradable"},
        )
        preview = self.client.post(f"/api/reports/eye-health/{report.pk}/preview/", {}, format="json")
        self.assertEqual(preview.status_code, 400)
        self.assertIn("cannot support the reassuring outcome", str(preview.data))

    def test_package_correction_requires_confirmation_preserves_data_and_locks_after_version(self):
        OcularDiagnosticAssessment.objects.create(
            encounter=self.encounter, presenting_complaint="Synthetic preserved complaint"
        )
        existing_investigation = OcularInvestigation.objects.create(
            investigation_id="INV-PRESERVE", encounter=self.encounter, investigation_type="visual_field",
            laterality="both", file=SimpleUploadedFile("preserve.pdf", synthetic_pdf()),
        )
        self.encounter.service_package = ScreeningEncounter.ServicePackage.COMPREHENSIVE_OCULAR
        self.encounter.programme = "ocular_diagnostics"
        self.encounter.save()
        self.client.force_authenticate(self.optometrist)
        url = f"/api/encounters/{self.encounter.pk}/service-package/"
        missing = self.client.post(url, {
            "service_package": ScreeningEncounter.ServicePackage.COMBINED,
            "reason": "Synthetic package correction",
        }, format="json")
        self.assertEqual(missing.status_code, 400)
        changed = self.client.post(url, {
            "service_package": ScreeningEncounter.ServicePackage.COMBINED,
            "reason": "Synthetic package correction", "diabetic_confirmed": True,
        }, format="json")
        self.assertEqual(changed.status_code, 200, changed.data)
        self.encounter.refresh_from_db()
        self.assertEqual(self.encounter.programme, "combined_assessment")
        self.assertEqual(self.encounter.ocular_assessment.presenting_complaint, "Synthetic preserved complaint")
        self.assertTrue(self.encounter.ocular_investigations.filter(pk=existing_investigation.pk).exists())
        self.assertFalse(StructuredReport.objects.filter(encounter=self.encounter).exists())
        report = self.preview_and_finalize(self.save_draft())
        locked = self.client.post(url, {
            "service_package": ScreeningEncounter.ServicePackage.EYE_HEALTH_SCREENING,
            "reason": "Must be locked",
        }, format="json")
        self.assertEqual(locked.status_code, 409)
        opened = self.client.post(
            f"/api/reports/eye-health/{report.pk}/correction/",
            {"reason": "Correct package through controlled report correction"}, format="json",
        )
        self.assertEqual(opened.status_code, 200, opened.data)
        controlled = self.client.post(url, {
            "service_package": ScreeningEncounter.ServicePackage.EYE_HEALTH_SCREENING,
            "reason": "Corrected within versioned report workflow",
        }, format="json")
        self.assertEqual(controlled.status_code, 200, controlled.data)
        report.finalized_version.refresh_from_db()
        self.assertEqual(
            report.finalized_version.clinical_snapshot["service_package"],
            ScreeningEncounter.ServicePackage.COMBINED,
        )
