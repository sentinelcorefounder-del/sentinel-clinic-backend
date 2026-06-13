
from pathlib import Path

OPS_VIEWS = Path("ops/views.py")

if not OPS_VIEWS.exists():
    raise SystemExit("Could not find ops/views.py. Run this from backend folder.")

text = OPS_VIEWS.read_text(encoding="utf-8")

if "def patch(self, request, pk):" in text and "hospital_pricing_updated" in text:
    print("OpsHospitalDetailView already supports PATCH pricing edits.")
    raise SystemExit(0)

old = '''class OpsHospitalDetailView(OpsOnlyMixin, APIView):
    def get(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        hospital = Organization.objects.filter(pk=pk, organization_type="hospital").first()
        if not hospital:
            return Response({"detail": "Hospital not found."}, status=status.HTTP_404_NOT_FOUND)

        referrals = HospitalReferral.objects.select_related("patient", "matched_clinic", "report").filter(source_hospital=hospital)
        payments = OpsPayment.objects.select_related("referral").filter(referral__source_hospital=hospital)

        return Response(
            {
                "hospital": {
                    "id": hospital.id,
                    "code": hospital.clinic_id,
                    "name": hospital.name,
                    "contact_email": hospital.contact_email,
                    "phone": hospital.phone,
                    "address": hospital.address,
                    "screening_fee_amount": hospital.screening_fee_amount,
                    "hospital_commission_amount": hospital.hospital_commission_amount,
                    "currency": hospital.currency,
                },
                "referrals": OpsReferralSerializer(referrals, many=True).data,
                "payments": OpsPaymentSerializer(payments, many=True).data,
            }
        )
'''

new = '''class OpsHospitalDetailView(OpsOnlyMixin, APIView):
    def _build_response(self, request, hospital):
        referrals = HospitalReferral.objects.select_related("patient", "matched_clinic", "report").filter(source_hospital=hospital)
        payments = OpsPayment.objects.select_related("referral").filter(referral__source_hospital=hospital)

        return Response(
            {
                "hospital": {
                    "id": hospital.id,
                    "code": hospital.clinic_id,
                    "name": hospital.name,
                    "contact_email": hospital.contact_email,
                    "phone": hospital.phone,
                    "address": hospital.address,
                    "screening_fee_amount": hospital.screening_fee_amount,
                    "hospital_commission_amount": hospital.hospital_commission_amount,
                    "currency": hospital.currency,
                },
                "referrals": OpsReferralSerializer(referrals, many=True).data,
                "payments": OpsPaymentSerializer(payments, many=True).data,
            }
        )

    def get(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        hospital = Organization.objects.filter(pk=pk, organization_type="hospital").first()
        if not hospital:
            return Response({"detail": "Hospital not found."}, status=status.HTTP_404_NOT_FOUND)

        return self._build_response(request, hospital)

    def patch(self, request, pk):
        denied = self.check_ops_permission(request)
        if denied:
            return denied

        hospital = Organization.objects.filter(pk=pk, organization_type="hospital").first()
        if not hospital:
            return Response({"detail": "Hospital not found."}, status=status.HTTP_404_NOT_FOUND)

        for field in ["name", "contact_email", "phone", "address", "currency"]:
            if field in request.data:
                value = request.data.get(field)
                if field == "currency":
                    value = (value or "NGN").strip().upper()
                setattr(hospital, field, value)

        for field in ["screening_fee_amount", "hospital_commission_amount"]:
            if field in request.data:
                try:
                    value = Decimal(str(request.data.get(field) or "0").replace(",", "").strip())
                except Exception:
                    return Response(
                        {"detail": f"{field.replace('_', ' ').title()} must be a valid number."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                if value < 0:
                    return Response(
                        {"detail": f"{field.replace('_', ' ').title()} cannot be negative."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                setattr(hospital, field, value)

        hospital.save(update_fields=[
            "name",
            "contact_email",
            "phone",
            "address",
            "currency",
            "screening_fee_amount",
            "hospital_commission_amount",
        ])

        create_audit_log(
            actor=request.user,
            action="hospital_pricing_updated",
            entity_type="hospital",
            entity_id=hospital.id,
            entity_label=hospital.clinic_id,
            message=f"Hospital {hospital.name} charge updated.",
            metadata={
                "screening_fee_amount": str(hospital.screening_fee_amount),
                "hospital_commission_amount": str(hospital.hospital_commission_amount),
                "currency": hospital.currency,
            },
        )

        return self._build_response(request, hospital)
'''

if old not in text:
    raise SystemExit("Expected OpsHospitalDetailView block not found. Send latest backend/ops/views.py.")

text = text.replace(old, new, 1)
OPS_VIEWS.write_text(text, encoding="utf-8")
print("DONE: hospital detail PATCH endpoint added.")
