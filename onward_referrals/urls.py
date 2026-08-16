from django.urls import path

from .views import (
    AvailabilityView, DocumentView, EligibilityView, FinalizeView,
    OnwardReferralDetailView, OnwardReferralListCreateView, PreviewView,
    RegisteredHospitalListView, ResponsibilityView, SupersedeView, VoidView,
)

urlpatterns = [
    path("", OnwardReferralListCreateView.as_view(), name="onward-referral-list-create"),
    path("registered-hospitals/", RegisteredHospitalListView.as_view(), name="onward-registered-hospitals"),
    path("encounters/<int:encounter_id>/eligibility/", EligibilityView.as_view(), name="onward-eligibility"),
    path("encounters/<int:encounter_id>/responsibility/", ResponsibilityView.as_view(), name="onward-responsibility"),
    path("<uuid:referral_uuid>/", OnwardReferralDetailView.as_view(), name="onward-referral-detail"),
    path("<uuid:referral_uuid>/finalize/", FinalizeView.as_view(), name="onward-finalize"),
    path("<uuid:referral_uuid>/supersede/", SupersedeView.as_view(), name="onward-supersede"),
    path("<uuid:referral_uuid>/void/", VoidView.as_view(), name="onward-void"),
    path("<uuid:referral_uuid>/availability/", AvailabilityView.as_view(), name="onward-availability"),
    path("<uuid:referral_uuid>/preview/", PreviewView.as_view(), name="onward-preview"),
    path("<uuid:referral_uuid>/versions/<int:version_number>/document/", DocumentView.as_view(), name="onward-document"),
]
