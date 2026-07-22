from django.urls import reverse


def build_report_pdf_url(request, report):
    """Build an internal report PDF URL for authorised clinic/Ops responses."""
    path = reverse("report-pdf", kwargs={"pk": report.pk})
    if request is not None:
        return request.build_absolute_uri(path)
    return path


def sync_report_to_local_hospital_referral(report):
    """Link a submitted report to its Sentinel referral without releasing it."""
    from referrals.models import HospitalReferral

    patient = report.patient
    referral_id = (getattr(patient, "referral_id", "") or "").strip()

    referral = None
    if referral_id:
        referral = HospitalReferral.objects.filter(referral_id=referral_id).first()

    if not referral:
        referral = (
            HospitalReferral.objects.filter(patient=patient)
            .order_by("-created_at")
            .first()
        )

    if not referral:
        return None

    referral.report = report
    referral.report_ready = False
    referral.referral_status = "submitted_to_ops"
    referral.save(
        update_fields=[
            "report",
            "report_ready",
            "referral_status",
            "updated_at",
        ]
    )
    return referral
