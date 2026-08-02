from django.contrib import admin

from .models import Demand, Event, EventChecklistItem, EventRosterEntry, RundownItem, RundownItemStatus


class RundownItemInline(admin.TabularInline):
    model = RundownItem
    extra = 0


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ["title", "event_type", "campus", "ministry_kind", "starts_at", "status"]
    list_filter = ["event_type", "status", "campus", "ministry_kind"]
    search_fields = ["title"]
    inlines = [RundownItemInline]


@admin.register(RundownItem)
class RundownItemAdmin(admin.ModelAdmin):
    list_display = ["title", "event", "order", "ministry_kind", "scheduled_time"]
    list_filter = ["ministry_kind"]
    autocomplete_fields = ["event"]


@admin.register(RundownItemStatus)
class RundownItemStatusAdmin(admin.ModelAdmin):
    list_display = ["rundown_item", "ministry_kind", "status", "updated_at"]
    list_filter = ["ministry_kind", "status"]


@admin.register(Demand)
class DemandAdmin(admin.ModelAdmin):
    list_display = [
        "demand_type",
        "from_ministry_kind",
        "to_ministry_kind",
        "priority",
        "status",
        "due_date",
        "created_at",
    ]
    list_filter = ["status", "priority", "demand_type", "to_ministry_kind", "from_ministry_kind"]
    autocomplete_fields = ["event", "from_campus", "to_campus", "created_by"]


@admin.register(EventRosterEntry)
class EventRosterEntryAdmin(admin.ModelAdmin):
    list_display = ["person", "ministry", "event", "function", "status"]
    list_filter = ["status", "ministry"]
    autocomplete_fields = ["event", "ministry", "person", "invited_by"]


@admin.register(EventChecklistItem)
class EventChecklistItemAdmin(admin.ModelAdmin):
    list_display = ["text", "event", "ministry_kind", "is_done"]
    list_filter = ["ministry_kind", "is_done"]
    autocomplete_fields = ["event", "done_by", "created_by"]
