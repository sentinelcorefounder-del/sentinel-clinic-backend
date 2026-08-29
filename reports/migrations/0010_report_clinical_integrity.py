import hashlib
import json

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


SNAPSHOT_FIELDS = (
    "review_date", "left_unaided_va", "left_corrected_va", "left_dr_grade",
    "left_maculopathy_grade", "right_unaided_va", "right_corrected_va",
    "right_dr_grade", "right_maculopathy_grade", "ungradable",
    "urgency_outcome", "recommendation", "next_followup_interval",
    "recall_months", "recall_due_date", "recall_status", "recall_note",
    "generated_clinical_summary", "final_clinical_summary",
    "clinical_summary_overridden", "report_layout", "selected_fundus_upload_ids",
    "selected_ocular_investigation_ids", "attachment_captions", "notes",
)


def _json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def create_legacy_baselines(apps, schema_editor):
    Report = apps.get_model("reports", "StructuredReport")
    Version = apps.get_model("reports", "StructuredReportVersion")
    duplicate = (
        Report.objects.values("encounter_id")
        .annotate(total=models.Count("id"))
        .filter(total__gt=1)
        .exists()
    )
    if duplicate:
        raise RuntimeError(
            "Duplicate encounter/report relationships require clinical governance resolution."
        )
    for report in Report.objects.order_by("pk").iterator(chunk_size=500):
        snapshot = {field: _json_value(getattr(report, field)) for field in SNAPSHOT_FIELDS}
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        version = Version.objects.create(
            report_id=report.pk,
            version_number=1,
            snapshot_schema_version=1,
            clinical_snapshot=snapshot,
            checksum_sha256=hashlib.sha256(encoded).hexdigest(),
            editor_id=None,
            responsibility_snapshot={
                "historical_author": "unknown",
                "baseline_source": "system_migration",
            },
            purpose="legacy_baseline",
            legacy_pdf_unbound=report.report_status in {"issued", "clinic_issued"},
        )
        Report.objects.filter(pk=report.pk).update(lock_version=1)
        if report.report_status == "submitted_to_ops":
            Report.objects.filter(pk=report.pk).update(submitted_version_id=version.pk)
        if report.report_status in {"issued", "clinic_issued"}:
            Report.objects.filter(pk=report.pk).update(issued_version_id=version.pk)


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("organizations", "0008_alter_organization_organization_type"),
        ("reports", "0009_report_attachment_selection"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="structuredreport", name="lock_version",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.CreateModel(
            name="StructuredReportVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version_number", models.PositiveIntegerField()),
                ("snapshot_schema_version", models.PositiveSmallIntegerField(default=1)),
                ("clinical_snapshot", models.JSONField(default=dict)),
                ("checksum_sha256", models.CharField(max_length=64)),
                ("responsibility_snapshot", models.JSONField(blank=True, default=dict)),
                ("purpose", models.CharField(choices=[("legacy_baseline", "Legacy baseline"), ("initial", "Initial clinical version"), ("clinical_edit", "Clinical edit"), ("returned_correction", "Returned correction")], max_length=30)),
                ("correction_note", models.TextField(blank=True, default="")),
                ("pdf_object_key", models.CharField(blank=True, default="", max_length=500)),
                ("pdf_checksum_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("pdf_size", models.PositiveBigIntegerField(blank=True, null=True)),
                ("pdf_generated_at", models.DateTimeField(blank=True, null=True)),
                ("legacy_pdf_unbound", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("editor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="retinal_report_versions", to=settings.AUTH_USER_MODEL)),
                ("report", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="versions", to="reports.structuredreport")),
                ("source_version", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="derived_versions", to="reports.structuredreportversion")),
            ],
            options={"ordering": ["version_number"]},
        ),
        migrations.AddConstraint(
            model_name="structuredreportversion",
            constraint=models.UniqueConstraint(fields=("report", "version_number"), name="report_unique_clinical_version"),
        ),
        migrations.AddField(
            model_name="structuredreport", name="submitted_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="submitted_reports", to="reports.structuredreportversion"),
        ),
        migrations.AddField(
            model_name="structuredreport", name="issued_version",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="issued_reports", to="reports.structuredreportversion"),
        ),
        migrations.CreateModel(
            name="ReportClinicalResponsibility",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("accepted_at", models.DateTimeField()),
                ("authority_used", models.CharField(max_length=40)),
                ("clinician_name", models.CharField(max_length=255)),
                ("professional_role", models.CharField(max_length=120)),
                ("registration_number", models.CharField(max_length=120)),
                ("takeover_reason", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("accepted_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="retinal_responsibility_acceptances", to=settings.AUTH_USER_MODEL)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="retinal_report_responsibilities", to="organizations.organizationbranch")),
                ("clinic", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="retinal_report_responsibilities", to="organizations.organization")),
                ("current_clinician", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="responsible_retinal_reports", to=settings.AUTH_USER_MODEL)),
                ("original_clinician", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="originally_responsible_retinal_reports", to=settings.AUTH_USER_MODEL)),
                ("previous_clinician", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="transferred_retinal_reports", to=settings.AUTH_USER_MODEL)),
                ("report", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="clinical_responsibility", to="reports.structuredreport")),
            ],
        ),
        migrations.AddField(model_name="reportstatusevent", name="authority_used", field=models.CharField(blank=True, default="", max_length=80)),
        migrations.AlterField(
            model_name="reportstatusevent", name="event_type",
            field=models.CharField(choices=[("created", "Created"), ("responsibility_accepted", "Clinical responsibility accepted"), ("responsibility_taken_over", "Clinical responsibility taken over"), ("submitted_to_ops", "Submitted to Ops"), ("returned_to_clinic", "Returned to Clinic"), ("resubmitted", "Resubmitted"), ("rejected", "Rejected"), ("issued", "Issued"), ("clinic_signed", "Clinic Signed"), ("clinic_issued", "Clinic Issued"), ("queued_for_distribution", "Queued for Distribution"), ("released_to_hospital", "Released to Hospital"), ("hospital_viewed", "Hospital Viewed"), ("hospital_downloaded", "Hospital Downloaded")], max_length=40),
        ),
        migrations.AddField(model_name="reportstatusevent", name="correction_note", field=models.TextField(blank=True, default="")),
        migrations.AddField(model_name="reportstatusevent", name="idempotency_key", field=models.CharField(blank=True, default="", max_length=120)),
        migrations.AddField(model_name="reportstatusevent", name="source_version", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="source_status_events", to="reports.structuredreportversion")),
        migrations.AddField(model_name="reportstatusevent", name="target_version", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="target_status_events", to="reports.structuredreportversion")),
        migrations.AddConstraint(
            model_name="reportstatusevent",
            constraint=models.UniqueConstraint(condition=~models.Q(idempotency_key=""), fields=("report", "idempotency_key"), name="report_unique_status_event_idempotency"),
        ),
        migrations.RunPython(
            create_legacy_baselines,
            migrations.RunPython.noop,
            atomic=True,
        ),
    ]
