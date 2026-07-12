from django.core.management.base import BaseCommand

from organizations.models import Organization, OrganizationProfile


class Command(BaseCommand):
    help = "Create a default capability profile for every organization."

    def handle(self, *args, **options):
        created_count = 0

        for organization in Organization.objects.all():
            profile, created = OrganizationProfile.objects.get_or_create(
                organization=organization
            )

            if created:
                created_count += 1

                if organization.organization_type == "clinic":
                    profile.workflow_mode = "sentinel_managed"
                    profile.referral_requirement = "required"
                    profile.patient_ownership = "shared"
                    profile.can_create_direct_patients = False
                    profile.can_issue_reports_directly = False
                    profile.sentinel_review_policy = "mandatory"
                    profile.default_payment_responsibility = "hospital"
                    profile.branding_policy = "organization_and_sentinel"
                    profile.subscription_tier = "pilot"
                    profile.ai_enabled = True
                    profile.clinic_direct_screening_enabled = False

                elif organization.organization_type == "hospital":
                    profile.workflow_mode = "sentinel_managed"
                    profile.referral_requirement = "required"
                    profile.patient_ownership = "hospital"
                    profile.can_create_direct_patients = False
                    profile.can_issue_reports_directly = False
                    profile.sentinel_review_policy = "mandatory"
                    profile.default_payment_responsibility = "hospital"
                    profile.branding_policy = "hospital_and_sentinel"
                    profile.subscription_tier = "pilot"
                    profile.ai_enabled = False

                else:
                    profile.workflow_mode = "sentinel_managed"
                    profile.referral_requirement = "optional"
                    profile.patient_ownership = "shared"
                    profile.sentinel_review_policy = "mandatory"
                    profile.branding_policy = "sentinel_only"
                    profile.subscription_tier = "enterprise"

                profile.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Capability profile backfill complete. "
                f"Created {created_count} profile(s)."
            )
        )
