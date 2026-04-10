from django.urls import path
from .views import initialize_paystack_payment, paystack_webhook

urlpatterns = [
    path("initialize/", initialize_paystack_payment, name="initialize_paystack_payment"),
    path("webhook/", paystack_webhook, name="paystack_webhook"),
]