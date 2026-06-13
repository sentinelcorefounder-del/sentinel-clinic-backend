from pathlib import Path

VIEWS = Path("referrals/views.py")
if not VIEWS.exists():
    raise SystemExit("Could not find referrals/views.py. Run from backend folder.")

text = VIEWS.read_text(encoding="utf-8")
old = """        queryset = HospitalReferral.objects.select_related(
            "source_hospital",
            "patient",
            "matched_clinic",
            "report",
        ).all()
"""
new = """        queryset = HospitalReferral.objects.select_related(
            "source_hospital",
            "patient",
            "matched_clinic",
            "report",
        ).prefetch_related("ops_payments").all()
"""
if old in text:
    text = text.replace(old, new, 1)
    VIEWS.write_text(text, encoding="utf-8")
    print("DONE: referrals queryset now prefetches ops_payments for payment status.")
elif 'prefetch_related("ops_payments")' in text:
    print("Already patched: ops_payments prefetch exists.")
else:
    print("Expected queryset block not found. This optimisation is optional.")
