from django.urls import path
from .views import OrganizationSyncView

urlpatterns = [
    path("sync/", OrganizationSyncView.as_view(), name="organization-sync"),
]