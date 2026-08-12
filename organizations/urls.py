from django.urls import path

from .views import (
    ClinicProvisionView,
    HospitalProvisionView,
    OrganizationDetailView,
    OrganizationListView,
    MyOrganizationCapabilityProfileView,
    OrganizationBranchListCreateView,
    OrganizationBranchDetailView,
    MyPartnerNotificationListView,
    MyPartnerNotificationMarkReadView,
    MyPartnerNotificationMarkAllReadView,
)

urlpatterns = [
    path("", OrganizationListView.as_view(), name="organization-list"),
    path("me/capabilities/", MyOrganizationCapabilityProfileView.as_view(), name="my-organization-capabilities"),
    path("me/notifications/", MyPartnerNotificationListView.as_view(), name="my-partner-notifications"),
    path("me/notifications/<int:pk>/read/", MyPartnerNotificationMarkReadView.as_view(), name="my-partner-notification-read"),
    path("me/notifications/mark-all-read/", MyPartnerNotificationMarkAllReadView.as_view(), name="my-partner-notifications-read-all"),
    path("provision/", ClinicProvisionView.as_view(), name="clinic-provision"),
    path("hospital/provision/", HospitalProvisionView.as_view(), name="hospital-provision"),
    path("<int:pk>/", OrganizationDetailView.as_view(), name="organization-detail"),
    path("<int:organization_id>/branches/", OrganizationBranchListCreateView.as_view(), name="organization-branches"),
    path("<int:organization_id>/branches/<int:pk>/", OrganizationBranchDetailView.as_view(), name="organization-branch-detail"),
]
