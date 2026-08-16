import hashlib
import tempfile
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.test import APIClient

from audit.models import PatientTimelineEvent
from encounters.models import OcularDiagnosticAssessment, ScreeningEncounter
from organizations.models import Organization, OrganizationBranch, PartnerNotification
from patients.models import MasterPatient, Patient
from referrals.models import HospitalReferral
from reports.models import StructuredReport
from users.models import UserBranchAccess, UserOrganization

from .models import (
    EncounterResponsibleOptometrist,
    OnwardReferral,
    OnwardReferralAccessEvent,
    OnwardReferralAvailability,
    OnwardReferralEvent,
    OnwardReferralVersion,
)
from .services import (
    accept_responsibility,
    create_referral,
    eligibility,
    finalize_referral,
    make_available,
    source_is_stale,
    supersede_referral,
)
from uploads.storage import get_private_clinical_storage


@override_settings(
    CLINICAL_ASSETS_USE_PRIVATE_OBJECT_STORAGE=False,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class OnwardReferralTests(TestCase):
    def setUp(self):
        self.private_root = tempfile.TemporaryDirectory()
        self.addCleanup(self.private_root.cleanup)
        self.settings_override = override_settings(
            PRIVATE_CLINICAL_ASSETS_ROOT=self.private_root.name,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.client = APIClient()

        self.clinic = self.organization("CL-ORF", "Origin Clinic", "clinic")
        self.other_clinic = self.organization("CL-OTHER", "Other Clinic", "clinic")
        self.hospital = self.organization("H-ORF", "Origin Hospital", "hospital")
        self.receiving_hospital = self.organization("H-REC", "Receiving Hospital", "hospital")
        self.other_hospital = self.organization("H-OTHER", "Other Hospital", "hospital")
        self.branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="MAIN", name="Main Branch",
            is_head_office=True,
        )
        self.other_branch = OrganizationBranch.objects.create(
            organization=self.clinic, branch_code="OTHER", name="Other Branch",
        )
        self.optometrist = self.user("orf-optometrist", "optometrist", self.clinic, self.branch)
        self.other_optometrist = self.user("orf-optometrist-2", "optometrist", self.clinic, self.branch)
        self.wrong_branch_optometrist = self.user("orf-wrong-branch", "optometrist", self.clinic, self.other_branch)
        self.admin = self.user("orf-admin", "clinic_admin", self.clinic, self.branch)
        self.admin.groups.add(Group.objects.get_or_create(name="onward_referral_distributor")[0])
        self.hospital_user = self.user("orf-hospital", "hospital_admin", self.hospital)
        self.receiving_user = self.user("orf-receiving", "hospital_admin", self.receiving_hospital)
        self.other_hospital_user = self.user("orf-other-hospital", "hospital_admin", self.other_hospital)
        self.finance_user = self.user("orf-finance", "finance_admin", self.clinic, self.branch)
        self.ops_user = self.user("orf-ops", "ops_admin", None)
        self.superuser = get_user_model().objects.create_superuser(
            "orf-superuser", "super@example.invalid", "test-pass"
        )
        self.superuser.groups.add(Group.objects.get_or_create(name="optometrist")[0])

        self.master = MasterPatient.objects.create(
            sentinel_patient_id="SNT-P-ORF", first_name="Synthetic",
            last_name="Patient", date_of_birth=date(1980, 1, 2), sex="female",
            primary_phone="08000000000",
        )
        self.patient = Patient.objects.create(
            patient_id="PAT-ORF", master_patient=self.master,
            first_name="Synthetic", last_name="Patient",
            date_of_birth=date(1980, 1, 2), sex="female",
            phone="08000000000", email="excluded@example.invalid",
            address="Excluded address", assigned_clinic=self.clinic,
            assigned_branch=self.branch, consent_status="completed",
        )
        self.inbound = HospitalReferral.objects.create(
            referral_id="SNT-REF-INBOUND", source_hospital=self.hospital,
            patient=self.patient, matched_clinic=self.clinic,
            matched_branch=self.branch, first_name="Synthetic", last_name="Patient",
            dob=date(1980, 1, 2), patient_sex="female", hospital_mrn="MRN-SYNTH",
            reason_for_referral="Synthetic assessment", referral_status="in_clinic",
            report_ready=False,
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-ORF", patient=self.patient,
            encounter_date=date(2026, 8, 15), programme="combined_assessment",
            source_type="hospital_referral", workflow_route="sentinel_managed",
            originating_organization=self.hospital, service_branch=self.branch,
            hospital_referral=self.inbound, screening_status="completed",
            left_corrected_pinhole_va="6/9", right_corrected_pinhole_va="6/12",
            iop_before_dilation_left="14", iop_before_dilation_right="15",
            dilation_drops_used="Tropicamide",
        )
        self.ocular = OcularDiagnosticAssessment.objects.create(
            encounter=self.encounter, management_outcome="refer_urgent",
            completed_at=timezone.now(), completed_by=self.optometrist,
            presenting_complaint="Reduced vision", impression="Synthetic impression",
            management_plan="Refer onward",
        )
        self.retinal = StructuredReport.objects.create(
            report_id="RPT-ORF", encounter=self.encounter, patient=self.patient,
            review_date=date(2026, 8, 15), urgency_outcome="ophthalmology_required",
            report_status="draft", recommendation="Professional referral",
        )
        # Synthetic fixture represents an assessment already completed under the
        # encounter workflow, independently of later report distribution state.
        self.encounter.screening_status = "completed"
        self.encounter.save(update_fields=["screening_status", "updated_at"])

    @staticmethod
    def organization(code, name, kind):
        return Organization.objects.create(
            clinic_id=code, name=name, organization_type=kind,
        )

    @staticmethod
    def user(username, role, organization=None, branch=None):
        user = get_user_model().objects.create_user(username=username, password="test-pass")
        user.groups.add(Group.objects.get_or_create(name=role)[0])
        if organization:
            UserOrganization.objects.create(user=user, organization=organization)
        if branch:
            UserBranchAccess.objects.create(
                user=user, branch=branch, has_all_branch_access=False,
                is_default=True,
            )
        return user

    def accept(self, user=None, reason=""):
        return accept_responsibility(
            user=user or self.optometrist, encounter=self.encounter,
            clinician_name="Dr Synthetic Optometrist", professional_role="Optometrist",
            registration_number="ODORBN-SYNTH-1", reason=reason,
        )

    def draft(self, *, route="originating_hospital", sources=None, user=None,
              urgency="urgent", include_phone=False, recipient=None):
        user = user or self.optometrist
        if not hasattr(self.encounter, "onward_responsibility"):
            self.accept(user)
        return create_referral(
            user=user, encounter=self.encounter,
            clinical_sources=sources if sources is not None else ["ocular", "retinal"],
            route=route, recipient_organization_id=getattr(recipient, "id", None),
            recipient_department="Ophthalmology",
            data={
                "urgency": urgency,
                "referral_reason": "Professional onward assessment required",
                "requested_specialist_action": "Please assess and manage",
                "relevant_history": "Selected relevant history only",
                "pertinent_findings": "Selected pertinent findings",
                "professional_impression": "Professional impression",
                "management_provided": "Safety advice provided",
                "include_patient_phone": include_phone,
            },
        )

    def finalize(self, **kwargs):
        referral = self.draft(**kwargs)
        finalize_referral(user=self.optometrist, referral=referral)
        referral.refresh_from_db()
        return referral

    def test_eligibility_requires_completed_professional_record_and_responsibility(self):
        self.assertEqual(set(eligibility(self.encounter)["eligible_sources"]), {"ocular", "retinal"})
        self.assertFalse(eligibility(self.encounter)["eligible"])
        self.accept()
        self.assertTrue(eligibility(self.encounter)["eligible"])
        self.encounter.screening_status = "under_review"
        self.encounter.save(update_fields=["screening_status", "updated_at"])
        self.assertFalse(eligibility(self.encounter)["eligible"])

    def test_ai_alone_is_never_eligible(self):
        self.ocular.delete()
        self.retinal.delete()
        self.accept()
        self.assertEqual(eligibility(self.encounter)["eligible_sources"], [])
        with self.assertRaises(ValidationError):
            self.draft()

    def test_combined_source_selection_is_explicit_and_validated(self):
        self.accept()
        with self.assertRaises(ValidationError):
            self.draft(sources=[])
        referral = self.draft(sources=["retinal"])
        self.assertEqual(referral.clinical_sources, ["retinal"])
        self.assertIsNone(referral.ocular_assessment)

    def test_responsibility_exact_role_branch_takeover_and_audit(self):
        first = self.accept()
        self.assertEqual(first.original_optometrist, self.optometrist)
        self.assertTrue(PatientTimelineEvent.objects.filter(
            event_type="onward_responsibility_accepted"
        ).exists())
        with self.assertRaises(ValidationError):
            self.accept(self.other_optometrist)
        taken = self.accept(self.other_optometrist, "Case formally handed over")
        self.assertEqual(taken.optometrist, self.other_optometrist)
        self.assertEqual(taken.original_optometrist, self.optometrist)
        self.assertEqual(taken.previous_optometrist, self.optometrist)
        self.assertTrue(PatientTimelineEvent.objects.filter(
            event_type="onward_responsibility_taken_over"
        ).exists())
        for user in (self.admin, self.ops_user, self.finance_user, self.hospital_user, self.superuser, self.wrong_branch_optometrist):
            with self.assertRaises(PermissionDenied):
                self.accept(user, "Unauthorized attempt")

    def test_responsibility_audit_failure_rolls_back(self):
        with patch("onward_referrals.services.record_patient_event", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.accept()
        self.assertFalse(EncounterResponsibleOptometrist.objects.filter(encounter=self.encounter).exists())

    def test_inbound_referral_is_unchanged_and_reference_is_separate(self):
        before = (self.inbound.referral_id, self.inbound.referral_status, self.inbound.report_ready, self.inbound.report_id)
        referral = self.finalize()
        self.inbound.refresh_from_db()
        self.assertEqual(before, (self.inbound.referral_id, self.inbound.referral_status, self.inbound.report_ready, self.inbound.report_id))
        self.assertNotEqual(referral.referral_reference, self.inbound.referral_id)
        self.assertTrue(referral.referral_reference.startswith("SNT-ORF-"))

    def test_draft_editing_author_only_and_finalized_content_is_immutable(self):
        referral = self.draft()
        self.client.force_authenticate(self.optometrist)
        response = self.client.patch(
            f"/api/onward-referrals/{referral.referral_uuid}/",
            {"professional_impression": "Updated professional impression"}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.client.force_authenticate(self.admin)
        denied = self.client.patch(
            f"/api/onward-referrals/{referral.referral_uuid}/",
            {"urgency": "emergency"}, format="json",
        )
        self.assertEqual(denied.status_code, 403)
        finalize_referral(user=self.optometrist, referral=referral)
        version = OnwardReferralVersion.objects.get(referral=referral)
        version.professional_impression = "Rewritten"
        with self.assertRaises(DjangoValidationError):
            version.save()
        with self.assertRaises(DjangoValidationError):
            version.delete()

    def test_urgency_floor_and_emergency_confirmation(self):
        self.accept()
        initial_report_status = self.retinal.report_status
        too_low = self.draft(urgency="routine")
        with self.assertRaises(ValidationError):
            finalize_referral(user=self.optometrist, referral=too_low)
        too_low.lifecycle = "voided"
        too_low.save(update_fields=["lifecycle", "updated_at"])
        emergency = self.draft(urgency="emergency")
        with self.assertRaises(ValidationError):
            finalize_referral(user=self.optometrist, referral=emergency)
        version = emergency.current_version
        version.emergency_escalation_confirmed = True
        version.emergency_escalation_method = "Immediate hospital transfer advice"
        version.emergency_escalation_note = "Patient instructed to attend emergency care now."
        version.emergency_escalation_by = self.optometrist
        version.emergency_escalation_at = timezone.now()
        version.save()
        finalize_referral(user=self.optometrist, referral=emergency)
        self.retinal.refresh_from_db()
        self.assertEqual(self.retinal.report_status, initial_report_status)

    def test_finalization_is_idempotent_private_and_minimized(self):
        referral = self.draft(include_phone=True)
        referral.current_version.relevant_history = "Selected <history> & review only"
        referral.current_version.save(update_fields=["relevant_history", "updated_at"])
        first = finalize_referral(user=self.optometrist, referral=referral)
        second = finalize_referral(user=self.optometrist, referral=referral)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OnwardReferralEvent.objects.filter(referral=referral, event_type="finalized").count(), 1)
        self.assertTrue(first.pdf_object_key.startswith("clinical-documents/onward-referrals/"))
        self.assertNotIn(self.patient.first_name, first.pdf_object_key)
        self.assertEqual(len(first.pdf_checksum_sha256), 64)
        self.assertEqual(first.patient_snapshot["phone"], self.patient.phone)
        self.assertNotIn("email", first.patient_snapshot)
        self.assertNotIn("address", first.patient_snapshot)
        self.assertEqual(first.author_snapshot["registration_number"], "ODORBN-SYNTH-1")
        with get_private_clinical_storage().open(first.pdf_object_key, "rb") as stored:
            rendered = stored.read()
        self.assertEqual(hashlib.sha256(rendered).hexdigest(), first.pdf_checksum_sha256)
        self.assertTrue(rendered.startswith(b"%PDF"))

    def test_finalize_audit_failure_rolls_back_database_and_storage(self):
        referral = self.draft()
        with patch.object(OnwardReferralEvent.objects, "create", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                finalize_referral(user=self.optometrist, referral=referral)
        referral.current_version.refresh_from_db()
        self.assertEqual(referral.current_version.status, "draft")
        self.assertEqual(referral.current_version.pdf_object_key, "")

    def test_supersession_preserves_pdf_and_warns_when_source_changes(self):
        referral = self.finalize()
        old = referral.current_version
        old_checksum = old.pdf_checksum_sha256
        self.retinal.recommendation = "Corrected professional recommendation"
        self.retinal.save(update_fields=["recommendation", "updated_at"])
        old.refresh_from_db()
        self.assertTrue(source_is_stale(old))
        with self.assertRaises(ValidationError):
            make_available(
                user=self.optometrist, referral=referral,
                idempotency_key="stale-version",
            )
        new = supersede_referral(user=self.optometrist, referral=referral, reason="Professional report corrected")
        self.assertEqual(new.supersedes, old)
        self.assertEqual(new.version_number, 2)
        new.professional_impression = "Corrected impression"
        new.save(update_fields=["professional_impression", "updated_at"])
        finalize_referral(user=self.optometrist, referral=referral)
        old.refresh_from_db()
        self.assertEqual(old.status, "superseded")
        self.assertEqual(old.pdf_checksum_sha256, old_checksum)

    def test_originating_and_registered_recipient_isolation(self):
        referral = self.finalize()
        make_available(user=self.optometrist, referral=referral, idempotency_key="origin-1")
        self.client.force_authenticate(self.hospital_user)
        allowed = self.client.get(f"/api/onward-referrals/{referral.referral_uuid}/")
        self.assertEqual(allowed.status_code, 200, allowed.data)
        self.assertNotIn("events", allowed.data)
        self.assertNotIn("responsibility", allowed.data)
        self.client.force_authenticate(self.other_hospital_user)
        self.assertEqual(self.client.get(f"/api/onward-referrals/{referral.referral_uuid}/").status_code, 403)

        direct = self.make_direct_encounter()
        accept_responsibility(
            user=self.optometrist, encounter=direct, clinician_name="Dr Synthetic",
            professional_role="Optometrist", registration_number="ODORBN-SYNTH-1",
        )
        registered = create_referral(
            user=self.optometrist, encounter=direct, clinical_sources=["ocular"],
            route="registered_hospital", recipient_organization_id=self.receiving_hospital.id,
            recipient_department="Ophthalmology",
            data={"urgency": "routine", "referral_reason": "Routine onward care", "requested_specialist_action": "Assess"},
        )
        finalize_referral(user=self.optometrist, referral=registered)
        make_available(user=self.optometrist, referral=registered, idempotency_key="registered-1")
        self.client.force_authenticate(self.receiving_user)
        response = self.client.get(f"/api/onward-referrals/{registered.referral_uuid}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["inbound_referral_reference"], "")
        self.client.force_authenticate(self.hospital_user)
        self.assertEqual(self.client.get(f"/api/onward-referrals/{registered.referral_uuid}/").status_code, 403)

    def test_availability_is_idempotent_and_uses_portal_notification_only(self):
        referral = self.finalize()
        with patch("organizations.notification_service.send_mail") as send_mail:
            first = make_available(user=self.optometrist, referral=referral, idempotency_key="same-key")
            second = make_available(user=self.optometrist, referral=referral, idempotency_key="same-key")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OnwardReferralAvailability.objects.count(), 1)
        self.assertEqual(PartnerNotification.objects.filter(organization=self.hospital).count(), 1)
        send_mail.assert_not_called()

    def test_protected_document_download_and_access_audit(self):
        referral = self.finalize()
        version = referral.current_version
        url = f"/api/onward-referrals/{referral.referral_uuid}/versions/1/document/"
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_authenticate(self.other_hospital_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        make_available(user=self.optometrist, referral=referral, idempotency_key="download-1")
        self.client.force_authenticate(self.hospital_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store, max-age=0")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertNotIn(version.pdf_object_key, str(response.headers))
        self.assertTrue(OnwardReferralAccessEvent.objects.filter(
            version=version, actor=self.hospital_user, action="download"
        ).exists())
        access_event = OnwardReferralAccessEvent.objects.filter(
            version=version, actor=self.hospital_user, action="download"
        ).first()
        access_event.action = "view"
        with self.assertRaises(DjangoValidationError):
            access_event.save()
        with self.assertRaises(DjangoValidationError):
            access_event.delete()
        b"".join(response.streaming_content)
        response.close()
        version.refresh_from_db()
        self.assertEqual(version.status, "finalized")

    def test_hospital_never_sees_unavailable_superseding_draft(self):
        referral = self.finalize()
        make_available(user=self.optometrist, referral=referral, idempotency_key="visible-v1")
        supersede_referral(user=self.optometrist, referral=referral, reason="Correction in progress")
        self.client.force_authenticate(self.hospital_user)
        response = self.client.get(f"/api/onward-referrals/{referral.referral_uuid}/")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([item["version_number"] for item in response.data["versions"]], [1])
        self.assertEqual(response.data["current_version"]["version_number"], 1)

    def test_finance_ops_superuser_and_clinic_admin_have_no_clinical_authority(self):
        self.accept()
        for user in (self.finance_user, self.ops_user, self.superuser, self.admin, self.hospital_user):
            self.client.force_authenticate(user)
            response = self.client.post(
                "/api/onward-referrals/",
                {"encounter": self.encounter.pk, "clinical_sources": ["ocular"],
                 "route": "clinic_download", "urgency": "urgent",
                 "referral_reason": "Reason", "requested_specialist_action": "Assess"},
                format="json",
            )
            self.assertIn(response.status_code, {403, 404})

    def test_clinic_download_does_not_mark_distribution(self):
        referral = self.finalize(route="clinic_download")
        self.assertFalse(OnwardReferralAvailability.objects.exists())
        self.client.force_authenticate(self.admin)
        response = self.client.get(
            f"/api/onward-referrals/{referral.referral_uuid}/versions/1/document/"
        )
        self.assertEqual(response.status_code, 200)
        b"".join(response.streaming_content)
        response.close()
        self.assertFalse(OnwardReferralAvailability.objects.exists())
        self.assertFalse(OnwardReferralEvent.objects.filter(event_type="made_available").exists())

    def test_post_distribution_void_is_blocked(self):
        referral = self.finalize()
        make_available(user=self.optometrist, referral=referral, idempotency_key="cannot-void")
        self.client.force_authenticate(self.optometrist)
        response = self.client.post(
            f"/api/onward-referrals/{referral.referral_uuid}/void/",
            {"reason": "Issued in error"}, format="json",
        )
        self.assertEqual(response.status_code, 400)
        referral.current_version.refresh_from_db()
        self.assertEqual(referral.current_version.status, "finalized")

    def make_direct_encounter(self):
        patient = Patient.objects.create(
            patient_id="PAT-DIRECT-ORF", master_patient=self.master,
            first_name="Direct", last_name="Synthetic", date_of_birth=date(1990, 2, 3),
            sex="male", assigned_clinic=self.clinic, assigned_branch=self.branch,
        )
        encounter = ScreeningEncounter.objects.create(
            encounter_id="ENC-DIRECT-ORF", patient=patient,
            encounter_date=date(2026, 8, 15), programme="ocular_diagnostics",
            source_type="clinic_direct", workflow_route="clinic_managed",
            originating_organization=self.clinic, service_branch=self.branch,
            screening_status="completed",
        )
        OcularDiagnosticAssessment.objects.create(
            encounter=encounter, management_outcome="refer_routine",
            completed_at=timezone.now(), completed_by=self.optometrist,
        )
        return encounter
