from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from .models import (
    AllocationRule, EncounterFinancialRecord, PartnerContract, PricingRule,
    OrganizationWallet, WalletLedgerEntry, WalletReservation, SettlementBatch,
    BankTransferFundingRequest,
    ServiceAllowance, ServiceAllowanceReservation,
    FinanceActionRequest, FinanceControlAudit,
)
from .serializers import (
    AllocationRuleSerializer,
    EncounterFinancialRecordSerializer,
    PartnerContractSerializer,
    PricingRuleSerializer,
    OrganizationWalletSerializer, WalletLedgerEntrySerializer, WalletReservationSerializer, SettlementBatchSerializer,
    BankTransferFundingRequestSerializer,
    ServiceAllowanceSerializer, ServiceAllowanceReservationSerializer,
    FinanceActionRequestSerializer, FinanceControlAuditSerializer,
)
from .services import (
    price_encounter, top_up_wallet, adjust_wallet, reserve_wallet_funds,
    capture_wallet_reservation, release_wallet_reservation, refund_to_wallet,
    reserve_financial_record_from_originating_wallet, capture_financial_record_wallet_reservation,
    create_settlement_batch, approve_settlement_batch, mark_settlement_batch_paid,
    cancel_settlement_batch,
    sync_encounter_finance_lifecycle,
    submit_bank_transfer_proof, verify_bank_transfer, approve_bank_transfer, reject_bank_transfer,
    approve_service_allowance,
    create_finance_action_request, approve_finance_action_request,
    reject_finance_action_request, reconcile_finance_controls,
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


class FinanceRolePermission(BasePermission):
    role_groups = {
        "viewer": {"finance_viewer", "finance_operator", "finance_approver", "finance_admin",
                   "ops_admin", "sentinel_ops", "super_admin", "finance_tester"},
        "operator": {"finance_operator", "finance_admin", "super_admin", "finance_tester"},
        "approver": {"finance_approver", "finance_admin", "super_admin", "finance_tester"},
        "admin": {"finance_admin", "super_admin", "finance_tester"},
    }
    required_role = "viewer"

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return user.groups.filter(name__in=self.role_groups[self.required_role]).exists()


class IsFinanceViewer(FinanceRolePermission):
    required_role = "viewer"


class IsFinanceOperator(FinanceRolePermission):
    required_role = "operator"


class IsFinanceApprover(FinanceRolePermission):
    required_role = "approver"


class IsFinanceAdministrator(FinanceRolePermission):
    required_role = "admin"


class FinanceAdminViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsFinanceAdministrator]

    def get_permissions(self):
        role = IsFinanceViewer if self.action in {"list", "retrieve"} else IsFinanceAdministrator
        return [IsAuthenticated(), role()]


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


class ServiceAllowanceViewSet(FinanceAdminViewSet):
    serializer_class = ServiceAllowanceSerializer
    queryset = ServiceAllowance.objects.select_related(
        "organization", "contract", "approved_by"
    ).all()

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            role = IsFinanceViewer
        elif self.action == "approve":
            role = IsFinanceApprover
        else:
            role = IsFinanceAdministrator
        return [IsAuthenticated(), role()]

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        try:
            allowance = approve_service_allowance(self.get_object(), actor=request.user)
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(allowance).data)


class ServiceAllowanceReservationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsSentinelFinanceOps]
    serializer_class = ServiceAllowanceReservationSerializer
    queryset = ServiceAllowanceReservation.objects.select_related(
        "allowance", "allowance__organization", "financial_record",
        "financial_record__encounter", "actor",
    ).all()


class EncounterFinancialRecordViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsFinanceViewer]
    serializer_class = EncounterFinancialRecordSerializer
    queryset = EncounterFinancialRecord.objects.select_related(
        "encounter",
        "encounter__originating_organization",
        "contract",
        "pricing_rule",
    ).prefetch_related("allocations", "allocations__beneficiary_organization")

    def get_permissions(self):
        role = IsFinanceViewer if self.action in {"list", "retrieve"} else IsFinanceOperator
        return [IsAuthenticated(), role()]

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

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            role = IsFinanceViewer
        elif self.action in {"top_up", "reserve"}:
            role = IsFinanceOperator
        else:
            role = IsFinanceAdministrator
        return [IsAuthenticated(), role()]

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
        return Response(
            {"detail": "Direct wallet adjustment is disabled. Create a finance action request."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        return Response(
            {"detail": "Direct refund is disabled. Create a finance action request."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

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
    permission_classes = [IsAuthenticated, IsFinanceViewer]
    serializer_class = WalletLedgerEntrySerializer
    queryset = WalletLedgerEntry.objects.select_related(
        "wallet", "wallet__organization", "financial_record", "reservation", "actor"
    ).all()


class WalletReservationViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsFinanceViewer]
    serializer_class = WalletReservationSerializer
    queryset = WalletReservation.objects.select_related(
        "wallet", "wallet__organization", "financial_record", "financial_record__encounter"
    ).all()

    def get_permissions(self):
        role = IsFinanceViewer if self.action in {"list", "retrieve"} else IsFinanceOperator
        return [IsAuthenticated(), role()]

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


class BankTransferFundingRequestViewSet(viewsets.ModelViewSet):
    serializer_class = BankTransferFundingRequestSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]
    queryset = BankTransferFundingRequest.objects.select_related(
        "wallet", "wallet__organization", "requester", "verified_by", "approved_by", "ledger_entry"
    ).all()

    def _is_finance_ops(self):
        return IsSentinelFinanceOps().has_permission(self.request, self)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self._is_finance_ops():
            status_value = (self.request.query_params.get("status") or "").strip()
            return queryset.filter(status=status_value) if status_value else queryset
        organization = get_user_organization(self.request.user)
        return queryset.filter(wallet__organization=organization) if organization else queryset.none()

    def perform_create(self, serializer):
        wallet = serializer.validated_data["wallet"]
        if not self._is_finance_ops():
            organization = get_user_organization(self.request.user)
            if organization is None or wallet.organization_id != organization.id:
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("You can only request funding for your own organisation.")
        instance = serializer.save(requester=self.request.user, currency=wallet.currency)
        instance.full_clean()
        instance.save()

    @action(detail=True, methods=["post"], url_path="submit-proof")
    def submit_proof(self, request, pk=None):
        try:
            funding_request = submit_bank_transfer_proof(
                self.get_object(), request.FILES.get("proof"), actor=request.user
            )
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(funding_request).data)

    def _require_finance_ops(self):
        if not self._is_finance_ops():
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Sentinel Finance permission is required.")

    def _require_finance_role(self, permission_class):
        if not permission_class().has_permission(self.request, self):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("The required Sentinel Finance role is not assigned.")

    @action(detail=True, methods=["post"])
    def verify(self, request, pk=None):
        self._require_finance_role(IsFinanceOperator)
        try:
            funding_request = verify_bank_transfer(
                self.get_object(),
                received_amount=request.data.get("received_amount"),
                bank_transaction_reference=request.data.get("bank_transaction_reference"),
                value_date=parse_date(request.data.get("value_date", "")),
                actor=request.user,
                notes=request.data.get("notes", ""),
            )
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(funding_request).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        self._require_finance_role(IsFinanceApprover)
        try:
            funding_request = approve_bank_transfer(self.get_object(), actor=request.user)
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(funding_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        self._require_finance_role(IsFinanceApprover)
        try:
            funding_request = reject_bank_transfer(
                self.get_object(), reason=request.data.get("reason"), actor=request.user
            )
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(funding_request).data)

class SettlementBatchViewSet(FinanceAdminViewSet):
    serializer_class = SettlementBatchSerializer
    queryset = SettlementBatch.objects.select_related(
        "beneficiary_organization", "approved_by"
    ).prefetch_related("items", "items__allocation", "items__allocation__financial_record")

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            role = IsFinanceViewer
        elif self.action in {"approve", "mark_paid"}:
            role = IsFinanceApprover
        else:
            role = IsFinanceOperator
        return [IsAuthenticated(), role()]

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
                payment_evidence=request.FILES.get("payment_evidence"),
            )
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(batch).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        try:
            batch = cancel_settlement_batch(
                self.get_object(), reason=request.data.get("reason", ""), actor=request.user
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
    permission_classes = [IsAuthenticated, IsFinanceViewer]

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
    permission_classes = [IsAuthenticated, IsFinanceViewer]

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


class FinanceActionRequestViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = FinanceActionRequestSerializer
    permission_classes = [IsAuthenticated, IsFinanceViewer]
    queryset = FinanceActionRequest.objects.select_related(
        "wallet", "wallet__organization", "financial_record", "related_entry",
        "requested_by", "decided_by", "posted_entry",
    ).all()

    def get_permissions(self):
        role = IsFinanceViewer
        if self.action == "create":
            role = IsFinanceOperator
        elif self.action in {"approve", "reject"}:
            role = IsFinanceApprover
        return [IsAuthenticated(), role()]

    def create(self, request, *args, **kwargs):
        try:
            wallet = OrganizationWallet.objects.get(pk=request.data.get("wallet"))
            record = EncounterFinancialRecord.objects.filter(
                pk=request.data.get("financial_record")
            ).first()
            related_entry = WalletLedgerEntry.objects.filter(pk=request.data.get("related_entry")).first()
            action_request = create_finance_action_request(
                action_type=request.data.get("action_type"), wallet=wallet,
                amount=request.data.get("amount"), reason=request.data.get("reason"),
                external_reference=request.data.get("external_reference"),
                idempotency_key=request.data.get("idempotency_key"), requested_by=request.user,
                financial_record=record, related_entry=related_entry,
                evidence=request.FILES.get("evidence"),
            )
        except OrganizationWallet.DoesNotExist:
            return Response({"detail": "Wallet not found."}, status=status.HTTP_404_NOT_FOUND)
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(action_request).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        try:
            action_request = approve_finance_action_request(self.get_object(), decided_by=request.user)
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(action_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        try:
            action_request = reject_finance_action_request(
                self.get_object(), decided_by=request.user, reason=request.data.get("reason")
            )
        except DjangoValidationError as exc:
            return Response({"detail": exc.messages}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(action_request).data)


class FinanceControlAuditViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = FinanceControlAuditSerializer
    permission_classes = [IsAuthenticated, IsFinanceViewer]
    queryset = FinanceControlAudit.objects.select_related(
        "actor", "wallet", "financial_record", "action_request", "settlement_batch"
    ).all()


class FinanceReconciliationView(APIView):
    permission_classes = [IsAuthenticated, IsFinanceViewer]

    def get(self, request):
        return Response(reconcile_finance_controls())


class FinanceCapabilitiesView(APIView):
    permission_classes = [IsAuthenticated, IsFinanceViewer]

    def get(self, request):
        def allowed(permission):
            return permission().has_permission(request, self)

        return Response({
            "can_view": True,
            "can_operate": allowed(IsFinanceOperator),
            "can_approve": allowed(IsFinanceApprover),
            "can_administer": allowed(IsFinanceAdministrator),
            "can_request_corrections": allowed(IsFinanceOperator),
            "can_decide_corrections": allowed(IsFinanceApprover),
            "can_prepare_settlements": allowed(IsFinanceOperator),
            "can_approve_settlements": allowed(IsFinanceApprover),
            "can_configure_pricing": allowed(IsFinanceAdministrator),
        })
