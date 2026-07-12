from django.urls import path
from .views import (
    HospitalDashboardView,
    HospitalPayoutListView,
    HospitalReferralDetailView,
    HospitalReferralListView,
    HospitalReferralStatusSyncView,
    HospitalReferralSubmitView,
    MatchClinicView,
    HospitalIssuedReportListView,
    HospitalIssuedReportDetailView,
    HospitalPatientListView,
    HospitalPatientDetailView,
)

urlpatterns = [
    path("hospital/dashboard/", HospitalDashboardView.as_view(), name="hospital-dashboard"),
    path("hospital/patients/", HospitalPatientListView.as_view(), name="hospital-patients"),
    path("hospital/patients/<int:pk>/", HospitalPatientDetailView.as_view(), name="hospital-patient-detail"),
    path("hospital/referrals/", HospitalReferralListView.as_view(), name="hospital-referrals"),
    path("hospital/referrals/<int:pk>/", HospitalReferralDetailView.as_view(), name="hospital-referral-detail"),
    path("hospital/payouts/", HospitalPayoutListView.as_view(), name="hospital-payouts"),
    path("hospital/reports/", HospitalIssuedReportListView.as_view(), name="hospital-reports"),
    path("hospital/reports/<int:pk>/", HospitalIssuedReportDetailView.as_view(), name="hospital-report-detail"),
    path("hospital/submit/", HospitalReferralSubmitView.as_view(), name="hospital-submit-referral"),

    # 🔥 NEW OPS ENDPOINT
    path("ops/match-clinic/", MatchClinicView.as_view(), name="match-clinic"),

    # ⚠️ TEMP (keep for now, will remove later)
    path("hospital/sync-status/", HospitalReferralStatusSyncView.as_view(), name="hospital-sync-status"),
]