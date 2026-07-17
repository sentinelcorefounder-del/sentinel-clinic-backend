import re
from difflib import SequenceMatcher

from django.db import transaction

from .models import (
    MasterPatient,
    Patient,
    PatientIdentityReview,
    PatientOrganizationIdentity,
)


def normalise_text(value):
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def normalise_phone(value):
    return re.sub(r"\D", "", value or "")[-11:]


def generate_sentinel_patient_id():
    latest = (
        MasterPatient.objects.filter(
            sentinel_patient_id__startswith="SNT-PAT-"
        )
        .order_by("-id")
        .first()
    )
    next_number = (latest.id + 1) if latest else 1
    candidate = f"SNT-PAT-{next_number:08d}"

    while MasterPatient.objects.filter(
        sentinel_patient_id=candidate
    ).exists():
        next_number += 1
        candidate = f"SNT-PAT-{next_number:08d}"

    return candidate


def score_master_match(patient, master):
    score = 0
    reasons = []

    patient_first = normalise_text(patient.first_name)
    patient_last = normalise_text(patient.last_name)
    master_first = normalise_text(master.first_name)
    master_last = normalise_text(master.last_name)

    if patient.date_of_birth == master.date_of_birth:
        score += 35
        reasons.append("same date of birth")

    if patient.sex and master.sex and patient.sex == master.sex:
        score += 10
        reasons.append("same sex")

    first_similarity = SequenceMatcher(
        None, patient_first, master_first
    ).ratio()
    last_similarity = SequenceMatcher(
        None, patient_last, master_last
    ).ratio()

    if first_similarity >= 0.92:
        score += 20
        reasons.append("very similar first name")
    elif first_similarity >= 0.75:
        score += 10
        reasons.append("similar first name")

    if last_similarity >= 0.92:
        score += 20
        reasons.append("very similar last name")
    elif last_similarity >= 0.75:
        score += 10
        reasons.append("similar last name")

    patient_phone = normalise_phone(patient.phone)
    master_phone = normalise_phone(master.primary_phone)
    if (
        patient_phone
        and master_phone
        and patient_phone == master_phone
    ):
        score += 25
        reasons.append("same phone")

    if (
        patient.email
        and master.primary_email
        and patient.email.strip().lower()
        == master.primary_email.strip().lower()
    ):
        score += 25
        reasons.append("same email")

    return min(score, 100), reasons


def find_master_candidates(patient, minimum_score=45):
    candidates = MasterPatient.objects.filter(
        date_of_birth=patient.date_of_birth,
    ).exclude(identity_status="merged")

    rows = []
    for master in candidates:
        score, reasons = score_master_match(patient, master)
        if score >= minimum_score:
            rows.append((master, score, reasons))

    return sorted(rows, key=lambda row: row[1], reverse=True)


@transaction.atomic
def ensure_master_identity(patient, organization=None, local_id=""):
    if patient.master_patient_id:
        master = patient.master_patient
    else:
        candidates = find_master_candidates(patient)

        exact = next(
            (
                row for row in candidates
                if row[1] >= 90
            ),
            None,
        )

        if exact:
            master = exact[0]
            patient.master_patient = master
            patient.save(
                update_fields=["master_patient", "updated_at"]
            )
        else:
            master = MasterPatient.objects.create(
                sentinel_patient_id=generate_sentinel_patient_id(),
                first_name=patient.first_name,
                last_name=patient.last_name,
                date_of_birth=patient.date_of_birth,
                sex=patient.sex or "",
                primary_phone=patient.phone or "",
                primary_email=patient.email or "",
                identity_status=(
                    "possible_duplicate" if candidates else "active"
                ),
            )
            patient.master_patient = master
            patient.save(
                update_fields=["master_patient", "updated_at"]
            )

            for candidate, score, reasons in candidates[:5]:
                PatientIdentityReview.objects.get_or_create(
                    candidate_patient=patient,
                    possible_master_patient=candidate,
                    defaults={
                        "match_score": score,
                        "match_reasons": reasons,
                    },
                )

    if organization and local_id:
        PatientOrganizationIdentity.objects.update_or_create(
            organization=organization,
            identity_type="legacy_patient_id",
            local_identifier=local_id,
            defaults={
                "master_patient": master,
                "is_verified": True,
            },
        )

    return master


@transaction.atomic
def link_patient_to_master(patient, master, reviewed_by=None, note=""):
    old_master = patient.master_patient
    patient.master_patient = master
    patient.save(update_fields=["master_patient", "updated_at"])

    PatientIdentityReview.objects.filter(
        candidate_patient=patient
    ).update(
        status="linked",
        reviewed_by=reviewed_by,
        reviewed_at=__import__(
            "django.utils.timezone"
        ).utils.timezone.now(),
        decision_note=note,
    )

    if old_master and old_master != master:
        old_master.identity_status = "merged"
        old_master.merged_into = master
        old_master.save(
            update_fields=[
                "identity_status",
                "merged_into",
                "updated_at",
            ]
        )

    return patient
