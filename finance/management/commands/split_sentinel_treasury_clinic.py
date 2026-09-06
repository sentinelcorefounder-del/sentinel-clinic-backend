from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from encounters.models import AssessmentServiceSession, ScreeningEncounter
from finance.models import OrganizationWallet, PartnerContract
from finance.services import post_opening_balance
from organizations.models import Organization, OrganizationBranch
from patients.models import Patient
from users.models import UserBranchAccess, UserOrganization


class Command(BaseCommand):
    help = (
        "Controlled, idempotent separation of the legacy Project Sentinel clinic/treasury identity. "
        "Dry-run by default; pass --apply after reviewing the output."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--opening-balance", default="180140.00")
        parser.add_argument("--treasury-code", default="SNT-TREASURY")
        parser.add_argument("--clinic-code", default="SNT-CLINIC")
        parser.add_argument("--clinic-name", default="Sentinel Clinic")

    def handle(self, *args, **options):
        apply = options["apply"]
        amount = Decimal(str(options["opening_balance"])).quantize(Decimal("0.01"))
        if amount <= 0:
            raise CommandError("Opening balance must be greater than zero.")

        legacy = Organization.objects.filter(is_sentinel_treasury=True).first()
        if legacy is None:
            legacy = Organization.objects.filter(clinic_id="SNT-CLINIC").first()
        if legacy is None:
            raise CommandError("Could not identify the legacy Project Sentinel organisation.")

        clinic = (
            Organization.objects.filter(clinic_id=options["clinic_code"])
            .exclude(pk=legacy.pk)
            .first()
        )

        clinical_users = UserOrganization.objects.filter(
            organization=legacy
        ).exclude(
            user__security_profile__is_internal_sentinel_staff=True
        )

        branch_qs = OrganizationBranch.objects.filter(organization=legacy)
        branch_ids = list(branch_qs.values_list("id", flat=True))

        encounter_qs = ScreeningEncounter.objects.filter(
            originating_organization=legacy
        )
        patient_qs = Patient.objects.filter(assigned_clinic=legacy)
        contract_qs = PartnerContract.objects.filter(organization=legacy)

        service_session_qs = AssessmentServiceSession.objects.filter(
            participating_organization=legacy,
            service_branch_id__in=branch_ids,
        )

        internal_branch_access_qs = UserBranchAccess.objects.filter(
            branch_id__in=branch_ids,
            user__organization_link__organization=legacy,
            user__security_profile__is_internal_sentinel_staff=True,
        )

        self.stdout.write(f"MODE={'APPLY' if apply else 'DRY-RUN'}")
        self.stdout.write(
            f"legacy_org={legacy.pk}:{legacy.name}:{legacy.clinic_id}:{legacy.organization_type}"
        )
        self.stdout.write(f"clinic_exists={bool(clinic)}")
        self.stdout.write(f"clinical_user_links={clinical_users.count()}")
        self.stdout.write(f"branches={branch_qs.count()}")
        self.stdout.write(f"encounters={encounter_qs.count()}")
        self.stdout.write(f"patients={patient_qs.count()}")
        self.stdout.write(f"contracts={contract_qs.count()}")
        self.stdout.write(f"service_sessions={service_session_qs.count()}")
        self.stdout.write(
            f"internal_branch_access_to_remove={internal_branch_access_qs.count()}"
        )
        self.stdout.write(f"opening_balance={amount}")

        if not apply:
            self.stdout.write(
                self.style.WARNING(
                    "No data changed. Re-run with --apply after review."
                )
            )
            return

        with transaction.atomic():
            legacy = Organization.objects.select_for_update().get(pk=legacy.pk)

            # Capture the exact rows that belong to the legacy clinical identity
            # before moving the branch itself.
            branch_ids = list(
                OrganizationBranch.objects.filter(
                    organization=legacy
                ).values_list("id", flat=True)
            )

            service_session_ids = list(
                AssessmentServiceSession.objects.filter(
                    participating_organization=legacy,
                    service_branch_id__in=branch_ids,
                ).values_list("id", flat=True)
            )

            internal_branch_access_ids = list(
                UserBranchAccess.objects.filter(
                    branch_id__in=branch_ids,
                    user__organization_link__organization=legacy,
                    user__security_profile__is_internal_sentinel_staff=True,
                ).values_list("id", flat=True)
            )

            legacy.name = "Project Sentinel Treasury"
            legacy.clinic_id = options["treasury_code"]
            legacy.organization_type = "sentinel"
            legacy.is_sentinel_treasury = True
            legacy.save(
                update_fields=[
                    "name",
                    "clinic_id",
                    "organization_type",
                    "is_sentinel_treasury",
                ]
            )

            clinic, created = Organization.objects.get_or_create(
                clinic_id=options["clinic_code"],
                defaults={
                    "name": options["clinic_name"],
                    "organization_type": "clinic",
                    "is_active": True,
                    "currency": legacy.currency,
                    "contact_email": legacy.contact_email,
                    "address": legacy.address,
                    "phone": legacy.phone,
                },
            )

            if not created:
                if clinic.is_sentinel_treasury:
                    raise CommandError(
                        "Target clinic is marked as treasury; refusing split."
                    )

                clinic.name = options["clinic_name"]
                clinic.organization_type = "clinic"
                clinic.is_active = True
                clinic.save(
                    update_fields=[
                        "name",
                        "organization_type",
                        "is_active",
                    ]
                )

            # Move the clinical branch to Sentinel Clinic.
            OrganizationBranch.objects.filter(
                id__in=branch_ids
            ).update(
                organization=clinic
            )

            # Move non-internal clinical users to Sentinel Clinic.
            UserOrganization.objects.filter(
                pk__in=clinical_users.values_list("pk", flat=True)
            ).update(
                organization=clinic
            )

            # Internal Sentinel staff remain attached to Project Sentinel Treasury.
            # Their old clinic branch-access rows would become cross-organisation
            # references after the branch moves, so remove only those invalid rows.
            UserBranchAccess.objects.filter(
                id__in=internal_branch_access_ids
            ).delete()

            # Remaining clinic users continue to have valid access to the moved branch.
            UserBranchAccess.objects.filter(
                branch_id__in=branch_ids
            ).update(
                has_all_branch_access=True
            )

            # Completed/historical service sessions follow the operational clinic
            # when their service branch is one of the moved clinical branches.
            AssessmentServiceSession.objects.filter(
                id__in=service_session_ids
            ).update(
                participating_organization=clinic
            )

            ScreeningEncounter.objects.filter(
                originating_organization=legacy
            ).update(
                originating_organization=clinic
            )

            Patient.objects.filter(
                assigned_clinic=legacy
            ).update(
                assigned_clinic=clinic
            )

            PartnerContract.objects.filter(
                organization=legacy
            ).update(
                organization=clinic
            )

            wallet, _ = OrganizationWallet.objects.get_or_create(
                organization=clinic,
                currency="NGN",
                defaults={
                    "is_active": True,
                    "credit_limit": Decimal("0.00"),
                },
            )

            post_opening_balance(
                wallet=wallet,
                amount=amount,
                idempotency_key="sentinel-clinic-opening-balance-v1",
                reference="PRELIVE-SENTINEL-CLINIC-OPENING",
                description=(
                    "Pre-live Sentinel Clinic operational opening balance reconstruction"
                ),
                metadata={
                    "migration": "split_sentinel_treasury_clinic",
                    "legacy_organization_id": legacy.pk,
                    "approved_opening_balance": str(amount),
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Split complete: treasury={legacy.pk}:{legacy.clinic_id}; "
                f"clinic={clinic.pk}:{clinic.clinic_id}; "
                f"clinic_wallet_balance={wallet.available_balance}"
            )
        )