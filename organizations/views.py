import os
import secrets

from django.contrib.auth.models import Group, User
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Organization
from .serializers import OrganizationSyncSerializer


class OrganizationSyncView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        token = request.headers.get("X-SENTINEL-SYNC-TOKEN")
        expected = os.environ.get("SENTINEL_SYNC_TOKEN", "")

        if not expected or token != expected:
            return Response(
                {"detail": "Unauthorized"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = OrganizationSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        org, clinic_created = Organization.objects.update_or_create(
            clinic_id=data["clinic_id"],
            defaults={
                "name": data["name"],
                "contact_email": data.get("contact_email", ""),
                "is_active": data.get("is_active", True),
            },
        )

        group, _ = Group.objects.get_or_create(name="clinic_admin")

        username = f"{org.clinic_id.lower().replace('-', '_')}_admin"
        email = org.contact_email or f"{username}@sentinel.local"

        user, admin_created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_active": True,
                "is_staff": True,
            },
        )

        temporary_password = None

        if admin_created:
            temporary_password = secrets.token_urlsafe(10)
            user.set_password(temporary_password)
            user.save()
            user.groups.add(group)

        return Response(
            {
                "detail": "Clinic synced successfully",
                "clinic_id": org.clinic_id,
                "clinic_name": org.name,
                "admin_username": username,
                "temporary_password": temporary_password,
                "admin_created": admin_created,
                "clinic_created": clinic_created,
            },
            status=status.HTTP_200_OK,
        )