from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse


def home(request):
    return JsonResponse({"status": "ok", "message": "Sentinel Clinic backend is running"})


urlpatterns = [
    path("", home),
    path("admin/", admin.site.urls),
    path("api/auth/", include("authn.urls")),
    path("api/patients/", include("patients.urls")),
    path("api/encounters/", include("encounters.urls")),
    path("api/uploads/", include("uploads.urls")),
    path("api/reports/", include("reports.urls")),
    path("api/consents/", include("consents.urls")),
    path("api/dashboard/", include("dashboard.urls")),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)