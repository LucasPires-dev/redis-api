from django.contrib import admin

from .models import EventRegistration, EventTicketConfig


@admin.register(EventTicketConfig)
class EventTicketConfigAdmin(admin.ModelAdmin):
    list_display = ["event", "price_cents", "max_capacity", "registration_deadline"]
    list_filter = ["created_at"]
    autocomplete_fields = ["event"]


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ["person", "event", "payment_status", "paid_at", "created_at"]
    list_filter = ["payment_status", "created_at"]
    autocomplete_fields = ["person", "event"]
    readonly_fields = ["asaas_payment_id", "payment_url"]
