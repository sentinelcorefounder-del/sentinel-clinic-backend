import os

from rest_framework.permissions import IsAuthenticated
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from .models import Organization, OrganizationProfile
from .provision_serializers import ClinicProvisionSerializer, HospitalProvisionSerializer
from .serializers import (
    OrganizationSerializer,
    OrganizationProfileSerializer,
    OrganizationWithProfileSerializer,
)
from .services.provisioning import (
    provision_clinic_with_admin,
    provision_hospital_with_admin,
)


class OrganizationListView(generics.ListAPIView):
    serializer_class = OrganizationSerializer

    def get_queryset(self):
        queryset = Organization.objects.all()

        user = self.request.user
        if user.is_superuser:
            return queryset

        org = get_user_organization(user)
        if not org:
            return Organization.objects.none()

        return queryset.filter(id=org.id)


class OrganizationDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = OrganizationWithProfileSerializer

    def get_queryset(self):
        queryset = Organization.objects.all()

        user = self.request.user
        if user.is_superuser or user.groups.filter(name="ops_admin").exists():
            return queryset

        org = get_user_organization(user)
        if not org:
            return Organization.objects.none()

        return queryset.filter(id=org.id)

    def perform_update(self, serializer):
        organization = serializer.save()
        branding_policy = self.request.data.get("branding_policy")
        if branding_policy:
            profile, _ = OrganizationProfile.objects.get_or_create(
                organization=organization
            )
            allowed = {
                value
                for value, _label in OrganizationProfile.BRANDING_POLICY_CHOICES
            }
            if branding_policy in allowed:
                profile.branding_policy = branding_policy
                profile.save(update_fields=["branding_policy", "updated_at"])


class ClinicProvisionView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        token = request.headers.get("X-SENTINEL-PROVISION-TOKEN")
        expected = os.environ.get("SENTINEL_PROVISION_TOKEN", "")

        if not expected or token != expected:
            return Response(
                {"detail": "Unauthorized"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = ClinicProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = provision_clinic_with_admin(serializer.validated_data)

        return Response(
            {
                "detail": "Clinic provisioned successfully",
                **result,
            },
            status=status.HTTP_200_OK,
        )


class HospitalProvisionView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        token = request.headers.get("X-SENTINEL-PROVISION-TOKEN")
        expected = os.environ.get("SENTINEL_PROVISION_TOKEN", "")

        if not expected or token != expected:
            return Response(
                {"detail": "Unauthorized"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = HospitalProvisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = provision_hospital_with_admin(serializer.validated_data)

        return Response(
            {
                "detail": "Hospital provisioned successfully",
                **result,
            },
            status=status.HTTP_200_OK,
        )


class MyOrganizationCapabilityProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        org = get_user_organization(request.user)

        # Superusers may deliberately return no tenant from the shared
        # tenant helper. Use their explicit UserOrganization link instead.
        if not org:
            try:
                org = request.user.organization_link.organization
            except Exception:
                org = None

        if not org:
            return Response(
                {"detail": "You are not linked to an organization."},
                status=status.HTTP_404_NOT_FOUND,
            )

        profile, _ = OrganizationProfile.objects.get_or_create(
            organization=org
        )

        return Response(
            OrganizationProfileSerializer(profile).data
        )
