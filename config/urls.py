from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.http import JsonResponse
from django.views.static import serve
from django.http import Http404


def deny_public_finance_media(request, path):
    raise Http404("Not found.")


def home(request):
    return JsonResponse({"status": "ok", "message": "Sentinel Clinic backend is running"})


urlpatterns = [
    path("", home),
    path("admin/", admin.site.urls),
    path("api/auth/", include("authn.urls")),
    path("api/patients/", include("patients.urls")),
    path("api/referrals/", include("referrals.urls")),
    path("api/encounters/", include("encounters.urls")),
    path("api/uploads/", include("uploads.urls")),
    path("api/reports/", include("reports.urls")),
    path("api/consents/", include("consents.urls")),
    path("api/dashboard/", include("dashboard.urls")),
    path("api/organizations/", include("organizations.urls")),
    path("api/payments/", include("payments.urls")),
    path("api/ops/", include("ops.urls")),
    path("api/audit/", include("audit.urls")),
    path("api/finance/", include("finance.urls")),
    path("api/onward-referrals/", include("onward_referrals.urls")),
    re_path(r"^media/finance/(?P<path>.*)$", deny_public_finance_media),
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]
