from types import SimpleNamespace

from django.test import SimpleTestCase

from encounters.ocular_report_pdf import _signer_snapshot_details


class OcularSignerSnapshotTests(SimpleTestCase):
    def test_signed_snapshot_is_authoritative_over_live_user_identity(self):
        live_user = SimpleNamespace(
            username="changed-live-user",
            get_full_name=lambda: "Changed Live Name",
        )
        assessment = SimpleNamespace(
            signer_snapshot={
                "display_name": "Dr Original Clinician",
                "signature_name": "Dr Original Signature",
                "professional_role": "Optometrist",
                "registration_number": "GOC-123",
                "registration_body": "GOC",
                "qualifications": "BSc Optom",
            },
            completed_by=live_user,
        )

        signer = _signer_snapshot_details(assessment)

        self.assertEqual(signer["name"], "Dr Original Signature")
        self.assertEqual(signer["role"], "Optometrist")
        self.assertEqual(signer["registration_number"], "GOC-123")
        self.assertEqual(signer["registration_body"], "GOC")
        self.assertEqual(signer["qualifications"], "BSc Optom")
        self.assertNotEqual(signer["name"], "Changed Live Name")

    def test_legacy_record_falls_back_to_completed_by(self):
        live_user = SimpleNamespace(
            username="legacy-clinician",
            get_full_name=lambda: "Legacy Clinician",
        )
        assessment = SimpleNamespace(signer_snapshot={}, completed_by=live_user)

        signer = _signer_snapshot_details(assessment)

        self.assertEqual(signer["name"], "Legacy Clinician")
