import os

from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.tenant import get_user_organization
from .models import Organization
from .provision_serializers import ClinicProvisionSerializer, HospitalProvisionSerializer
from .serializers import OrganizationSerializer
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


class OrganizationDetailView(generics.RetrieveAPIView):
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