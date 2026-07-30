from django.urls import path

from .views import (
    ClinicProvisionView,
    HospitalProvisionView,
    OrganizationDetailView,
    OrganizationListView,
    MyOrganizationCapabilityProfileView,
    OrganizationBranchListCreateView,
    OrganizationBranchDetailView,
)

urlpatterns = [
    path("", OrganizationListView.as_view(), name="organization-list"),
    path("me/capabilities/", MyOrganizationCapabilityProfileView.as_view(), name="my-organization-capabilities"),
    path("provision/", ClinicProvisionView.as_view(), name="clinic-provision"),
    path("hospital/provision/", HospitalProvisionView.as_view(), name="hospital-provision"),
    path("<int:pk>/", OrganizationDetailView.as_view(), name="organization-detail"),
    path("<int:organization_id>/branches/", OrganizationBranchListCreateView.as_view(), name="organization-branches"),
    path("<int:organization_id>/branches/<int:pk>/", OrganizationBranchDetailView.as_view(), name="organization-branch-detail"),
]
