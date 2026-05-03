
from referrals.models import HospitalReferral


def sync_report_to_referral(report):
    referral = HospitalReferral.objects.filter(patient=report.patient).first()

    if referral:
        referral.report = report
        referral.report_ready = True
        referral.referral_status = "completed"
        referral.save()
