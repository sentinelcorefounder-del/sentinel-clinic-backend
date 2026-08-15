from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from organizations.services.branches import user_can_access_branch
from uploads.clinical_assets import open_image_upload
from uploads.models import ImageUpload


class ImageUploadContentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        upload = get_object_or_404(
            ImageUpload.objects.select_related(
                "asset_organization", "asset_branch", "encounter__hospital_referral"
            ), pk=pk
        )
        if upload.storage_kind != "private_clinical":
            raise ValidationError("This image does not use protected clinical storage.")
        roles = set(request.user.groups.values_list("name", flat=True))
        internal = request.user.is_superuser or bool(roles & {"super_admin", "ops_admin", "sentinel_ops", "ophthalmologist", "optometrist"})
        linked = getattr(request.user, "organization_link", None)
        if not internal:
            referral = getattr(upload.encounter, "hospital_referral", None)
            hospital_access = bool(
                linked and referral and referral.source_hospital_id == linked.organization_id
            )
            clinic_access = bool(linked and linked.organization_id == upload.asset_organization_id)
            if not (clinic_access or hospital_access):
                raise PermissionDenied("You do not have access to this image.")
            if clinic_access and not user_can_access_branch(request.user, upload.asset_branch):
                raise PermissionDenied("You do not have access to this image.")
        content_type = "image/jpeg" if upload.source_format == "JPEG" else "image/png"
        response = FileResponse(open_image_upload(upload), content_type=content_type)
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = "inline"
        return response
