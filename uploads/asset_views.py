from django.http import FileResponse
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from reports.permissions import has_internal_ops_authority
from reports.release_control import is_report_released_to_hospital
from uploads.clinical_assets import open_image_upload
from uploads.models import ImageUpload


CLINICAL_ASSET_ROLES = {"optometrist", "ophthalmologist", "reviewer"}
HOSPITAL_ASSET_ROLES = {"hospital_admin"}


class ImageUploadContentView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        upload = get_object_or_404(
            ImageUpload.objects.select_related(
                "asset_organization", "asset_branch",
                "encounter__hospital_referral__report",
            ), pk=pk
        )
        if upload.storage_kind != "private_clinical":
            raise ValidationError("This image does not use protected clinical storage.")
        roles = set(request.user.groups.values_list("name", flat=True))
        linked = getattr(request.user, "organization_link", None)
        referral = getattr(upload.encounter, "hospital_referral", None)
        report = getattr(referral, "report", None) if referral else None
        internal_access = has_internal_ops_authority(request.user)
        explicit_branch_access = request.user.branch_access.filter(
            branch=upload.asset_branch,
        ).exists() or request.user.branch_access.filter(
            branch__organization=upload.asset_organization,
            has_all_branch_access=True,
        ).exists()
        clinic_access = bool(
            not request.user.is_superuser
            and roles & CLINICAL_ASSET_ROLES
            and linked
            and linked.organization_id == upload.asset_organization_id
            and explicit_branch_access
        )
        hospital_access = bool(
            not request.user.is_superuser
            and roles & HOSPITAL_ASSET_ROLES
            and linked
            and referral
            and referral.source_hospital_id == linked.organization_id
            and is_report_released_to_hospital(report, referral)
        )
        if not (internal_access or clinic_access or hospital_access):
            raise PermissionDenied("You do not have access to this image.")
        content_type = "image/jpeg" if upload.source_format == "JPEG" else "image/png"
        response = FileResponse(open_image_upload(upload), content_type=content_type)
        response["Cache-Control"] = "private, no-store, max-age=0"
        response["Pragma"] = "no-cache"
        response["X-Content-Type-Options"] = "nosniff"
        response["Content-Disposition"] = "inline"
        return response
