import uuid

import django_filters
from django.db.models import Q

from .models import Cell, CellMeeting, Season


class CellFilter(django_filters.FilterSet):
    """Filters for /api/cells/.

    ?season=<uuid> or ?season=current (the season running today; cells created
    before seasons existed are included with "current" so they don't vanish).
    ?active=true keeps only cells running today (see CellQuerySet.active).
    ?mine=true keeps the cells the caller leads, co-leads or belongs to — the
    personal "Minhas células" tab, even for a coordinator who sees many more.
    """

    season = django_filters.CharFilter(method="filter_season")
    active = django_filters.BooleanFilter(method="filter_active")
    mine = django_filters.BooleanFilter(method="filter_mine")

    class Meta:
        model = Cell
        fields = {
            "campus": ["exact"],
            "meeting_day": ["exact", "in"],
            "meeting_time": ["gte", "lte"],
            "target_audience": ["exact"],
        }

    def filter_season(self, queryset, name, value):
        if value == "current":
            current = Season.objects.current()
            return queryset.filter(Q(season__in=current) | Q(season__isnull=True))
        try:
            return queryset.filter(season_id=uuid.UUID(value))
        except ValueError:
            return queryset.none()

    def filter_active(self, queryset, name, value):
        if value is None:
            return queryset
        active = queryset.active()
        return active if value else queryset.exclude(pk__in=active.values("pk"))

    def filter_mine(self, queryset, name, value):
        if not value:
            return queryset
        person = self.request.user
        return queryset.filter(Q(leaderships__person=person) | Q(members=person)).distinct()


class CellMeetingFilter(django_filters.FilterSet):
    """?cell=<uuid>, ?date__gte=2026-08-01, ?date__lte=2026-12-31"""

    class Meta:
        model = CellMeeting
        fields = {"cell": ["exact"], "date": ["gte", "lte"]}
