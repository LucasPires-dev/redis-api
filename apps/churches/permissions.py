from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import (
    Campus,
    CampusCellsLeadership,
    Cell,
    CellGeneralLeadership,
    Ministry,
    MinistryGeneralLeadership,
    Team,
)


def led_ministries(person):
    """Ministries where person is leader OR co-leader — same access level for both."""
    if not person.is_authenticated:
        return Ministry.objects.none()
    return Ministry.objects.filter(leaderships__person=person).distinct()


def led_campuses(person):
    """Campuses where person is leader OR co-leader — same access level for both."""
    if not person.is_authenticated:
        return Campus.objects.none()
    return Campus.objects.filter(leaderships__person=person).distinct()


def general_led_kinds(person):
    if not person.is_authenticated:
        return set()
    return set(
        MinistryGeneralLeadership.objects.filter(leaders=person).values_list("kind", flat=True)
    )


def can_manage_campus(person, campus) -> bool:
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    return campus.leaderships.filter(person=person).exists()


def can_manage_ministry(person, ministry) -> bool:
    if not person.is_authenticated:
        return False
    if person.is_manager:
        return True
    if ministry.kind in general_led_kinds(person):
        return True
    if ministry.leaderships.filter(person=person).exists():
        return True
    return can_manage_campus(person, ministry.campus)


def can_appoint_ministry_leaders(person, ministry) -> bool:
    """Who appoints a local ministry's leaders: the levels above it — the manager,
    the campus leader of the ministry's campus, or the general leader of that
    ministry kind (who acts on every campus). Not the ministry's own leaders:
    nobody promotes/demotes peers at their own level."""
    if not person.is_authenticated:
        return False
    if can_manage_campus(person, ministry.campus):
        return True
    return ministry.kind in general_led_kinds(person)


def scope_ministries(qs, person):
    """Ministries a person may see: everything for a manager, otherwise the ones
    they lead/co-lead locally plus every ministry of a kind they lead generally,
    plus every ministry at a campus they lead, plus every ministry they're simply
    a member of (read access) — same precedent as scope_cells's Q(members=person)."""
    if person.is_manager:
        return qs
    kinds = general_led_kinds(person)
    condition = (
        Q(leaderships__person=person) | Q(campus__leaderships__person=person) | Q(members=person)
    )
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


def is_cells_general_leader(person) -> bool:
    """General cells leader — responsável geral pelas células (CellGeneralLeadership)."""
    if not person.is_authenticated:
        return False
    return CellGeneralLeadership.objects.filter(leaders=person).exists()


def can_coordinate_cells(person) -> bool:
    """Church-wide cell powers: define seasons, create cells, assign cell
    leaders, renew cells into a new season. Manager or general cells leader."""
    if not person.is_authenticated:
        return False
    return person.is_manager or is_cells_general_leader(person)


def led_campuses_cells(person):
    """Campuses whose cells the person is in charge of (CampusCellsLeadership)."""
    if not person.is_authenticated:
        return Campus.objects.none()
    return Campus.objects.filter(cells_leaderships__person=person).distinct()


def can_coordinate_campus_cells(person, campus) -> bool:
    """Cell powers within one campus: create cells there, assign their leaders,
    renew them. Church-wide coordinators, the campus leader, or the campus's
    cells leader."""
    if not person.is_authenticated:
        return False
    if can_coordinate_cells(person) or can_manage_campus(person, campus):
        return True
    return CampusCellsLeadership.objects.filter(campus=campus, person=person).exists()


def can_assign_campus_cells_leader(person, campus) -> bool:
    """Who defines the campus's cells leader: the campus leader, or the
    manager / general cells leader."""
    if not person.is_authenticated:
        return False
    return can_coordinate_cells(person) or can_manage_campus(person, campus)


def can_manage_cell(person, cell) -> bool:
    if not person.is_authenticated:
        return False
    if cell.leaderships.filter(person=person).exists():
        return True
    return can_coordinate_campus_cells(person, cell.campus)


def scope_cells(qs, person):
    """Cells a person may see: everything for a manager or the general cells
    leader; otherwise the ones they lead/co-lead plus the ones they're simply a
    member of (read access), plus every célula at a campus they lead or whose
    cells they're in charge of."""
    if can_coordinate_cells(person):
        return qs
    return qs.filter(
        Q(leaderships__person=person)
        | Q(members=person)
        | Q(campus__leaderships__person=person)
        | Q(campus__cells_leaderships__person=person)
    ).distinct()


class CellPermission(BasePermission):
    """Read: anyone who can see the célula (queryset already scoped).
    Create célula / assign leadership / renew: can_coordinate_campus_cells for the
    célula's campus (checked in the view, since on create the campus comes in
    the payload).
    Update célula fields, manage members and meetings: those, or a leader/
    co-leader of the célula (can_manage_cell)."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        # Create is checked in CellViewSet.perform_create (the campus is in the
        # payload). Custom actions are gated by has_object_permission
        # (can_manage_cell) and, for leaderships/renew, by an explicit
        # can_coordinate_campus_cells check in the view.
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_cell(request.user, obj)


class SeasonPermission(BasePermission):
    """Read: anyone authenticated (seasons are church-wide public info).
    Create/update/delete: manager or general cells leader."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return can_coordinate_cells(request.user)


def scope_cell_meetings(qs, person):
    """Meetings of the cells the person can see (same rules as scope_cells)."""
    return qs.filter(cell__in=scope_cells(Cell.objects.all(), person))


class CellMeetingPermission(BasePermission):
    """Read: anyone who can see the meeting's cell (queryset already scoped).
    Create/update/delete: whoever can manage the cell (can_manage_cell). For
    create, the cell comes in the payload, so the view checks it."""

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_cell(request.user, obj.cell)


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
    their ministry) lead, plus teams they're simply a member of (read access), plus
    teams whose ministry is at a campus they lead."""
    if person.is_manager:
        return qs
    kinds = general_led_kinds(person)
    condition = (
        Q(leaderships__person=person)
        | Q(members=person)
        | Q(ministry__leaderships__person=person)
        | Q(ministry__campus__leaderships__person=person)
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
        | Q(team__ministry__campus__leaderships__person=person)
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
