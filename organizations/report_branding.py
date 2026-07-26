from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist


@dataclass(frozen=True)
class ReportBrand:
    name: str
    organization: object | None = None
    logo_path: str | None = None


@dataclass(frozen=True)
class ReportBranding:
    policy: str
    owner: object | None
    clinic: object | None
    hospital: object | None
    brands: tuple[ReportBrand, ...]
    powered_by_sentinel: bool

    @property
    def primary_organization(self):
        for brand in self.brands:
            if brand.organization is not None:
                return brand.organization
        return self.owner or self.clinic or self.hospital


def _profile_policy(organization):
    if organization is None:
        return ""
    try:
        return organization.capability_profile.branding_policy
    except (AttributeError, ObjectDoesNotExist):
        return ""


def _hospital_for(encounter, report=None):
    referral = getattr(encounter, "hospital_referral", None)
    if referral is None and report is not None:
        referral = report.hospital_referrals.select_related("source_hospital").first()
    return getattr(referral, "source_hospital", None)


def _sentinel_brand():
    logo_path = Path(settings.BASE_DIR) / "assets" / "sentinel-logo.png"
    return ReportBrand(
        name="Sentinel",
        logo_path=str(logo_path) if logo_path.exists() else None,
    )


def _organization_brand(organization, fallback_name):
    return ReportBrand(
        name=getattr(organization, "name", "") or fallback_name,
        organization=organization,
    )


def resolve_report_branding(encounter, clinic=None, report=None):
    """
    Resolve patient-facing report brands from the selected organisation policy.

    Hospital-sponsored encounters use the hospital's policy. Clinic-direct
    encounters use the clinic's policy. Diabetic and combined pathways always
    retain the small mandatory "Powered by Sentinel" acknowledgement, even
    when their main header is white-labelled.
    """
    clinic = clinic or getattr(getattr(encounter, "patient", None), "assigned_clinic", None)
    hospital = _hospital_for(encounter, report=report)
    owner = hospital or getattr(encounter, "originating_organization", None) or clinic
    policy = _profile_policy(owner) or "organization_and_sentinel"

    organization_brand = _organization_brand(owner, "Clinical service")
    clinic_brand = _organization_brand(clinic, "Reporting clinic") if clinic else None
    hospital_brand = _organization_brand(hospital, "Referring hospital") if hospital else None
    sentinel_brand = _sentinel_brand()

    if policy == "sentinel_only":
        brands = (sentinel_brand,)
    elif policy == "organization_only":
        brands = (organization_brand,)
    elif policy == "hospital_and_sentinel":
        brands = tuple(
            brand for brand in (hospital_brand or organization_brand, sentinel_brand) if brand
        )
    elif policy == "hospital_clinic_sentinel":
        ordered = (hospital_brand, clinic_brand, sentinel_brand)
        brands = tuple(
            brand
            for index, brand in enumerate(ordered)
            if brand and brand.name not in {item.name for item in ordered[:index] if item}
        )
    else:  # organization_and_sentinel, including the safe legacy default
        brands = (organization_brand, sentinel_brand)

    includes_diabetic = bool(
        getattr(encounter, "includes_diabetic_screening", False)
    )
    powered_by_sentinel = includes_diabetic or any(
        brand.name == "Sentinel" for brand in brands
    )
    return ReportBranding(
        policy=policy,
        owner=owner,
        clinic=clinic,
        hospital=hospital,
        brands=brands,
        powered_by_sentinel=powered_by_sentinel,
    )
