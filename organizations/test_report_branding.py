from types import SimpleNamespace
from unittest.mock import MagicMock

from django.test import SimpleTestCase

from .report_branding import resolve_report_branding


def organization(name, policy):
    return SimpleNamespace(
        name=name,
        capability_profile=SimpleNamespace(branding_policy=policy),
    )


def encounter(programme, clinic, hospital=None):
    referral = SimpleNamespace(source_hospital=hospital) if hospital else None
    return SimpleNamespace(
        programme=programme,
        patient=SimpleNamespace(assigned_clinic=clinic),
        hospital_referral=referral,
        originating_organization=hospital or clinic,
        includes_diabetic_screening=programme
        in {"diabetic_screening", "combined_assessment"},
    )


class ReportBrandingResolverTests(SimpleTestCase):
    def test_white_label_hospital_uses_hospital_brand_but_keeps_diabetic_credit(self):
        clinic = organization("Clinic One", "organization_and_sentinel")
        hospital = organization("Hospital One", "organization_only")

        result = resolve_report_branding(
            encounter("diabetic_screening", clinic, hospital),
            clinic=clinic,
        )

        self.assertEqual([brand.name for brand in result.brands], ["Hospital One"])
        self.assertTrue(result.powered_by_sentinel)

    def test_non_white_label_hospital_uses_hospital_and_sentinel(self):
        clinic = organization("Clinic One", "organization_only")
        hospital = organization("Hospital One", "hospital_and_sentinel")

        result = resolve_report_branding(
            encounter("diabetic_screening", clinic, hospital),
            clinic=clinic,
        )

        self.assertEqual(
            [brand.name for brand in result.brands],
            ["Hospital One", "Sentinel"],
        )

    def test_clinic_direct_ocular_uses_clinic_policy_without_forced_credit(self):
        clinic = organization("Clinic One", "organization_only")

        result = resolve_report_branding(
            encounter("ocular_diagnostics", clinic),
            clinic=clinic,
        )

        self.assertEqual([brand.name for brand in result.brands], ["Clinic One"])
        self.assertFalse(result.powered_by_sentinel)

    def test_combined_report_obeys_policy_and_keeps_diabetic_credit(self):
        clinic = organization("Clinic One", "organization_and_sentinel")
        hospital = organization("Hospital One", "hospital_clinic_sentinel")

        result = resolve_report_branding(
            encounter("combined_assessment", clinic, hospital),
            clinic=clinic,
        )

        self.assertEqual(
            [brand.name for brand in result.brands],
            ["Hospital One", "Clinic One", "Sentinel"],
        )
        self.assertTrue(result.powered_by_sentinel)

    def test_structured_report_fallback_can_resolve_hospital(self):
        clinic = organization("Clinic One", "organization_only")
        hospital = organization("Hospital One", "hospital_and_sentinel")
        item = encounter("diabetic_screening", clinic)
        item.hospital_referral = None
        report = SimpleNamespace()
        report.hospital_referrals = MagicMock()
        report.hospital_referrals.select_related.return_value.first.return_value = (
            SimpleNamespace(source_hospital=hospital)
        )

        result = resolve_report_branding(item, clinic=clinic, report=report)

        self.assertIs(result.owner, hospital)
        self.assertEqual([brand.name for brand in result.brands], ["Hospital One", "Sentinel"])
