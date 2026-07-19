from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from .models import AllocationRule, EncounterFinancialRecord, PartnerContract, PricingRule
from .serializers import (
    AllocationRuleSerializer,
    EncounterFinancialRecordSerializer,
    PartnerContractSerializer,
    PricingRuleSerializer,
)
from .services import price_encounter


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
