from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from uploads.access import can_access_clinical_asset
from uploads.clinical_assets import open_image_upload
from uploads.models import ImageUpload


class ImageUploadContentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        upload = get_object_or_404(
            ImageUpload.objects.select_related(
                "asset_organization", "asset_branch",
                "encounter__patient__assigned_clinic",
                "encounter__patient__assigned_branch",
                "encounter__hospital_referral__report",
            ), pk=pk
        )
        organization = (
            upload.asset_organization
            or upload.encounter.originating_organization
            or upload.encounter.patient.assigned_clinic
        )
        branch = (
            upload.asset_branch
            or upload.encounter.service_branch
            or upload.encounter.patient.assigned_branch
        )
        if not can_access_clinical_asset(
            request.user, encounter=upload.encounter,
            organization=organization, branch=branch,
        ):
            raise PermissionDenied("You do not have access to this image.")
        name = upload.private_object_key or upload.image_file.name
        content_type = (
            "image/png" if (upload.source_format == "PNG" or name.lower().endswith(".png"))
            else "image/jpeg"
        )
        response = FileResponse(open_image_upload(upload), content_type=content_type)
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = "inline"
        return response
