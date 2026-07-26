from django.urls import path
from .views import (
    ScreeningEncounterListCreateView,
    ScreeningEncounterDetailView,
    PatientEncounterListView,
    PatientActiveReferralListView,
    OcularDiagnosticAssessmentDetailView,
    OcularInvestigationListCreateView,
    OcularInvestigationDetailView,
    OcularAIReviewListCreateView,
    OcularAIReviewDecisionView,
)

urlpatterns = [
    path("", ScreeningEncounterListCreateView.as_view(), name="encounter-list-create"),
    path("<int:pk>/", ScreeningEncounterDetailView.as_view(), name="encounter-detail"),
    path(
        "<int:encounter_id>/ocular-assessment/",
        OcularDiagnosticAssessmentDetailView.as_view(),
        name="ocular-assessment-detail",
    ),
    path(
        "<int:encounter_id>/ocular-investigations/",
        OcularInvestigationListCreateView.as_view(),
        name="ocular-investigation-list-create",
    ),
    path(
        "ocular-investigations/<int:pk>/",
        OcularInvestigationDetailView.as_view(),
        name="ocular-investigation-detail",
    ),
    path(
        "<int:encounter_id>/ocular-ai-reviews/",
        OcularAIReviewListCreateView.as_view(),
        name="ocular-ai-review-list-create",
    ),
    path(
        "ocular-ai-reviews/<int:pk>/decision/",
        OcularAIReviewDecisionView.as_view(),
        name="ocular-ai-review-decision",
    ),
    path(
        "patient/<int:patient_id>/active-referrals/",
        PatientActiveReferralListView.as_view(),
        name="patient-active-referrals",
    ),
    path(
        "patient/<int:patient_id>/",
        PatientEncounterListView.as_view(),
        name="patient-encounters",
    ),
]
