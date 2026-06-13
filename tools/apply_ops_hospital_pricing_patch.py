
from pathlib import Path

OPS_VIEWS = Path("ops/views.py")

if not OPS_VIEWS.exists():
    raise SystemExit("Could not find ops/views.py. Run this from the backend folder.")

text = OPS_VIEWS.read_text(encoding="utf-8")

old_amount_block = '''        try:
            amount = Decimal(str(request.data.get("amount", "15000")).replace(",", "").strip())
        except Exception:
            return Response({"detail": "Invalid amount."}, status=status.HTTP_400_BAD_REQUEST)
'''

new_amount_block = '''        hospital = referral.source_hospital

        default_amount = (
            hospital.screening_fee_amount
            if hospital and hospital.screening_fee_amount
            else Decimal("15000")
        )

        default_currency = (
            hospital.currency
            if hospital and hospital.currency
            else "NGN"
        )

        # Ops can still override amount manually if needed.
        # If no amount is sent, use the source hospital's configured screening fee.
        try:
            amount = Decimal(
                str(
                    request.data.get("amount", default_amount)
                ).replace(",", "").strip()
            )
        except Exception:
            return Response({"detail": "Invalid amount."}, status=status.HTTP_400_BAD_REQUEST)
'''

if old_amount_block in text:
    text = text.replace(old_amount_block, new_amount_block, 1)
    print("Patched payment amount default block.")
else:
    print("Amount block already patched or exact block not found.")

old_defaults = '''            defaults={
                "patient_email": patient_email,
                "amount": amount,
                "currency": "NGN",
                "status": "draft",
            },
'''

new_defaults = '''            defaults={
                "patient_email": patient_email,
                "amount": amount,
                "currency": default_currency,
                "status": "draft",
            },
'''

if old_defaults in text:
    text = text.replace(old_defaults, new_defaults, 1)
    print("Patched payment default currency.")
else:
    print("Payment defaults block already patched or exact block not found.")

old_existing = '''        if not created:
            payment.patient_email = patient_email
            payment.amount = amount
            payment.save(update_fields=["patient_email", "amount", "updated_at"])
'''

new_existing = '''        if not created:
            payment.patient_email = patient_email
            payment.amount = amount
            payment.currency = default_currency
            payment.save(update_fields=["patient_email", "amount", "currency", "updated_at"])
'''

if old_existing in text:
    text = text.replace(old_existing, new_existing, 1)
    print("Patched existing payment update block.")
else:
    print("Existing payment block already patched or exact block not found.")

old_hospital_list = '''                    "address": h.address,
                    "referrals_count": referrals.count(),
'''

new_hospital_list = '''                    "address": h.address,
                    "screening_fee_amount": h.screening_fee_amount,
                    "hospital_commission_amount": h.hospital_commission_amount,
                    "currency": h.currency,
                    "referrals_count": referrals.count(),
'''

if old_hospital_list in text:
    text = text.replace(old_hospital_list, new_hospital_list, 1)
    print("Patched OpsHospitalListView pricing fields.")
else:
    print("OpsHospitalListView pricing fields already patched or exact block not found.")

old_hospital_detail = '''                    "address": hospital.address,
                },
'''

new_hospital_detail = '''                    "address": hospital.address,
                    "screening_fee_amount": hospital.screening_fee_amount,
                    "hospital_commission_amount": hospital.hospital_commission_amount,
                    "currency": hospital.currency,
                },
'''

if old_hospital_detail in text:
    text = text.replace(old_hospital_detail, new_hospital_detail, 1)
    print("Patched OpsHospitalDetailView pricing fields.")
else:
    print("OpsHospitalDetailView pricing fields already patched or exact block not found.")

old_hospital_payload = '''                "temporary_password": request.data.get("temporary_password", ""),
                "is_active": True,
            }
'''

new_hospital_payload = '''                "temporary_password": request.data.get("temporary_password", ""),
                "is_active": True,
                "screening_fee_amount": request.data.get("screening_fee_amount", "15000"),
                "hospital_commission_amount": request.data.get("hospital_commission_amount", "0"),
                "currency": request.data.get("currency", "NGN"),
            }
'''

occurrences = []
start = 0
while True:
    idx = text.find(old_hospital_payload, start)
    if idx == -1:
        break
    occurrences.append(idx)
    start = idx + 1

if occurrences:
    target_index = occurrences[-1]
    text = text[:target_index] + new_hospital_payload + text[target_index + len(old_hospital_payload):]
    print("Patched hospital provisioning payload pricing fields.")
else:
    print("Hospital provisioning payload already patched or exact block not found.")

old_after_provision = '''            result = provision_hospital_with_admin(payload)

            create_audit_log(
'''

new_after_provision = '''            result = provision_hospital_with_admin(payload)

            hospital_org = (
                Organization.objects.filter(id=result.get("organization_id")).first()
                or Organization.objects.filter(clinic_id=payload["hospital_id"]).first()
            )

            if hospital_org:
                try:
                    hospital_org.screening_fee_amount = Decimal(str(payload.get("screening_fee_amount", "15000")).replace(",", "").strip())
                    hospital_org.hospital_commission_amount = Decimal(str(payload.get("hospital_commission_amount", "0")).replace(",", "").strip())
                    hospital_org.currency = payload.get("currency") or "NGN"
                    hospital_org.save(
                        update_fields=[
                            "screening_fee_amount",
                            "hospital_commission_amount",
                            "currency",
                        ]
                    )
                except Exception as exc:
                    print("Hospital pricing update failed:", exc)

            create_audit_log(
'''

if old_after_provision in text:
    text = text.replace(old_after_provision, new_after_provision, 1)
    print("Patched hospital provisioning post-create pricing save.")
else:
    print("Hospital post-provision pricing save already patched or exact block not found.")

OPS_VIEWS.write_text(text, encoding="utf-8")
print("DONE: ops/views.py pricing patch complete.")
