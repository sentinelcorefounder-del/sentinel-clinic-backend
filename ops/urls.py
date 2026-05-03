from django.urls import path

from .views import (
    OpsDashboardView,
    OpsReferralListView,
    OpsReferralDetailView,
    OpsAssignClinicView,
    OpsPaymentListView,
    CreatePaymentForReferralView,
    InitializeOpsPaymentView,
    VerifyOpsPaymentView,
    OpsReportApprovalQueueView,
    OpsReportApproveView,
    OpsReportRejectView,
    OpsCreateOrganizationView,
    OpsCreateUserView,
    PaystackOpsWebhookView,
    OpsPatientListView,
    OpsPatientDetailView,
    OpsHospitalListView,
    OpsHospitalDetailView,
    OpsClinicListView,
    OpsClinicDetailView,
    OpsAuditLogListView,
    OpsNotificationListView,
    OpsNotificationMarkReadView,
    OpsNotificationMarkAllReadView,
    OpsNotificationDeleteView,
    PublicSelfReferralView,
)

urlpatterns = [
    path("dashboard/", OpsDashboardView.as_view(), name="ops-dashboard"),

    path("referrals/", OpsReferralListView.as_view(), name="ops-referral-list"),
    path("referrals/<int:pk>/", OpsReferralDetailView.as_view(), name="ops-referral-detail"),
    path("referrals/<int:pk>/assign-clinic/", OpsAssignClinicView.as_view(), name="ops-assign-clinic"),

    path("payments/", OpsPaymentListView.as_view(), name="ops-payment-list"),
    path("referrals/<int:referral_pk>/create-payment/", CreatePaymentForReferralView.as_view()),
    path("payments/<int:pk>/initialize/", InitializeOpsPaymentView.as_view()),
    path("payments/<int:pk>/verify/", VerifyOpsPaymentView.as_view()),
    path("payments/webhook/", PaystackOpsWebhookView.as_view()),

    path("reports/approval-queue/", OpsReportApprovalQueueView.as_view()),
    path("reports/<int:pk>/approve/", OpsReportApproveView.as_view()),
    path("reports/<int:pk>/reject/", OpsReportRejectView.as_view()),

    path("organizations/create/", OpsCreateOrganizationView.as_view()),
    path("users/create/", OpsCreateUserView.as_view()),

    path("patients/", OpsPatientListView.as_view()),
    path("patients/<int:pk>/", OpsPatientDetailView.as_view()),

    path("hospitals/", OpsHospitalListView.as_view()),
    path("hospitals/<int:pk>/", OpsHospitalDetailView.as_view()),

    path("clinics/", OpsClinicListView.as_view()),
    path("clinics/<int:pk>/", OpsClinicDetailView.as_view()),

    path("audit-logs/", OpsAuditLogListView.as_view()),

    path("notifications/", OpsNotificationListView.as_view()),
    path("notifications/<int:pk>/read/", OpsNotificationMarkReadView.as_view()),
    path("notifications/mark-all-read/", OpsNotificationMarkAllReadView.as_view()),

    path("self-referrals/", PublicSelfReferralView.as_view()),
    path("notifications/<int:pk>/delete/", OpsNotificationDeleteView.as_view()),
]