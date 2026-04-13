from django.contrib import admin
from .models import PricingRule, PayoutLedger

admin.site.register(PricingRule)
admin.site.register(PayoutLedger)