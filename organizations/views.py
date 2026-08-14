import os

from rest_framework.permissions import IsAuthenticated
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from common.tenant import get_user_organization
from .models import Organization, OrganizationBranch, OrganizationProfile, PartnerNotification
from .provision_serializers import ClinicProvisionSerializer, HospitalProvisionSerializer
from .serializers import (
    OrganizationSerializer,
    OrganizationProfileSerializer,
    OrganizationWithProfileSerializer,
    OrganizationBranchSerializer,
    PartnerNotificationSerializer,
)


class MyPartnerNotificationListView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PartnerNotificationSerializer

    def get_queryset(self):
        queryset = PartnerNotification.objects.filter(recipient=self.request.user)
        if self.request.query_params.get("unread", "").lower() == "true":
            queryset = queryset.filter(is_read=False)
        return queryset


class MyPartnerNotificationMarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        notification = PartnerNotification.objects.filter(
            pk=pk, recipient=request.user
        ).first()
        if notification is None:
            return Response({"detail": "Notification not found."}, status=status.HTTP_404_NOT_FOUND)
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=["is_read", "read_at"])
        return Response(PartnerNotificationSerializer(notification).data)


class MyPartnerNotificationMarkAllReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        PartnerNotification.objects.filter(recipient=request.user, is_read=False).update(
            is_read=True, read_at=timezone.now()
        )
        return Response({"message": "All notifications marked as read."})
from .services.provisioning import (
    provision_clinic_with_admin,
    provision_hospital_with_admin,
)


class OrganizationListView(generics.ListAPIView):
    serializer_class = OrganizationSerializer

    def get_queryset(self):
        queryset = Organization.objects.exclude(organization_type="service_partner")

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
        queryset = Organization.objects.exclude(organization_type="service_partner")

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


class OrganizationBranchListCreateView(generics.ListCreateAPIView):
    serializer_class = OrganizationBranchSerializer
    permission_classes = [IsAuthenticated]

    def _organization(self):
        organization_id = self.kwargs["organization_id"]
        user = self.request.user
        queryset = Organization.objects.filter(
            pk=organization_id, organization_type__in={"clinic", "hospital"}
        )
        if user.is_superuser or user.groups.filter(name="ops_admin").exists():
            return queryset.first()
        organization = get_user_organization(user)
        return organization if organization and organization.id == organization_id else None

    def get_queryset(self):
        organization = self._organization()
        if not organization:
            return OrganizationBranch.objects.none()
        return organization.branches.all()

    def perform_create(self, serializer):
        organization = self._organization()
        if not organization:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You cannot manage branches for this organization.")
        allowed = self.request.user.is_superuser or self.request.user.groups.filter(
            name__in=["ops_admin", "clinic_admin", "hospital_admin"]
        ).exists()
        if not allowed:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only organisation administrators can create branches.")
        serializer.save(organization=organization)


class OrganizationBranchDetailView(generics.RetrieveUpdateAPIView):
    serializer_class = OrganizationBranchSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = OrganizationBranch.objects.filter(
            organization_id=self.kwargs["organization_id"],
            organization__organization_type__in={"clinic", "hospital"},
        )
        if user.is_superuser or user.groups.filter(name="ops_admin").exists():
            return queryset
        organization = get_user_organization(user)
        return queryset.filter(organization=organization) if organization else queryset.none()

    def perform_update(self, serializer):
        allowed = self.request.user.is_superuser or self.request.user.groups.filter(
            name__in=["ops_admin", "clinic_admin", "hospital_admin"]
        ).exists()
        if not allowed:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only organisation administrators can update branches.")
        serializer.save()
