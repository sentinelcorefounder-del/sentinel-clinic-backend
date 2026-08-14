from datetime import date

from django.test import TestCase

from encounters.models import ScreeningEncounter
from organizations.models import Organization
from patients.models import Patient
from reports.models import StructuredReport
from reports.pdf_renderer import (
    REPORT_FORMATS,
    ROUTINE_EYE_EXAMINATION_DISCLAIMER,
    ReportPDFRenderer,
)


class RetinalReportDisclaimerTests(TestCase):
    def setUp(self):
        self.clinic = Organization.objects.create(
            clinic_id="PDF-DISCLAIMER-CLINIC",
            name="Synthetic Eye Clinic",
            organization_type="clinic",
        )
        self.patient = Patient.objects.create(
            patient_id="PDF-DISCLAIMER-PATIENT",
            first_name="Synthetic",
            last_name="Patient",
            date_of_birth=date(1980, 1, 1),
            sex="female",
            assigned_clinic=self.clinic,
        )
        self.encounter = ScreeningEncounter.objects.create(
            encounter_id="PDF-DISCLAIMER-ENCOUNTER",
            patient=self.patient,
            encounter_date=date(2026, 8, 14),
            source_type="clinic_direct",
            originating_organization=self.clinic,
        )
        self.report = StructuredReport.objects.create(
            report_id="PDF-DISCLAIMER-REPORT",
            encounter=self.encounter,
            patient=self.patient,
            review_date=date(2026, 8, 14),
            urgency_outcome="routine_followup",
            recommendation="Continue routine review.",
            final_clinical_summary="No referable diabetic retinopathy identified.",
            signer_name="Synthetic Clinician",
        )

    def _story_text(self, report_format):
        story = ReportPDFRenderer(self.report, report_format=report_format)._build_story()
        values = []

        def collect(item):
            if hasattr(item, "getPlainText"):
                values.append(item.getPlainText())
            for child in getattr(item, "_content", []):
                collect(child)
            for row in getattr(item, "_cellvalues", []):
                for cell in row:
                    if isinstance(cell, (list, tuple)):
                        for child in cell:
                            collect(child)
                    else:
                        collect(cell)

        for flowable in story:
            collect(flowable)
        return " ".join(values)

    def test_exact_disclaimer_and_heading_are_in_every_report_variant(self):
        self.assertEqual(REPORT_FORMATS, {"clinician", "patient", "hospital", "ops"})
        for report_format in sorted(REPORT_FORMATS):
            with self.subTest(report_format=report_format):
                text = self._story_text(report_format)
                self.assertIn("Important information", text)
                self.assertIn(ROUTINE_EYE_EXAMINATION_DISCLAIMER, text)
                self.assertIn(self.clinic.name, text)

    def test_every_variant_builds_a_pdf_with_optional_fields_absent(self):
        for report_format in sorted(REPORT_FORMATS):
            with self.subTest(report_format=report_format):
                pdf = ReportPDFRenderer(
                    self.report, report_format=report_format
                ).build()
                self.assertTrue(pdf.startswith(b"%PDF"))

    def test_long_clinical_content_and_disclaimer_paginate_without_error(self):
        self.report.final_clinical_summary = "Detailed clinical interpretation. " * 220
        self.report.recommendation = "Continue coordinated clinical review. " * 180
        self.report.notes = "Additional clinical note. " * 180
        self.report.ops_review_note = "Internal review note. " * 180
        self.report.save(update_fields=[
            "final_clinical_summary", "recommendation", "notes",
            "ops_review_note", "updated_at",
        ])
        for report_format in sorted(REPORT_FORMATS):
            with self.subTest(report_format=report_format):
                pdf = ReportPDFRenderer(
                    self.report, report_format=report_format
                ).build()
                self.assertTrue(pdf.startswith(b"%PDF"))
                self.assertGreater(len(pdf), 1000)
