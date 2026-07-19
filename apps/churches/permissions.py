from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Activity, Ministry, MinistryGeneralLeadership, Team


def led_ministries(person):
    """Ministries where person is leader OR co-leader — same access level for both."""
    if not person.is_authenticated:
        return Ministry.objects.none()
    return Ministry.objects.filter(leaderships__person=person).distinct()


def general_led_kinds(person):
    if not person.is_authenticated:
        return set()
    return set(
        MinistryGeneralLeadership.objects.filter(leader=person).values_list("kind", flat=True)
    )


def can_manage_ministry(person, ministry) -> bool:
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    if ministry.kind in general_led_kinds(person):
        return True
    return ministry.leaderships.filter(person=person).exists()


def scope_ministries(qs, person):
    """Ministries a person may see: everything for a manager, otherwise the ones
    they lead/co-lead locally plus every ministry of a kind they lead generally."""
    if person.is_manager:
        return qs
    kinds = general_led_kinds(person)
    condition = Q(leaderships__person=person)
    if kinds:
        condition |= Q(kind__in=kinds)
    return qs.filter(condition).distinct()


class IsManager(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_manager)


class MinistryPermission(BasePermission):
    """Read: anyone authenticated (queryset is already scoped in the view).
    Create ministries / assign leadership: manager only.
    Update ministry fields or manage its members: manager, or a leader/co-leader of it."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        # Only the plain "create a new Ministry" action is manager-only here — the
        # custom "members"/"leaderships" actions also arrive as POST/DELETE but are
        # gated by has_object_permission (can_manage_ministry) instead.
        if view.action == "create":
            return request.user.is_manager
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_ministry(request.user, obj)


def can_manage_team(person, team) -> bool:
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    if can_manage_ministry(person, team.ministry):
        return True
    return team.leaderships.filter(person=person).exists()


def scope_teams(qs, person):
    """Teams a person may see: everything for a manager; otherwise teams they (or
    their ministry) lead, plus teams they're simply a member of (read access)."""
    if person.is_manager:
        return qs
    kinds = general_led_kinds(person)
    condition = (
        Q(leaderships__person=person)
        | Q(members=person)
        | Q(ministry__leaderships__person=person)
    )
    if kinds:
        condition |= Q(ministry__kind__in=kinds)
    return qs.filter(condition).distinct()


def scope_activities(qs, person):
    """Same scoping as scope_teams, one hop further through team->activities."""
    if person.is_manager:
        return qs
    kinds = general_led_kinds(person)
    condition = (
        Q(team__leaderships__person=person)
        | Q(team__members=person)
        | Q(team__ministry__leaderships__person=person)
    )
    if kinds:
        condition |= Q(team__ministry__kind__in=kinds)
    return qs.filter(condition).distinct()


class TeamPermission(BasePermission):
    """Read: anyone who can see the team (queryset already scoped).
    Create: manager, or leader/co-leader of the target ministry (ministry_id in body).
    Update/manage leadership/members/point-awards: manager, ministry leader/co-leader,
    or the team's own leader/co-leader (can_manage_team)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            ministry_id = request.data.get("ministry_id")
            ministry = Ministry.objects.filter(pk=ministry_id).first() if ministry_id else None
            if ministry is None:
                return True  # let serializer validation produce a clean 400
            return can_manage_ministry(request.user, ministry)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_team(request.user, obj)


class ActivityPermission(BasePermission):
    """Read: anyone who can see the activity (queryset already scoped).
    Create: manager, ministry leader/co-leader, or the target team's leader/co-leader
    (team_id in body). Update/deactivate: can_manage_team on the activity's team."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            team_id = request.data.get("team_id")
            team = Team.objects.filter(pk=team_id).first() if team_id else None
            if team is None:
                return True  # let serializer validation produce a clean 400
            return can_manage_team(request.user, team)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_team(request.user, obj.team)
