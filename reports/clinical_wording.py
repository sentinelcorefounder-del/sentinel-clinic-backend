def _eye_wording(dr_grade, mac_grade, eye_name):
    dr_grade = (dr_grade or "").upper()
    mac_grade = (mac_grade or "").upper()

    if dr_grade == "U" or mac_grade == "U":
        return (
            f"{eye_name}: image assessment was ungradable or incomplete; "
            "repeat imaging or clinical examination is required."
        )

    if dr_grade == "R0":
        dr_text = "no diabetic retinopathy was identified"
    elif dr_grade == "R1":
        dr_text = "mild background diabetic retinal changes were identified"
    elif dr_grade == "R2":
        dr_text = "pre-proliferative diabetic retinal changes were identified"
    elif dr_grade in {"R3A", "R3S"}:
        dr_text = "proliferative or previously treated proliferative diabetic retinal changes were identified"
    else:
        dr_text = "the diabetic retinopathy grade was not recorded"

    if mac_grade == "M0":
        mac_text = (
            "no diabetic maculopathy meeting the programme referral "
            "threshold was identified"
        )
    elif mac_grade == "M1":
        mac_text = "features meeting the diabetic maculopathy referral threshold were identified"
    else:
        mac_text = "the maculopathy grade was not recorded"

    return f"{eye_name}: {dr_text}; {mac_text}."


def build_clinical_summary(report):
    left = _eye_wording(
        report.left_dr_grade,
        report.left_maculopathy_grade,
        "Left eye",
    )
    right = _eye_wording(
        report.right_dr_grade,
        report.right_maculopathy_grade,
        "Right eye",
    )

    outcome = {
        "routine_followup": (
            "Overall outcome: routine follow-up is appropriate, subject "
            "to the clinician's full assessment."
        ),
        "early_review": (
            "Overall outcome: an earlier clinical review is recommended."
        ),
        "urgent_referral": (
            "Overall outcome: urgent referral is recommended."
        ),
        "ophthalmology_required": (
            "Overall outcome: ophthalmology assessment is required."
        ),
        "image_retake": (
            "Overall outcome: repeat retinal imaging is required."
        ),
    }.get(
        report.urgency_outcome,
        "Overall outcome: follow the clinician's recorded recommendation.",
    )

    return "\n".join([left, right, outcome])


def apply_generated_wording(report):
    generated = build_clinical_summary(report)
    report.generated_clinical_summary = generated

    if not report.clinical_summary_overridden:
        report.final_clinical_summary = generated

    return report
