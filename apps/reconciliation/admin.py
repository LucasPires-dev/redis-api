from django.contrib import admin

from .models import Reconciliation, ReconciliationLead


@admin.register(ReconciliationLead)
class ReconciliationLeadAdmin(admin.ModelAdmin):
    list_display = ["name", "phone", "email", "raw_campus_label", "campus", "status", "submitted_at"]
    list_filter = ["status", "campus"]
    search_fields = ["name", "phone", "email", "raw_campus_label"]


@admin.register(Reconciliation)
class ReconciliationAdmin(admin.ModelAdmin):
    list_display = ["convert", "reconciliador", "campus", "jornada_completed", "assigned_at"]
    list_filter = ["campus", "jornada_completed"]
    autocomplete_fields = ["convert", "reconciliador", "campus", "assigned_by", "source_lead"]
