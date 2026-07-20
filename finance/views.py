from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
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
)


class FinanceAdminViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAdminUser]


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
    permission_classes = [IsAdminUser]
    serializer_class = EncounterFinancialRecordSerializer
    queryset = EncounterFinancialRecord.objects.select_related(
        "encounter",
        "encounter__originating_organization",
        "contract",
        "pricing_rule",
    ).prefetch_related("allocations", "allocations__beneficiary_organization")

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
    permission_classes = [IsAdminUser]
    serializer_class = WalletLedgerEntrySerializer
    queryset = WalletLedgerEntry.objects.select_related(
        "wallet", "wallet__organization", "financial_record", "reservation", "actor"
    ).all()


class WalletReservationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAdminUser]
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
