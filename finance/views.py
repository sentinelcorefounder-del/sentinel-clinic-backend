from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from .models import (
    AllocationRule, EncounterFinancialRecord, PartnerContract, PricingRule,
    OrganizationWallet, WalletLedgerEntry, WalletReservation, SettlementBatch,
)
from .serializers import (
    AllocationRuleSerializer,
    EncounterFinancialRecordSerializer,
    PartnerContractSerializer,
    PricingRuleSerializer,
    OrganizationWalletSerializer, WalletLedgerEntrySerializer, WalletReservationSerializer, SettlementBatchSerializer,
)
from .services import (
    price_encounter, top_up_wallet, adjust_wallet, reserve_wallet_funds,
    capture_wallet_reservation, release_wallet_reservation, refund_to_wallet,
    reserve_financial_record_from_originating_wallet, capture_financial_record_wallet_reservation,
    create_settlement_batch, approve_settlement_batch, mark_settlement_batch_paid,
    sync_encounter_finance_lifecycle,
)




class IsSentinelFinanceOps(BasePermission):
    message = "You do not have permission to access Sentinel Finance."
    allowed_groups = {"ops_admin", "sentinel_ops", "super_admin", "finance_tester"}

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return user.groups.filter(name__in=self.allowed_groups).exists()


class FinanceAdminViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsSentinelFinanceOps]


class PartnerContractViewSet(FinanceAdminViewSet):
    queryset = PartnerContract.objects.select_related("organization").all()
    serializer_class = PartnerContractSerializer


class PricingRuleViewSet(FinanceAdminViewSet):
    queryset = PricingRule.objects.select_related("contract", "contract__organization").all()
    serializer_class = PricingRuleSerializer


class AllocationRuleViewSet(FinanceAdminViewSet):
    queryset = AllocationRule.objects.select_related(
        "pricing_rule", "beneficiary_organization"
    ).all()
    serializer_class = AllocationRuleSerializer


class EncounterFinancialRecordViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsSentinelFinanceOps]
    serializer_class = EncounterFinancialRecordSerializer
    queryset = EncounterFinancialRecord.objects.select_related(
        "encounter",
        "encounter__originating_organization",
        "contract",
        "pricing_rule",
    ).prefetch_related("allocations", "allocations__beneficiary_organization")

    def get_queryset(self):
        queryset = super().get_queryset()
        encounter_id = (self.request.query_params.get("encounter_id") or "").strip()
        status_value = (self.request.query_params.get("status") or "").strip()
        if encounter_id:
            queryset = queryset.filter(encounter__encounter_id=encounter_id)
        if status_value:
            queryset = queryset.filter(status=status_value)
        return queryset

    @action(detail=True, methods=["post"], url_path="sync-lifecycle")
    def sync_lifecycle(self, request, pk=None):
        record = self.get_object()
        try:
            record = sync_encounter_finance_lifecycle(record.encounter, actor=request.user)
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(record).data)

    @action(detail=True, methods=["post"])
    def price(self, request, pk=None):
        record = self.get_object()
        try:
            record = price_encounter(
                record.encounter,
                actor=request.user,
                force=bool(request.data.get("force", False)),
            )
        except DjangoValidationError as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(record).data)

    @action(detail=True, methods=["post"], url_path="reserve-originating-wallet")
    def reserve_originating_wallet(self, request, pk=None):
        record = self.get_object()
        try:
            reservation = reserve_financial_record_from_originating_wallet(
                record, actor=request.user, reference=request.data.get("reference", "")
            )
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(WalletReservationSerializer(reservation).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="capture-wallet")
    def capture_wallet(self, request, pk=None):
        record = self.get_object()
        try:
            reservation = capture_financial_record_wallet_reservation(
                record, actor=request.user, reference=request.data.get("reference", "")
            )
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(WalletReservationSerializer(reservation).data)


class OrganizationWalletViewSet(FinanceAdminViewSet):
    queryset = OrganizationWallet.objects.select_related("organization").all()
    serializer_class = OrganizationWalletSerializer

    @action(detail=True, methods=["post"], url_path="top-up")
    def top_up(self, request, pk=None):
        wallet = self.get_object()
        try:
            entry = top_up_wallet(
                wallet=wallet,
                amount=request.data.get("amount"),
                idempotency_key=request.data.get("idempotency_key", ""),
                actor=request.user,
                reference=request.data.get("reference", ""),
                description=request.data.get("description", ""),
                metadata=request.data.get("metadata") or {},
            )
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(WalletLedgerEntrySerializer(entry).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        wallet = self.get_object()
        try:
            entry = adjust_wallet(
                wallet=wallet,
                available_delta=request.data.get("available_delta"),
                idempotency_key=request.data.get("idempotency_key", ""),
                actor=request.user,
                reference=request.data.get("reference", ""),
                description=request.data.get("description", ""),
                metadata=request.data.get("metadata") or {},
            )
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(WalletLedgerEntrySerializer(entry).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        wallet = self.get_object()
        record = None
        record_id = request.data.get("financial_record_id")
        if record_id:
            try:
                record = EncounterFinancialRecord.objects.get(pk=record_id)
            except EncounterFinancialRecord.DoesNotExist:
                return Response({"detail": "Financial record not found."}, status=status.HTTP_404_NOT_FOUND)
        try:
            entry = refund_to_wallet(
                wallet=wallet,
                amount=request.data.get("amount"),
                idempotency_key=request.data.get("idempotency_key", ""),
                financial_record=record,
                actor=request.user,
                reference=request.data.get("reference", ""),
            )
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(WalletLedgerEntrySerializer(entry).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reserve(self, request, pk=None):
        wallet = self.get_object()
        try:
            record = EncounterFinancialRecord.objects.get(pk=request.data.get("financial_record_id"))
            reservation = reserve_wallet_funds(
                wallet=wallet,
                financial_record=record,
                amount=request.data.get("amount"),
                idempotency_key=request.data.get("idempotency_key", ""),
                actor=request.user,
                reference=request.data.get("reference", ""),
            )
        except EncounterFinancialRecord.DoesNotExist:
            return Response({"detail": "Financial record not found."}, status=status.HTTP_404_NOT_FOUND)
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(WalletReservationSerializer(reservation).data, status=status.HTTP_201_CREATED)


class WalletLedgerEntryViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsSentinelFinanceOps]
    serializer_class = WalletLedgerEntrySerializer
    queryset = WalletLedgerEntry.objects.select_related(
        "wallet", "wallet__organization", "financial_record", "reservation", "actor"
    ).all()


class WalletReservationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsSentinelFinanceOps]
    serializer_class = WalletReservationSerializer
    queryset = WalletReservation.objects.select_related(
        "wallet", "wallet__organization", "financial_record", "financial_record__encounter"
    ).all()

    @action(detail=True, methods=["post"])
    def capture(self, request, pk=None):
        reservation = self.get_object()
        try:
            reservation = capture_wallet_reservation(
                reservation,
                amount=request.data.get("amount"),
                idempotency_key=request.data.get("idempotency_key"),
                actor=request.user,
                reference=request.data.get("reference", ""),
            )
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(reservation).data)

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        reservation = self.get_object()
        try:
            reservation = release_wallet_reservation(
                reservation,
                amount=request.data.get("amount"),
                idempotency_key=request.data.get("idempotency_key"),
                actor=request.user,
                reference=request.data.get("reference", ""),
            )
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(reservation).data)


class SettlementBatchViewSet(FinanceAdminViewSet):
    serializer_class = SettlementBatchSerializer
    queryset = SettlementBatch.objects.select_related(
        "beneficiary_organization", "approved_by"
    ).prefetch_related("items", "items__allocation", "items__allocation__financial_record")

    def create(self, request, *args, **kwargs):
        try:
            from organizations.models import Organization
            organization = Organization.objects.get(pk=request.data.get("beneficiary_organization"))
            batch = create_settlement_batch(
                beneficiary_organization=organization,
                period_start=parse_date(request.data.get("period_start", "")),
                period_end=parse_date(request.data.get("period_end", "")),
                currency=request.data.get("currency", "NGN"),
                actor=request.user,
            )
        except Organization.DoesNotExist:
            return Response({"detail": "Beneficiary organisation not found."}, status=status.HTTP_404_NOT_FOUND)
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(batch).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        try:
            batch = approve_settlement_batch(self.get_object(), actor=request.user)
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(batch).data)

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        try:
            batch = mark_settlement_batch_paid(
                self.get_object(),
                external_reference=request.data.get("external_reference", ""),
                actor=request.user,
            )
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(batch).data)


from django.db import models
from django.db.models import Sum
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from common.tenant import get_user_organization
from organizations.models import Organization
from .serializers import PartnerFinanceSummarySerializer


def _active_contract_for(organization):
    today = timezone.localdate()
    return (
        PartnerContract.objects.filter(
            organization=organization,
            status=PartnerContract.Status.ACTIVE,
            effective_from__lte=today,
        )
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gte=today))
        .order_by("-effective_from")
        .first()
    )


class FinanceDashboardSummaryView(APIView):
    permission_classes = [IsAuthenticated, IsSentinelFinanceOps]

    def get(self, request):
        wallet_totals = WalletLedgerEntry.objects.aggregate(
            available=Sum("available_delta"), reserved=Sum("reserved_delta")
        )
        settlement_totals = SettlementBatch.objects.values("status").annotate(total=Sum("total_amount"))
        return Response({
            "contracts": PartnerContract.objects.count(),
            "active_contracts": PartnerContract.objects.filter(status=PartnerContract.Status.ACTIVE).count(),
            "pricing_rules": PricingRule.objects.filter(is_active=True).count(),
            "wallets": OrganizationWallet.objects.filter(is_active=True).count(),
            "wallet_available": str(wallet_totals["available"] or 0),
            "wallet_reserved": str(wallet_totals["reserved"] or 0),
            "financial_records": EncounterFinancialRecord.objects.count(),
            "awaiting_payment": EncounterFinancialRecord.objects.filter(status=EncounterFinancialRecord.Status.AWAITING_PAYMENT).count(),
            "captured": EncounterFinancialRecord.objects.filter(status=EncounterFinancialRecord.Status.CAPTURED).count(),
            "settlements": {row["status"]: str(row["total"] or 0) for row in settlement_totals},
        })


class PartnerFinanceView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organization = get_user_organization(request.user)
        if not organization:
            return Response({"detail": "No organisation is linked to this account."}, status=status.HTTP_403_FORBIDDEN)
        wallet = OrganizationWallet.objects.filter(organization=organization, currency="NGN", is_active=True).first()
        contract = _active_contract_for(organization)
        rules = PricingRule.objects.none()
        if contract:
            rules = contract.pricing_rules.filter(is_active=True).prefetch_related("allocation_rules")
        ledger = WalletLedgerEntry.objects.none()
        if wallet:
            ledger = wallet.ledger_entries.select_related("financial_record")[:50]
        records = EncounterFinancialRecord.objects.select_related(
            "encounter", "contract", "pricing_rule", "encounter__originating_organization"
        ).filter(encounter__originating_organization=organization)[:25]
        data = {
            "organization_id": organization.id,
            "organization_name": organization.name,
            "organization_type": organization.organization_type,
            "wallet": wallet,
            "active_contract": contract,
            "active_pricing_rules": rules,
            "recent_ledger": ledger,
            "recent_financial_records": records,
        }
        return Response(PartnerFinanceSummarySerializer(data).data)


class FinanceOrganizationOptionsView(APIView):
    permission_classes = [IsAuthenticated, IsSentinelFinanceOps]

    def get(self, request):
        organizations = Organization.objects.filter(is_active=True).order_by("name")
        return Response([
            {
                "id": organization.id,
                "name": organization.name,
                "organization_type": organization.organization_type,
                "clinic_id": organization.clinic_id,
            }
            for organization in organizations
        ])
