from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.db import transaction
from datetime import timedelta
import uuid
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.db.models import Count
from pathlib import Path
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError as DRFValidationError

from .models import (
    AllocationRule, EncounterFinancialRecord, PartnerContract, PricingRule,
    OrganizationWallet, WalletLedgerEntry, WalletReservation, SettlementBatch,
    BankTransferFundingRequest,
    ServiceAllowance, ServiceAllowanceReservation,
    FinanceActionRequest, FinanceControlAudit,
    BillingProfile,
)
from encounters.models import AssessmentServiceSession
from organizations.models import Organization
from .serializers import (
    AllocationRuleSerializer,
    EncounterFinancialRecordSerializer,
    PartnerContractSerializer,
    PricingRuleSerializer,
    OrganizationWalletSerializer, WalletLedgerEntrySerializer, WalletReservationSerializer, SettlementBatchSerializer,
    BankTransferFundingRequestSerializer,
    ServiceAllowanceSerializer, ServiceAllowanceReservationSerializer,
    FinanceActionRequestSerializer, FinanceControlAuditSerializer,
    BillingProfileSerializer,
    AssessmentServiceSessionSerializer, ServicePartnerSerializer,
)
from .permissions import (
    IsInternalFinanceAdministrator, IsInternalFinanceApprover,
    IsInternalFinanceOperator, has_internal_finance_role,
)
from .documents import UnreliableDocumentSnapshot, render_bank_transfer_document
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


def _audit_internal(action, actor, *, before=None, after=None, metadata=None):
    FinanceControlAudit.objects.create(
        action=action, actor=actor, before_state=before or {},
        after_state=after or {}, metadata=metadata or {},
    )


def _internal_evidence_role(user):
    return any(has_internal_finance_role(user, role) for role in (
        "administrator", "operator", "approver"
    ))


def _evidence_response(file_field):
    if not file_field or not file_field.name:
        raise Http404("Evidence file not found.")
    try:
        stream = file_field.open("rb")
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise Http404("Evidence file not found.") from exc
    filename = Path(file_field.name).name
    return FileResponse(stream, as_attachment=True, filename=filename)


class ServicePartnerViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsInternalFinanceAdministrator]
    serializer_class = ServicePartnerSerializer
    queryset = Organization.objects.filter(organization_type="service_partner").order_by("name")
    http_method_names = ["get", "post", "patch", "head", "options"]

    def perform_create(self, serializer):
        with transaction.atomic():
            partner = serializer.save()
            _audit_internal(
                "service_partner_created", self.request.user,
                after={"id": partner.id, "code": partner.clinic_id, "name": partner.name,
                       "is_active": partner.is_active},
            )

    def perform_update(self, serializer):
        with transaction.atomic():
            partner = get_object_or_404(
                self.get_queryset().select_for_update(), pk=serializer.instance.pk
            )
            self.check_object_permissions(self.request, partner)
            changed = any(
                getattr(partner, field) != value
                for field, value in serializer.validated_data.items()
            )
            serializer.instance = partner
            if not changed:
                return
            before = {"code": partner.clinic_id, "name": partner.name, "is_active": partner.is_active}
            partner = serializer.save()
            action_name = "service_partner_deactivated" if before["is_active"] and not partner.is_active else "service_partner_updated"
            _audit_internal(
                action_name, self.request.user, before=before,
                after={"code": partner.clinic_id, "name": partner.name, "is_active": partner.is_active},
                metadata={"service_partner_id": partner.id},
            )


class AssessmentServiceSessionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsInternalFinanceAdministrator]
    serializer_class = AssessmentServiceSessionSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        return AssessmentServiceSession.objects.select_related(
            "participating_organization", "service_branch", "service_partner",
            "created_by", "activated_by", "completed_by", "cancelled_by",
        ).annotate(linked_encounter_count=Count("encounters"))

    def perform_create(self, serializer):
        with transaction.atomic():
            session = serializer.save()
            _audit_internal(
                "service_session_created", self.request.user,
                after={"session_id": session.id, "session_reference": session.session_reference,
                       "status": session.status, "configuration_version": session.configuration_version},
            )

    def perform_update(self, serializer):
        with transaction.atomic():
            session = get_object_or_404(
                self.get_queryset().select_for_update(), pk=serializer.instance.pk
            )
            self.check_object_permissions(self.request, session)
            if session.status != AssessmentServiceSession.Status.DRAFT:
                raise DRFValidationError("Only draft sessions may be edited.")
            changed = any(
                getattr(session, field) != value
                for field, value in serializer.validated_data.items()
            )
            serializer.instance = session
            if not changed:
                return
            before = {"session_reference": session.session_reference, "configuration_version": session.configuration_version}
            session = serializer.save(configuration_version=session.configuration_version + 1)
            _audit_internal(
                "service_session_draft_edited", self.request.user, before=before,
                after={"session_reference": session.session_reference,
                       "configuration_version": session.configuration_version},
                metadata={"session_id": session.id},
            )

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        with transaction.atomic():
            session = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
            self.check_object_permissions(request, session)
            if session.status != AssessmentServiceSession.Status.DRAFT:
                return Response({"detail": "Only a draft session can be activated."}, status=status.HTTP_409_CONFLICT)
            session.status = AssessmentServiceSession.Status.ACTIVE
            session.activated_by = request.user
            session.activated_at = timezone.now()
            session.save(update_fields=["status", "activated_by", "activated_at", "updated_at"])
            _audit_internal("service_session_activated", request.user,
                            metadata={"session_id": session.id, "session_reference": session.session_reference,
                                      "configuration_version": session.configuration_version})
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        with transaction.atomic():
            session = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
            self.check_object_permissions(request, session)
            if session.status != AssessmentServiceSession.Status.ACTIVE:
                return Response({"detail": "Only an active session can be completed."}, status=status.HTTP_409_CONFLICT)
            session.status = AssessmentServiceSession.Status.COMPLETED
            session.completed_by = request.user
            session.completed_at = timezone.now()
            session.save(update_fields=["status", "completed_by", "completed_at", "updated_at"])
            _audit_internal("service_session_completed", request.user,
                            metadata={"session_id": session.id, "session_reference": session.session_reference,
                                      "configuration_version": session.configuration_version})
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        reason = str(request.data.get("reason") or "").strip()
        if not reason:
            return Response({"reason": ["A cancellation reason is required."]}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            session = get_object_or_404(self.get_queryset().select_for_update(), pk=pk)
            self.check_object_permissions(request, session)
            if session.status not in {AssessmentServiceSession.Status.DRAFT, AssessmentServiceSession.Status.ACTIVE}:
                return Response({"detail": "Only a draft or active session can be cancelled."}, status=status.HTTP_409_CONFLICT)
            session.status = AssessmentServiceSession.Status.CANCELLED
            session.cancelled_by = request.user
            session.cancelled_at = timezone.now()
            session.cancellation_reason = reason
            session.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancellation_reason", "updated_at"])
            _audit_internal("service_session_cancelled", request.user,
                            metadata={"session_id": session.id, "session_reference": session.session_reference,
                                      "reason": reason, "configuration_version": session.configuration_version})
        return Response(self.get_serializer(session).data)

class FinanceAdminViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsInternalFinanceAdministrator]

    def get_permissions(self):
        role = IsFinanceViewer if self.action in {"list", "retrieve"} else IsInternalFinanceAdministrator
        return [IsAuthenticated(), role()]


class PartnerContractViewSet(FinanceAdminViewSet):
    queryset = PartnerContract.objects.select_related("organization").prefetch_related(
        "pricing_rules"
    ).all()
    serializer_class = PartnerContractSerializer

    def destroy(self, request, *args, **kwargs):
        contract = self.get_object()
        if contract.status != PartnerContract.Status.DRAFT:
            return Response(
                {"detail": "Only unused draft contracts may be deleted. Suspend or end this contract instead."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if contract.financial_records.exists() or contract.pricing_rules.filter(
            financial_records__isnull=False
        ).exists():
            return Response(
                {"detail": "This contract has financial history and cannot be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        contract.pricing_rules.all().delete()
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def suspend(self, request, pk=None):
        contract = self.get_object()
        contract.status = PartnerContract.Status.SUSPENDED
        contract.pricing_rules.update(is_active=False)
        contract.save(update_fields=["status", "updated_at"])
        return Response(self.get_serializer(contract).data)

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        contract = self.get_object()
        end_date = parse_date(request.data.get("effective_to", "")) or timezone.localdate()
        if end_date < contract.effective_from:
            return Response(
                {"detail": "End date cannot precede the contract start date."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        contract.effective_to = end_date
        contract.status = PartnerContract.Status.EXPIRED
        contract.pricing_rules.update(is_active=False, effective_to=end_date)
        contract.save(update_fields=["effective_to", "status", "updated_at"])
        return Response(self.get_serializer(contract).data)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def replace(self, request, pk=None):
        current = self.get_object()
        start_date = parse_date(request.data.get("effective_from", ""))
        if not start_date:
            return Response(
                {"detail": "A valid effective_from date is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if start_date <= current.effective_from:
            return Response(
                {"detail": "The replacement must start after the current contract."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        current.effective_to = start_date - timedelta(days=1)
        current.status = PartnerContract.Status.EXPIRED
        current.pricing_rules.update(is_active=False, effective_to=current.effective_to)
        current.save(update_fields=["effective_to", "status", "updated_at"])

        payload = request.data.copy()
        payload["organization"] = current.organization_id
        payload["programme"] = current.programme
        payload["status"] = PartnerContract.Status.ACTIVE
        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)
        replacement = serializer.save()
        return Response(self.get_serializer(replacement).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="revise-pricing")
    @transaction.atomic
    def revise_pricing(self, request, pk=None):
        contract = self.get_object()
        incoming_rules = request.data.get("pricing_rules", [])
        if not isinstance(incoming_rules, list) or not incoming_rules:
            return Response(
                {"detail": "At least one pricing rule is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        allowed_fields = {
            "name", "service_type", "source_type", "workflow_route",
            "payment_responsibility", "equipment_owner_type",
            "min_monthly_volume", "max_monthly_volume", "gross_amount",
            "priority", "effective_from", "effective_to", "notes",
        }
        for rule_data in incoming_rules:
            service_type = rule_data.get("service_type")
            current = contract.pricing_rules.filter(
                service_type=service_type, is_active=True
            ).order_by("-version", "-effective_from", "-id").first()
            payload = {key: value for key, value in rule_data.items() if key in allowed_fields}
            payload.update({"contract": contract.id, "is_active": True})
            if current and current.financial_records.exists():
                proposed_start = parse_date(str(payload.get("effective_from", "")))
                if not proposed_start or proposed_start <= current.effective_from:
                    return Response(
                        {
                            "detail": (
                                f"New {service_type} pricing must start after "
                                f"{current.effective_from}."
                            )
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                payload["version"] = current.version + 1
                payload["supersedes"] = current.id
                serializer = PricingRuleSerializer(data=payload)
                serializer.is_valid(raise_exception=True)
                new_rule = serializer.save()
                current.is_active = False
                current.effective_to = new_rule.effective_from - timedelta(days=1)
                current.save(update_fields=["is_active", "effective_to", "updated_at"])
            elif current:
                serializer = PricingRuleSerializer(current, data=payload, partial=True)
                serializer.is_valid(raise_exception=True)
                serializer.save()
            else:
                payload.setdefault("version", 1)
                serializer = PricingRuleSerializer(data=payload)
                serializer.is_valid(raise_exception=True)
                serializer.save()
        contract.refresh_from_db()
        return Response(self.get_serializer(contract).data)

    def get_permissions(self):
        role = IsFinanceViewer if self.action in {"list", "retrieve"} else IsInternalFinanceAdministrator
        return [IsAuthenticated(), role()]


class PricingRuleViewSet(FinanceAdminViewSet):
    queryset = PricingRule.objects.select_related("contract", "contract__organization").all()
    serializer_class = PricingRuleSerializer

    def get_permissions(self):
        role = IsFinanceViewer if self.action in {"list", "retrieve"} else IsInternalFinanceAdministrator
        return [IsAuthenticated(), role()]


class AllocationRuleViewSet(FinanceAdminViewSet):
    queryset = AllocationRule.objects.select_related(
        "pricing_rule", "beneficiary_organization"
    ).all()
    serializer_class = AllocationRuleSerializer


class BillingProfileViewSet(viewsets.ModelViewSet):
    """Singleton-style billing configuration controlled by Finance Admin."""

    serializer_class = BillingProfileSerializer
    queryset = BillingProfile.objects.all()
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        role = IsFinanceViewer if self.action in {"list", "retrieve"} else IsInternalFinanceAdministrator
        return [IsAuthenticated(), role()]

    def perform_create(self, serializer):
        serializer.save(updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class ServiceAllowanceViewSet(FinanceAdminViewSet):
    serializer_class = ServiceAllowanceSerializer
    queryset = ServiceAllowance.objects.select_related(
        "organization", "contract", "approved_by"
    ).all()

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            role = IsFinanceViewer
        elif self.action == "approve":
            role = IsInternalFinanceApprover
        else:
            role = IsInternalFinanceAdministrator
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
        role = IsFinanceViewer if self.action in {"list", "retrieve"} else IsInternalFinanceOperator
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
            role = IsInternalFinanceOperator
        else:
            role = IsInternalFinanceAdministrator
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
        role = IsFinanceViewer if self.action in {"list", "retrieve"} else IsInternalFinanceOperator
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
        if _internal_evidence_role(self.request.user):
            return queryset
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
        profile = BillingProfile.objects.filter(is_active=True, currency=wallet.currency).first()
        if not profile or not profile.is_complete:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Sentinel bank-transfer details have not been configured for this currency.")
        billing_snapshot = {
            "legal_entity_name": profile.legal_entity_name,
            "trading_name": profile.trading_name,
            "registered_address": profile.registered_address,
            "company_registration_number": profile.company_registration_number,
            "tax_identification_number": profile.tax_identification_number,
            "finance_email": profile.finance_email,
            "finance_phone": profile.finance_phone,
            "bank_name": profile.bank_name,
            "bank_account_name": profile.bank_account_name,
            "bank_account_number": profile.bank_account_number,
            "bank_branch_code": profile.bank_branch_code,
            "currency": profile.currency,
            "transfer_instructions": profile.transfer_instructions,
            "funding_request_prefix": profile.funding_request_prefix,
            "receipt_prefix": profile.receipt_prefix,
        }
        organization = wallet.organization
        customer_snapshot = {
            "organization_id": organization.id,
            "name": organization.name,
            "address": organization.address,
            "email": organization.contact_email,
            "phone": organization.phone,
        }
        request_reference = f"{profile.funding_request_prefix}-{uuid.uuid4().hex[:12].upper()}"
        instance = serializer.save(
            requester=self.request.user, currency=wallet.currency,
            request_reference=request_reference, billing_snapshot=billing_snapshot,
            customer_snapshot=customer_snapshot,
            expires_at=timezone.now() + timedelta(days=7),
        )
        instance.full_clean()
        instance.save()

    @action(detail=True, methods=["get"], url_path="funding-request-pdf")
    def funding_request_pdf(self, request, pk=None):
        funding_request = self.get_object()
        try:
            document = render_bank_transfer_document(funding_request)
        except UnreliableDocumentSnapshot as exc:
            return Response(
                {"detail": str(exc)},
                status=status.HTTP_409_CONFLICT,
            )
        response = HttpResponse(
            document, content_type="application/pdf"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{funding_request.request_reference}.pdf"'
        )
        return response

    @action(detail=True, methods=["get"], url_path="receipt-pdf")
    def receipt_pdf(self, request, pk=None):
        funding_request = self.get_object()
        if funding_request.status != BankTransferFundingRequest.Status.CREDITED:
            return Response(
                {"detail": "A receipt is available only after the transfer is approved and credited."},
                status=status.HTTP_409_CONFLICT,
            )
        try:
            document = render_bank_transfer_document(funding_request, receipt=True)
        except UnreliableDocumentSnapshot as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        response = HttpResponse(document, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="{funding_request.receipt_reference}.pdf"'
        )
        return response

    @action(detail=True, methods=["get"], url_path="proof-download")
    def proof_download(self, request, pk=None):
        funding_request = self.get_object()
        organization = get_user_organization(request.user)
        owns_request = bool(
            organization and funding_request.wallet.organization_id == organization.id
        )
        if not owns_request and not _internal_evidence_role(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to download this evidence.")
        return _evidence_response(funding_request.proof)

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
        self._require_finance_role(IsInternalFinanceOperator)
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
        self._require_finance_role(IsInternalFinanceApprover)
        try:
            funding_request = approve_bank_transfer(self.get_object(), actor=request.user)
        except (DjangoValidationError, ValueError, TypeError) as exc:
            message = exc.messages if hasattr(exc, "messages") else [str(exc)]
            return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(self.get_serializer(funding_request).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        self._require_finance_role(IsInternalFinanceApprover)
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
        elif self.action == "evidence_download":
            return [IsAuthenticated()]
        elif self.action == "approve":
            role = IsInternalFinanceApprover
        else:
            role = IsInternalFinanceOperator
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

    @action(detail=True, methods=["get"], url_path="evidence-download")
    def evidence_download(self, request, pk=None):
        if not _internal_evidence_role(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to download this evidence.")
        return _evidence_response(self.get_object().payment_evidence)


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
        organizations = Organization.objects.filter(
            is_active=True, organization_type__in={"clinic", "hospital"}
        ).prefetch_related("branches").order_by("name")
        return Response([
            {
                "id": organization.id,
                "name": organization.name,
                "organization_type": organization.organization_type,
                "clinic_id": organization.clinic_id,
                "branches": [
                    {"id": branch.id, "name": branch.name}
                    for branch in organization.branches.all() if branch.is_active
                ],
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
            role = IsInternalFinanceOperator
        elif self.action in {"approve", "reject"}:
            role = IsInternalFinanceApprover
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

    @action(detail=True, methods=["get"], url_path="evidence-download")
    def evidence_download(self, request, pk=None):
        if not _internal_evidence_role(request.user):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("You do not have permission to download this evidence.")
        return _evidence_response(self.get_object().evidence)


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
            "can_operate": allowed(IsInternalFinanceOperator),
            "can_verify": allowed(IsInternalFinanceOperator),
            "can_approve": allowed(IsInternalFinanceApprover),
            "can_administer": allowed(IsInternalFinanceAdministrator),
            "can_request_corrections": allowed(IsInternalFinanceOperator),
            "can_decide_corrections": allowed(IsInternalFinanceApprover),
            "can_prepare_settlements": allowed(IsInternalFinanceOperator),
            "can_approve_settlements": allowed(IsInternalFinanceApprover),
            "can_mark_settlements_paid": allowed(IsInternalFinanceOperator),
            "can_configure_pricing": allowed(IsInternalFinanceAdministrator),
            "internal_finance": {
                "can_administer": allowed(IsInternalFinanceAdministrator),
                "can_operate": allowed(IsInternalFinanceOperator),
                "can_approve": allowed(IsInternalFinanceApprover),
                "can_manage_service_partners": allowed(IsInternalFinanceAdministrator),
                "can_manage_service_sessions": allowed(IsInternalFinanceAdministrator),
            },
        })
