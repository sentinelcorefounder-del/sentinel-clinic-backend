from reports.models import StructuredReport
from django.db.models import Count

duplicates = list(
    StructuredReport.objects.values("encounter_id")
    .annotate(report_count=Count("id"))
    .filter(report_count__gt=1)
)

if duplicates:
    print("DUPLICATE REPORTS FOUND:")
    for item in duplicates:
        reports = StructuredReport.objects.filter(
            encounter_id=item["encounter_id"]
        ).order_by("-updated_at", "-id")
        print(
            "Encounter",
            item["encounter_id"],
            "reports:",
            [(r.id, r.report_id, r.report_status) for r in reports],
        )
    raise SystemExit(
        "Resolve duplicate reports before running the OneToOne migration."
    )

print("OK: No encounter currently has more than one structured report.")
