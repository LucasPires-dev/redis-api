from django.contrib import admin

from .models import Notification, NotificationPreference


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "recipient", "kind", "created_at", "read_at", "emailed_at"]
    list_filter = ["kind"]
    search_fields = ["title", "recipient__name", "recipient__email"]
    raw_id_fields = ["recipient"]
    readonly_fields = ["created_at"]


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ["person", "email_off", "updated_at"]
    search_fields = ["person__name", "person__email"]
    raw_id_fields = ["person"]
