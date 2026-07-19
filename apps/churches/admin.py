from django.contrib import admin

from .models import (
    Activity,
    ActivityCompletion,
    Campus,
    Church,
    Ministry,
    MinistryGeneralLeadership,
    MinistryLeadership,
    Team,
    TeamLeadership,
    TeamPointAward,
)


@admin.register(Church)
class ChurchAdmin(admin.ModelAdmin):
    list_display = ["name", "abbreviation"]
    search_fields = ["name", "abbreviation"]


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    list_display = ["name", "church", "city"]
    list_filter = ["church"]
    search_fields = ["name", "city"]


class MinistryLeadershipInline(admin.TabularInline):
    model = MinistryLeadership
    extra = 1
    autocomplete_fields = ["person"]


@admin.register(Ministry)
class MinistryAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "campus", "min_age", "max_age"]
    list_filter = ["kind", "campus"]
    search_fields = ["name", "description"]
    filter_horizontal = ["members"]
    inlines = [MinistryLeadershipInline]


@admin.register(MinistryLeadership)
class MinistryLeadershipAdmin(admin.ModelAdmin):
    list_display = ["person", "ministry", "role"]
    list_filter = ["role", "ministry"]
    autocomplete_fields = ["person", "ministry"]


@admin.register(MinistryGeneralLeadership)
class MinistryGeneralLeadershipAdmin(admin.ModelAdmin):
    list_display = ["kind", "leaders_display"]
    filter_horizontal = ["leaders"]

    @admin.display(description="Líderes")
    def leaders_display(self, obj):
        return ", ".join(p.name for p in obj.leaders.all())


class TeamLeadershipInline(admin.TabularInline):
    model = TeamLeadership
    extra = 1
    autocomplete_fields = ["person"]


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "ministry"]
    list_filter = ["ministry"]
    search_fields = ["name", "description"]
    filter_horizontal = ["members"]
    inlines = [TeamLeadershipInline]


@admin.register(TeamLeadership)
class TeamLeadershipAdmin(admin.ModelAdmin):
    list_display = ["person", "team", "role"]
    list_filter = ["role", "team"]
    autocomplete_fields = ["person", "team"]


@admin.register(TeamPointAward)
class TeamPointAwardAdmin(admin.ModelAdmin):
    list_display = ["team", "points", "reason", "awarded_by", "awarded_at"]
    list_filter = ["team"]
    autocomplete_fields = ["team", "awarded_by"]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ["title", "team", "kind", "points", "is_active"]
    list_filter = ["kind", "is_active", "team"]
    search_fields = ["title", "description"]
    autocomplete_fields = ["team", "created_by"]


@admin.register(ActivityCompletion)
class ActivityCompletionAdmin(admin.ModelAdmin):
    list_display = ["person", "activity", "date", "points_awarded"]
    list_filter = ["activity"]
    autocomplete_fields = ["person", "activity"]
