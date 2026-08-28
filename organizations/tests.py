from django.contrib.auth import get_user_model
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
import tempfile
from rest_framework.test import APIClient

from users.models import UserOrganization
from .models import Organization, PartnerNotification


class PartnerNotificationApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organization = Organization.objects.create(
            clinic_id="CLINIC-NOTIFY", name="Notify Clinic", organization_type="clinic"
        )
        self.other_organization = Organization.objects.create(
            clinic_id="CLINIC-NOTIFY-OTHER", name="Other Clinic", organization_type="clinic"
        )
        self.user = get_user_model().objects.create_user(username="notify-user")
        self.other_user = get_user_model().objects.create_user(username="notify-other")
        UserOrganization.objects.create(user=self.user, organization=self.organization)
        UserOrganization.objects.create(user=self.other_user, organization=self.other_organization)
        self.notification = PartnerNotification.objects.create(
            recipient=self.user,
            organization=self.organization,
            title="Receipt ready",
            notification_type="wallet_credited",
            action_path="/finance",
            deduplication_key="test:receipt:1",
        )

    def test_user_can_only_list_and_mark_own_notifications(self):
        self.client.force_authenticate(self.other_user)
        self.assertEqual(self.client.get("/api/organizations/me/notifications/").data, [])
        denied = self.client.post(
            f"/api/organizations/me/notifications/{self.notification.pk}/read/", {}, format="json"
        )
        self.assertEqual(denied.status_code, 404)

        self.client.force_authenticate(self.user)
        response = self.client.get("/api/organizations/me/notifications/?unread=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data], [self.notification.pk])
        marked = self.client.post(
            f"/api/organizations/me/notifications/{self.notification.pk}/read/", {}, format="json"
        )
        self.assertEqual(marked.status_code, 200)
        self.assertTrue(marked.data["is_read"])


class PrivateLogoAccessTests(TestCase):
    def setUp(self):
        self.media_temp = tempfile.TemporaryDirectory()
        self.storage_override = override_settings(MEDIA_ROOT=self.media_temp.name)
        self.storage_override.enable()
        self.organization = Organization.objects.create(
            clinic_id="LOGO-CLINIC", name="Logo Clinic", organization_type="clinic",
            logo=SimpleUploadedFile("logo.png", b"synthetic-logo", content_type="image/png"),
        )
        self.other = Organization.objects.create(
            clinic_id="LOGO-OTHER", name="Other", organization_type="clinic"
        )
        self.user = get_user_model().objects.create_user("logo-user")
        self.other_user = get_user_model().objects.create_user("logo-other")
        UserOrganization.objects.create(user=self.user, organization=self.organization)
        UserOrganization.objects.create(user=self.other_user, organization=self.other)
        self.client = APIClient()

    def tearDown(self):
        self.storage_override.disable()
        self.media_temp.cleanup()

    def test_logo_uses_authenticated_application_endpoint(self):
        self.client.force_authenticate(self.user)
        detail = self.client.get(f"/api/organizations/{self.organization.pk}/")
        self.assertIn(
            f"/api/organizations/{self.organization.pk}/logo/",
            detail.data["logo"],
        )
        response = self.client.get(
            f"/api/organizations/{self.organization.pk}/logo/"
        )
        self.assertEqual(response.status_code, 200)
        response.close()
        self.client.force_authenticate(self.other_user)
        self.assertEqual(
            self.client.get(
                f"/api/organizations/{self.organization.pk}/logo/"
            ).status_code,
            403,
        )
