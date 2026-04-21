from django.urls import path
from .views import (
    HospitalDashboardView,
    HospitalPayoutListView,
    HospitalReferralDetailView,
    HospitalReferralListView,
    HospitalReferralStatusSyncView,
    HospitalReferralSubmitView,
)

urlpatterns = [
    path("hospital/dashboard/", HospitalDashboardView.as_view(), name="hospital-dashboard"),
    path("hospital/referrals/", HospitalReferralListView.as_view(), name="hospital-referrals"),
    path("hospital/referrals/<int:pk>/", HospitalReferralDetailView.as_view(), name="hospital-referral-detail"),
    path("hospital/payouts/", HospitalPayoutListView.as_view(), name="hospital-payouts"),
    path("hospital/submit/", HospitalReferralSubmitView.as_view(), name="hospital-submit-referral"),
    path("hospital/sync-status/", HospitalReferralStatusSyncView.as_view(), name="hospital-sync-status"),
]