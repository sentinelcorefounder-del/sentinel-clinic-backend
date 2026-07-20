from django.contrib import admin

from .models import PaymentTransaction, PaymentWebhookEvent


@admin.register(PaymentTransaction)
class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = ("reference", "purpose", "status", "expected_amount", "received_amount", "currency", "created_at")
    list_filter = ("purpose", "status", "currency", "created_at")
    search_fields = ("reference", "email", "wallet__organization__name", "financial_record__encounter__encounter_id")
    readonly_fields = ("provider_payload", "initialized_at", "verified_at", "posted_at", "created_at", "updated_at")


@admin.register(PaymentWebhookEvent)
class PaymentWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_name", "reference", "processed", "received_at", "processed_at")
    list_filter = ("event_name", "processed", "received_at")
    search_fields = ("event_key", "reference", "processing_error")
    readonly_fields = tuple(field.name for field in PaymentWebhookEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
