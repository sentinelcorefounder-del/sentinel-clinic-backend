from django.urls import path

from .views import (
    ClinicProvisionView,
    HospitalProvisionView,
    OrganizationDetailView,
    OrganizationListView,
)

urlpatterns = [
    path("", OrganizationListView.as_view(), name="organization-list"),
    path("provision/", ClinicProvisionView.as_view(), name="clinic-provision"),
    path("hospital/provision/", HospitalProvisionView.as_view(), name="hospital-provision"),
    path("<int:pk>/", OrganizationDetailView.as_view(), name="organization-detail"),
]