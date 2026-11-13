from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["title", "recipient", "kind", "created_at", "read_at"]
    list_filter = ["kind"]
    search_fields = ["title", "recipient__name", "recipient__email"]
    raw_id_fields = ["recipient"]
    readonly_fields = ["created_at"]
