from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Max, Min, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.courses.models import CampusCoursesLeadership, CourseGeneralLeadership
from apps.courses.permissions import can_assign_campus_courses_leader
from apps.people.capabilities import cells_capabilities
from apps.production.models import EventProducerAssignment
from apps.production.permissions import can_assign_campus_event_producer
from apps.reconciliation.models import ReconciliationLead

from . import cell_health, cell_report, meeting_calendar
from .choices import ActivityKind, CellRole, MinistryKind, VisitorStatus
from .filters import CellFilter, CellMeetingFilter
from .models import (
    Activity,
    ActivityCompletion,
    Campus,
    CampusCellsLeadership,
    CampusLeadership,
    Cell,
    CellGeneralLeadership,
    CellLeadership,
    CellMeeting,
    CellSupervisionVisit,
    CellVisitor,
    Church,
    Ministry,
    MinistryGeneralLeadership,
    MinistryLeadership,
    Season,
    Team,
    TeamLeadership,
    TeamPointAward,
)
from .permissions import (
    ActivityPermission,
    CellMeetingPermission,
    CellPermission,
    CellSupervisionVisitPermission,
    CellVisitorPermission,
    MinistryPermission,
    SeasonPermission,
    TeamPermission,
    can_appoint_ministry_leaders,
    can_assign_campus_cells_leader,
    can_appoint_cell_trainee,
    can_supervise_cell,
    can_coordinate_campus_cells,
    can_manage_cell,
    can_manage_ministry,
    can_manage_team,
    general_led_kinds,
    scope_activities,
    scope_cell_meetings,
    scope_cells,
    scope_coordinated_cells,
    scope_managed_cells,
    scope_supervised_cells,
    scope_ministries,
    scope_teams,
    season_church_ids,
)
from .serializers import (
    ActivityCompletionInputSerializer,
    ActivityCompletionSerializer,
    ActivitySerializer,
    CampusCellsLeadershipSerializer,
    CampusCoursesLeadershipSerializer,
    CampusLeadershipInputSerializer,
    CampusLeadershipSerializer,
    CampusSerializer,
    CellLeadershipInputSerializer,
    CellLeadershipSerializer,
    CellMeetingSerializer,
    CellMemberSerializer,
    CellMultiplicationPlanInputSerializer,
    CellMultiplyInputSerializer,
    CellRenewInputSerializer,
    CellSerializer,
    CellSupervisionVisitSerializer,
    CellSupervisorInputSerializer,
    CellVisitorSerializer,
    ChurchSerializer,
    GeneralLeaderInputSerializer,
    MakeVisitorMemberInputSerializer,
    MinistryGeneralLeadershipSerializer,
    MinistryLeadershipInputSerializer,
    MinistryLeadershipSerializer,
    MinistryMemberSerializer,
    MinistrySerializer,
    PersonMiniSerializer,
    SeasonSerializer,
    TeamLeadershipInputSerializer,
    TeamLeadershipSerializer,
    TeamMemberSerializer,
    TeamPointAwardInputSerializer,
    TeamPointAwardSerializer,
    TeamSerializer,
)

Person = get_user_model()

# People pickers return at most this many matches.
PEOPLE_SEARCH_LIMIT = 20


def search_people(qs, request):
    """Filter `qs` by ?q= (name or email, at least 2 characters) for the people
    pickers. Returns PersonMini data only — these endpoints exist so a cell or
    campus coordinator can find people without broader read access to
    /api/people/, so they must not leak anything beyond name/email."""
    term = (request.query_params.get("q") or "").strip()
    if len(term) < 2:
        return Response([])
    qs = qs.filter(Q(name__icontains=term) | Q(email__icontains=term)).order_by("name")
    return Response(PersonMiniSerializer(qs[:PEOPLE_SEARCH_LIMIT], many=True).data)


def _require_manager(user, message):
    """General (church-wide) leaders are chosen by the institution's leaders only —
    the top of the access hierarchy."""
    if not user.is_manager:
        raise PermissionDenied(message)


def _general_leaders_response(general_leadership, status_code=status.HTTP_200_OK):
    leaders = general_leadership.leaders.all() if general_leadership else []
    return Response(PersonMiniSerializer(leaders, many=True).data, status=status_code)


def _add_general_leader(request, general_leadership):
    """Adds the payload's person to a general leadership (an M2M `leaders`)."""
    serializer = GeneralLeaderInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
    general_leadership.leaders.add(person)
    return _general_leaders_response(general_leadership, status.HTTP_201_CREATED)


def _remove_general_leader(general_leadership, person_id):
    if general_leadership is None or not general_leadership.leaders.filter(pk=person_id).exists():
        return Response(status=status.HTTP_404_NOT_FOUND)
    general_leadership.leaders.remove(person_id)
    return Response(status=status.HTTP_204_NO_CONTENT)


class ChurchViewSet(viewsets.ReadOnlyModelViewSet):
    """Creation happens via seed/admin for now. The church-wide (general) leaders
    of cells and courses are chosen here, by the manager only."""

    queryset = Church.objects.all()
    serializer_class = ChurchSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["get"], url_path="people-search")
    def people_search(self, request, pk=None):
        """?q= — anyone in the church, for the manager's general-leader pickers."""
        self.get_object()
        _require_manager(request.user, "Só o gestor da mega igreja pode buscar pessoas da igreja toda.")
        return search_people(Person.objects.all(), request)

    @action(detail=True, methods=["get", "post"], url_path="cells-general-leaders")
    def cells_general_leaders(self, request, pk=None):
        """General cells leaders (responsáveis gerais pelas células)."""
        church = self.get_object()
        general = CellGeneralLeadership.objects.filter(church=church).first()
        if request.method == "GET":
            return _general_leaders_response(general)
        _require_manager(request.user, "Só o gestor da mega igreja pode escolher o responsável geral pelas células.")
        general, _ = CellGeneralLeadership.objects.get_or_create(church=church)
        return _add_general_leader(request, general)

    @action(
        detail=True, methods=["delete"], url_path=r"cells-general-leaders/(?P<person_id>[0-9a-fA-F-]+)"
    )
    def remove_cells_general_leader(self, request, pk=None, person_id=None):
        church = self.get_object()
        _require_manager(request.user, "Só o gestor da mega igreja pode remover o responsável geral pelas células.")
        return _remove_general_leader(CellGeneralLeadership.objects.filter(church=church).first(), person_id)

    @action(detail=True, methods=["get", "post"], url_path="courses-general-leaders")
    def courses_general_leaders(self, request, pk=None):
        """General courses leaders (responsáveis gerais pelos cursos)."""
        church = self.get_object()
        general = CourseGeneralLeadership.objects.filter(church=church).first()
        if request.method == "GET":
            return _general_leaders_response(general)
        _require_manager(request.user, "Só o gestor da mega igreja pode escolher o responsável geral pelos cursos.")
        general, _ = CourseGeneralLeadership.objects.get_or_create(church=church)
        return _add_general_leader(request, general)

    @action(
        detail=True, methods=["delete"], url_path=r"courses-general-leaders/(?P<person_id>[0-9a-fA-F-]+)"
    )
    def remove_courses_general_leader(self, request, pk=None, person_id=None):
        church = self.get_object()
        _require_manager(request.user, "Só o gestor da mega igreja pode remover o responsável geral pelos cursos.")
        return _remove_general_leader(CourseGeneralLeadership.objects.filter(church=church).first(), person_id)

    @action(detail=True, methods=["get", "post"], url_path="event-general-producers")
    def event_general_producers(self, request, pk=None):
        """Church-wide event producers (EventProducerAssignment with campus=null):
        they produce on every campus and appoint each campus's producers."""
        self.get_object()
        general = Person.objects.filter(event_producer_assignments__campus__isnull=True).distinct()
        if request.method == "GET":
            return Response(PersonMiniSerializer(general, many=True).data)
        _require_manager(request.user, "Só o gestor da mega igreja pode escolher o produtor geral de eventos.")
        serializer = GeneralLeaderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        # get_or_create (not a DB constraint) keeps it unique: NULL campus values
        # don't collide in the (person, campus) unique constraint.
        EventProducerAssignment.objects.get_or_create(person=person, campus=None)
        return Response(PersonMiniSerializer(general, many=True).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True, methods=["delete"], url_path=r"event-general-producers/(?P<person_id>[0-9a-fA-F-]+)"
    )
    def remove_event_general_producer(self, request, pk=None, person_id=None):
        self.get_object()
        _require_manager(request.user, "Só o gestor da mega igreja pode remover o produtor geral de eventos.")
        deleted, _ = EventProducerAssignment.objects.filter(campus__isnull=True, person_id=person_id).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CampusViewSet(viewsets.ReadOnlyModelViewSet):
    """Creation/editing of a Campus itself stays admin/seed-only (same precedent as
    Church) — only its leadership is manageable through this API."""

    queryset = Campus.objects.select_related("church").prefetch_related("leaderships__person")
    serializer_class = CampusSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=["get", "post"], url_path="leaderships")
    def leaderships(self, request, pk=None):
        campus = self.get_object()
        if request.method == "GET":
            qs = campus.leaderships.select_related("person")
            return Response(CampusLeadershipSerializer(qs, many=True).data)

        # Campus leadership is appointed by the level above (the institution/manager):
        # a campus leader cannot promote/demote themselves or other campus leaders.
        if not request.user.is_manager:
            raise PermissionDenied("Só o gestor da mega igreja pode atribuir liderança de campus.")
        serializer = CampusLeadershipInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        leadership, _ = CampusLeadership.objects.update_or_create(
            campus=campus,
            person=person,
            defaults={"role": serializer.validated_data["role"]},
        )
        return Response(
            CampusLeadershipSerializer(leadership).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["delete"], url_path=r"leaderships/(?P<person_id>[0-9a-fA-F-]+)")
    def remove_leadership(self, request, pk=None, person_id=None):
        campus = self.get_object()
        if not request.user.is_manager:
            raise PermissionDenied("Só o gestor da mega igreja pode remover liderança de campus.")
        deleted, _ = CampusLeadership.objects.filter(campus=campus, person_id=person_id).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="cells-leaderships")
    def cells_leaderships(self, request, pk=None):
        """The campus's cells leaders (responsáveis pelas células). Defined by the campus leader
        (or the manager / general cells leader)."""
        campus = self.get_object()
        if request.method == "GET":
            qs = campus.cells_leaderships.select_related("person")
            return Response(CampusCellsLeadershipSerializer(qs, many=True).data)

        if not can_assign_campus_cells_leader(request.user, campus):
            raise PermissionDenied("Só o líder do campus pode definir o responsável pelas células.")
        serializer = CampusLeadershipInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        if person.campus_id != campus.pk:
            raise ValidationError(
                {"person_id": "O responsável pelas células precisa ser do próprio campus."}
            )
        leadership, _ = CampusCellsLeadership.objects.update_or_create(
            campus=campus, person=person, defaults={"role": serializer.validated_data["role"]}
        )
        return Response(
            CampusCellsLeadershipSerializer(leadership).data, status=status.HTTP_201_CREATED
        )

    @action(
        detail=True, methods=["delete"], url_path=r"cells-leaderships/(?P<person_id>[0-9a-fA-F-]+)"
    )
    def remove_cells_leadership(self, request, pk=None, person_id=None):
        campus = self.get_object()
        if not can_assign_campus_cells_leader(request.user, campus):
            raise PermissionDenied("Só o líder do campus pode remover o responsável pelas células.")
        deleted, _ = CampusCellsLeadership.objects.filter(campus=campus, person_id=person_id).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="courses-leaderships")
    def courses_leaderships(self, request, pk=None):
        """The campus's courses leaders (responsáveis pelos cursos). Appointed by the
        level above: the campus leader, the manager or the general courses leader."""
        campus = self.get_object()
        if request.method == "GET":
            qs = campus.courses_leaderships.select_related("person")
            return Response(CampusCoursesLeadershipSerializer(qs, many=True).data)

        if not can_assign_campus_courses_leader(request.user, campus):
            raise PermissionDenied("Só o líder do campus pode definir o responsável pelos cursos.")
        serializer = CampusLeadershipInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        if person.campus_id != campus.pk:
            raise ValidationError(
                {"person_id": "O responsável pelos cursos precisa ser do próprio campus."}
            )
        leadership, _ = CampusCoursesLeadership.objects.update_or_create(
            campus=campus, person=person, defaults={"role": serializer.validated_data["role"]}
        )
        return Response(
            CampusCoursesLeadershipSerializer(leadership).data, status=status.HTTP_201_CREATED
        )

    @action(
        detail=True, methods=["delete"], url_path=r"courses-leaderships/(?P<person_id>[0-9a-fA-F-]+)"
    )
    def remove_courses_leadership(self, request, pk=None, person_id=None):
        campus = self.get_object()
        if not can_assign_campus_courses_leader(request.user, campus):
            raise PermissionDenied("Só o líder do campus pode remover o responsável pelos cursos.")
        deleted, _ = CampusCoursesLeadership.objects.filter(campus=campus, person_id=person_id).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="event-producers")
    def event_producers(self, request, pk=None):
        """The campus's event producers (produtores de eventos do campus). Appointed by
        the level above: the campus leader, the manager or a general producer."""
        campus = self.get_object()
        if request.method == "GET":
            people = Person.objects.filter(event_producer_assignments__campus=campus).distinct()
            return Response(PersonMiniSerializer(people, many=True).data)

        if not can_assign_campus_event_producer(request.user, campus):
            raise PermissionDenied("Só o líder do campus pode definir os produtores de eventos.")
        serializer = GeneralLeaderInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        if person.campus_id != campus.pk:
            raise ValidationError(
                {"person_id": "O produtor de eventos precisa ser do próprio campus."}
            )
        EventProducerAssignment.objects.get_or_create(person=person, campus=campus)
        people = Person.objects.filter(event_producer_assignments__campus=campus).distinct()
        return Response(PersonMiniSerializer(people, many=True).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True, methods=["delete"], url_path=r"event-producers/(?P<person_id>[0-9a-fA-F-]+)"
    )
    def remove_event_producer(self, request, pk=None, person_id=None):
        campus = self.get_object()
        if not can_assign_campus_event_producer(request.user, campus):
            raise PermissionDenied("Só o líder do campus pode remover produtores de eventos.")
        deleted, _ = EventProducerAssignment.objects.filter(campus=campus, person_id=person_id).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"], url_path="people-search")
    def people_search(self, request, pk=None):
        """?q= — people of this campus, for the pickers of anyone who appoints
        someone at this campus (cells/courses leaders, event producers, ministry
        leaders)."""
        campus = self.get_object()
        user = request.user
        can_appoint_here = (
            can_assign_campus_cells_leader(user, campus)
            or can_assign_campus_courses_leader(user, campus)
            or can_assign_campus_event_producer(user, campus)
            # A general ministry leader appoints that ministry's leaders on every campus.
            or bool(general_led_kinds(user))
        )
        if not can_appoint_here:
            raise PermissionDenied()
        return search_people(Person.objects.filter(campus=campus), request)


class MinistryGeneralLeadershipViewSet(viewsets.ReadOnlyModelViewSet):
    """General leaders of each ministry kind (e.g. líder geral de Jovens), who manage
    that ministry on every campus. Listing is open to anyone authenticated (the
    frontend shows them on the ministry screens); choosing them is manager-only:

    POST   /api/ministry-general-leaderships/{kind}/leaders/              {person_id}
    DELETE /api/ministry-general-leaderships/{kind}/leaders/{person_id}/
    """

    queryset = MinistryGeneralLeadership.objects.prefetch_related("leaders").all()
    serializer_class = MinistryGeneralLeadershipSerializer
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _validate_kind(kind):
        if kind not in MinistryKind.values:
            raise ValidationError({"kind": "Tipo de ministério inválido."})

    @action(detail=False, methods=["post"], url_path=r"(?P<kind>[a-z_]+)/leaders")
    def add_leader(self, request, kind=None):
        self._validate_kind(kind)
        _require_manager(request.user, "Só o gestor da mega igreja pode escolher o líder geral de um ministério.")
        general, _ = MinistryGeneralLeadership.objects.get_or_create(kind=kind)
        return _add_general_leader(request, general)

    @action(
        detail=False, methods=["delete"], url_path=r"(?P<kind>[a-z_]+)/leaders/(?P<person_id>[0-9a-fA-F-]+)"
    )
    def remove_leader(self, request, kind=None, person_id=None):
        self._validate_kind(kind)
        _require_manager(request.user, "Só o gestor da mega igreja pode remover o líder geral de um ministério.")
        return _remove_general_leader(MinistryGeneralLeadership.objects.filter(kind=kind).first(), person_id)


class MinistryViewSet(viewsets.ModelViewSet):
    serializer_class = MinistrySerializer
    permission_classes = [MinistryPermission]

    def get_queryset(self):
        qs = Ministry.objects.select_related("campus", "campus__church").prefetch_related(
            "leaderships__person", "members"
        )
        return scope_ministries(qs, self.request.user)

    @action(detail=True, methods=["get", "post"], url_path="leaderships")
    def leaderships(self, request, pk=None):
        ministry = self.get_object()
        if request.method == "GET":
            qs = ministry.leaderships.select_related("person")
            return Response(MinistryLeadershipSerializer(qs, many=True).data)

        # Each level appoints the level below it (see can_appoint_ministry_leaders).
        # Not can_manage_ministry — a ministry leader cannot promote/demote peers.
        if not can_appoint_ministry_leaders(request.user, ministry):
            raise PermissionDenied(
                "Só o líder do campus, o líder geral do ministério ou o gestor pode atribuir liderança de ministério."
            )
        serializer = MinistryLeadershipInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        leadership, _ = MinistryLeadership.objects.update_or_create(
            ministry=ministry,
            person=person,
            defaults={"role": serializer.validated_data["role"]},
        )
        return Response(
            MinistryLeadershipSerializer(leadership).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["delete"], url_path=r"leaderships/(?P<person_id>[0-9a-fA-F-]+)")
    def remove_leadership(self, request, pk=None, person_id=None):
        ministry = self.get_object()
        if not can_appoint_ministry_leaders(request.user, ministry):
            raise PermissionDenied(
                "Só o líder do campus, o líder geral do ministério ou o gestor pode remover liderança de ministério."
            )
        deleted, _ = MinistryLeadership.objects.filter(
            ministry=ministry, person_id=person_id
        ).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="members")
    def members(self, request, pk=None):
        ministry = self.get_object()  # enforces can_manage_ministry for POST via has_object_permission
        if request.method == "GET":
            return Response(PersonMiniSerializer(ministry.members.all(), many=True).data)

        serializer = MinistryMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        ministry.members.add(person)
        return Response(PersonMiniSerializer(ministry.members.all(), many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path=r"members/(?P<person_id>[0-9a-fA-F-]+)")
    def remove_member(self, request, pk=None, person_id=None):
        ministry = self.get_object()  # enforces can_manage_ministry via has_object_permission
        person = get_object_or_404(Person, pk=person_id)
        ministry.members.remove(person)
        return Response(status=status.HTTP_204_NO_CONTENT)


class SeasonViewSet(viewsets.ModelViewSet):
    """Cell seasons, church-wide. Anyone authenticated reads; the manager or the
    general cells leader creates/edits. GET /seasons/current/ returns the season
    running today (404 when between seasons)."""

    serializer_class = SeasonSerializer
    permission_classes = [SeasonPermission]
    filterset_fields = ["church"]

    def get_queryset(self):
        # Each church has its own calendar: only the person's church(es).
        qs = Season.objects.select_related("church")
        ids = season_church_ids(self.request.user)
        return qs if ids is None else qs.filter(church_id__in=ids)

    def perform_create(self, serializer):
        ids = season_church_ids(self.request.user, for_write=True)
        if ids is not None and serializer.validated_data["church"].pk not in ids:
            raise PermissionDenied("Você não pode criar temporadas para esta igreja.")
        serializer.save()

    def perform_update(self, serializer):
        church = serializer.validated_data.get("church")
        ids = season_church_ids(self.request.user, for_write=True)
        if church is not None and ids is not None and church.pk not in ids:
            raise PermissionDenied("Você não pode mover a temporada para esta igreja.")
        serializer.save()

    @action(detail=False, methods=["get"])
    def current(self, request):
        seasons = Season.objects.current()
        church_id = request.query_params.get("church") or getattr(
            request.user.campus, "church_id", None
        )
        if church_id:
            seasons = seasons.filter(church_id=church_id)
        season = seasons.first()
        if season is None:
            return Response({"detail": "Nenhuma temporada em andamento."}, status=status.HTTP_404_NOT_FOUND)
        return Response(SeasonSerializer(season).data)

    def perform_destroy(self, instance):
        if instance.cells.exists():
            raise ValidationError("Esta temporada tem células e não pode ser excluída.")
        instance.delete()


class CellViewSet(viewsets.ModelViewSet):
    serializer_class = CellSerializer
    permission_classes = [CellPermission]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    # e.g. ?season=current, ?active=true, ?meeting_day=3, ?meeting_day__in=3,5,
    # ?campus=<uuid>, ?target_audience=jovens, ?meeting_time__gte=19:00
    filterset_class = CellFilter
    # ?ordering=meeting_day,meeting_time gives the weekly calendar order.
    ordering_fields = ["name", "meeting_day", "meeting_time"]

    def get_queryset(self):
        qs = Cell.objects.select_related(
            "campus", "campus__church", "season", "parent_cell", "multiplication_leader", "supervisor"
        ).prefetch_related(
            "leaderships__person",
            Prefetch("supervision_visits", queryset=CellSupervisionVisit.objects.only("id", "cell_id", "date")),
            "members",
            # What the pending-meetings count and the health need: dates, and
            # who came (ids only).
            Prefetch(
                "meetings",
                queryset=CellMeeting.objects.only(
                    "id", "cell_id", "date", "held", "visitors_count"
                ).prefetch_related(
                    Prefetch("attendees", queryset=Person.objects.only("id")),
                    Prefetch("visitors", queryset=CellVisitor.objects.only("id")),
                ),
            ),
        )
        return scope_cells(qs, self.request.user)

    @action(detail=False, methods=["get"])
    def report(self, request):
        """Coordination report: weekly attendance, visitors and decisions per
        cell, per-campus comparison and how much of each campus is in a cell.
        Same filters as the list (?season=current, ?campus=...), over the cells
        the person coordinates."""
        if not cells_capabilities(request.user)["coordinate"]:
            raise PermissionDenied("Só a coordenação de células vê os relatórios.")
        base = Cell.objects.select_related("campus", "season", "parent_cell", "supervisor").prefetch_related(
            Prefetch("supervision_visits", queryset=CellSupervisionVisit.objects.only("id", "cell_id", "date")),
            "leaderships__person",
            "members",
            Prefetch("daughter_cells", queryset=Cell.objects.only("id", "parent_cell_id", "multiplied_on")),
            Prefetch("visitors", queryset=CellVisitor.objects.only("id", "cell_id", "status")),
            Prefetch(
                "meetings",
                queryset=CellMeeting.objects.only(
                    "id", "cell_id", "date", "held", "visitors_count", "decisions_count"
                ).prefetch_related(
                    Prefetch("attendees", queryset=Person.objects.only("id")),
                    Prefetch("visitors", queryset=CellVisitor.objects.only("id")),
                ),
            ),
        )
        cells = list(self.filter_queryset(scope_coordinated_cells(base, request.user)).order_by("campus__name", "name"))
        # Coverage looks at every cell of the same season(s), from any campus:
        # someone of campus A in a cell of campus B is still in a cell.
        season_ids = {cell.season_id for cell in cells}
        same_season = Q(season_id__in=[s for s in season_ids if s])
        if None in season_ids:
            same_season |= Q(season__isnull=True)
        season_cells = Cell.objects.filter(same_season)
        coverage = cell_report.campus_coverage({cell.campus_id for cell in cells}, season_cells)
        return Response(cell_report.build_report(cells, coverage))

    @action(detail=True, methods=["get"])
    def care(self, request, pk=None):
        """Pessoas para cuidar: participants who missed the last meetings in a
        row and visitors who didn't come back. For whoever manages the cell."""
        cell = self.get_object()
        if not can_manage_cell(request.user, cell):
            raise PermissionDenied("Só a liderança da célula vê esta lista.")
        meetings = list(CellMeeting.objects.filter(cell=cell).prefetch_related("attendees", "visitors"))
        visitors = CellVisitor.objects.filter(cell=cell, status=VisitorStatus.VISITING)
        absent, not_returning = cell_health.people_to_care(cell, meetings, visitors)

        def person(p):
            return {"id": str(p.pk), "name": p.name, "email": p.email, "phone": p.phone}

        return Response(
            {
                "absent_members": [
                    {
                        "person": person(row["person"]),
                        "absences": row["absences"],
                        "last_attended": row["last_attended"],
                    }
                    for row in absent
                ],
                "visitors_not_returning": [
                    {
                        "id": str(row["visitor"].pk),
                        "name": row["visitor"].name,
                        "phone": row["visitor"].phone,
                        "last_visit": row["last_visit"],
                        "visit_count": row["visit_count"],
                        "missed": row["missed"],
                    }
                    for row in not_returning
                ],
            }
        )

    @action(detail=True, methods=["get"])
    def calendar(self, request, pk=None):
        """Every scheduled date of the cell's period (from its meeting day) with
        its status — registered, not held, pending, today or upcoming — plus
        extra meetings outside the schedule, newest first, and a summary."""
        cell = self.get_object()
        # A fresh query: cell.meetings carries the list's slim prefetch (only()).
        meetings = list(
            CellMeeting.objects.filter(cell=cell)
            .select_related("created_by")
            .prefetch_related("attendees", "visitors")
        )
        entries = meeting_calendar.build_calendar(cell, meetings)
        return Response(
            {
                "entries": [
                    {
                        "date": entry.date,
                        "scheduled_date": entry.scheduled_date,
                        "status": entry.status,
                        "meeting": CellMeetingSerializer(entry.meeting).data if entry.meeting else None,
                    }
                    for entry in entries
                ],
                "summary": meeting_calendar.summarize(entries),
            }
        )

    def perform_create(self, serializer):
        if not can_coordinate_campus_cells(self.request.user, serializer.validated_data["campus"]):
            raise PermissionDenied("Você não pode criar células neste campus.")
        serializer.save()

    def perform_update(self, serializer):
        # Moving a cell to another campus needs coordination rights there too.
        new_campus = serializer.validated_data.get("campus")
        if new_campus and new_campus != serializer.instance.campus:
            if not can_coordinate_campus_cells(self.request.user, new_campus):
                raise PermissionDenied("Você não pode mover a célula para este campus.")
        serializer.save()

    @action(detail=True, methods=["get"], url_path="people-search")
    def people_search(self, request, pk=None):
        """?q=&for=members|leaders — candidates to add to this cell. Members may
        come from any campus; leaders only from the cell's campus."""
        cell = self.get_object()
        if not can_manage_cell(request.user, cell):
            raise PermissionDenied()
        qs = Person.objects.all()
        if request.query_params.get("for") == "leaders":
            qs = qs.filter(campus=cell.campus)
        return search_people(qs, request)

    @action(detail=True, methods=["post"])
    def renew(self, request, pk=None):
        """Carry this cell over to another season: a new Cell with the same data
        and leaders (and, by default, members), linked back via previous_cell.
        The old cell and its meetings stay untouched as history."""
        cell = self.get_object()
        if not can_coordinate_campus_cells(request.user, cell.campus):
            raise PermissionDenied("Você não pode renovar as células deste campus.")
        serializer = CellRenewInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        season = serializer.validated_data["season_id"]

        if season.church_id != cell.campus.church_id:
            raise ValidationError({"season_id": "A temporada precisa ser da mesma igreja do campus."})
        if season.pk == cell.season_id:
            raise ValidationError({"season_id": "A célula já é desta temporada."})
        if not cell.is_complete:
            # The new cell copies these fields; it would be born incomplete.
            raise ValidationError(
                "Defina o dia, o horário e o público-alvo da célula antes de renová-la."
            )
        if Cell.objects.filter(campus=cell.campus, season=season, name=cell.name).exists():
            raise ValidationError({"season_id": f"Já existe a célula \"{cell.name}\" nesta temporada."})

        with transaction.atomic():
            new_cell = Cell.objects.create(
                campus=cell.campus,
                season=season,
                previous_cell=cell,
                name=cell.name,
                meeting_day=cell.meeting_day,
                meeting_time=cell.meeting_time,
                target_audience=cell.target_audience,
                address=cell.address,
                description=cell.description,
                # Same mother, but the multiplication itself isn't repeated.
                parent_cell=cell.parent_cell,
                multiplication_date=cell.multiplication_date,
                multiplication_leader=cell.multiplication_leader,
                supervisor=cell.supervisor,
                # start/end dates are season-specific, so they aren't copied.
            )
            CellLeadership.objects.bulk_create(
                CellLeadership(cell=new_cell, person_id=leadership.person_id, role=leadership.role)
                for leadership in cell.leaderships.all()
            )
            if serializer.validated_data["copy_members"]:
                new_cell.members.set(cell.members.all())

        return Response(
            CellSerializer(new_cell, context={"request": request}).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["get", "post"], url_path="leaderships")
    def leaderships(self, request, pk=None):
        cell = self.get_object()
        if request.method == "GET":
            qs = cell.leaderships.select_related("person")
            return Response(CellLeadershipSerializer(qs, many=True).data)

        # Leaders and co-leaders are assigned from above — the cell's supervisor
        # or coordination — regardless of can_manage_cell: a célula leader
        # cannot promote/demote themselves or others. A leader in training is
        # named by the cell's leader too (each level appoints the one below),
        # but only over someone without a role.
        serializer = CellLeadershipInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data["role"]
        if not can_supervise_cell(request.user, cell):
            if role != CellRole.TRAINEE or not can_appoint_cell_trainee(request.user, cell):
                raise PermissionDenied("Você não pode atribuir liderança às células deste campus.")
            current = cell.leaderships.filter(person_id=serializer.validated_data["person_id"]).first()
            if current and current.role != CellRole.TRAINEE:
                raise PermissionDenied("Só a coordenação muda o papel de líderes e colíderes.")
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        # Members may come from any campus, but leaders must belong to the cell's.
        if person.campus_id != cell.campus_id:
            raise ValidationError(
                {"person_id": "O líder da célula precisa ser do mesmo campus da célula."}
            )
        leadership, _ = CellLeadership.objects.update_or_create(
            cell=cell,
            person=person,
            defaults={"role": role},
        )
        return Response(
            CellLeadershipSerializer(leadership).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["delete"], url_path=r"leaderships/(?P<person_id>[0-9a-fA-F-]+)")
    def remove_leadership(self, request, pk=None, person_id=None):
        cell = self.get_object()
        leadership = CellLeadership.objects.filter(cell=cell, person_id=person_id).first()
        allowed = can_supervise_cell(request.user, cell) or (
            leadership is not None
            and leadership.role == CellRole.TRAINEE
            and can_appoint_cell_trainee(request.user, cell)
        )
        if not allowed:
            raise PermissionDenied("Você não pode remover liderança das células deste campus.")
        if leadership is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        leadership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["put"])
    def supervisor(self, request, pk=None):
        """Who supervises the cell (person_id, or null to clear): someone of the
        cell's campus, named by whoever coordinates the campus's cells."""
        cell = self.get_object()
        if not can_coordinate_campus_cells(request.user, cell.campus):
            raise PermissionDenied("Só a coordenação de células define o supervisor.")
        serializer = CellSupervisorInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person_id = serializer.validated_data["person_id"]
        if person_id:
            person = get_object_or_404(Person, pk=person_id)
            if person.campus_id != cell.campus_id:
                raise ValidationError({"person_id": "O supervisor precisa ser do mesmo campus da célula."})
        cell.supervisor_id = person_id
        cell.save(update_fields=["supervisor", "updated_at"])
        return Response(CellSerializer(self.get_queryset().get(pk=cell.pk), context={"request": request}).data)

    @action(detail=True, methods=["put", "delete"], url_path="multiplication-plan")
    def multiplication_plan(self, request, pk=None):
        """The cell's multiplication plan: the planned date and who will lead
        the new cell (someone of the cell, usually the leader in training).
        Set by whoever manages the cell; DELETE clears it."""
        cell = self.get_object()
        if not can_manage_cell(request.user, cell):
            raise PermissionDenied("Só a liderança da célula define o plano de multiplicação.")
        if request.method == "DELETE":
            cell.multiplication_date = None
            cell.multiplication_leader = None
        else:
            serializer = CellMultiplicationPlanInputSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            leader_id = serializer.validated_data["leader_id"]
            if leader_id and leader_id not in cell.participant_ids():
                raise ValidationError({"leader_id": "Escolha alguém que participa da célula."})
            cell.multiplication_date = serializer.validated_data["date"]
            cell.multiplication_leader_id = leader_id
        cell.save(update_fields=["multiplication_date", "multiplication_leader", "updated_at"])
        return Response(CellSerializer(self.get_object(), context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def multiply(self, request, pk=None):
        """Multiplicação: a new cell born from this one, in the same campus and
        season, led by `leader_id` and with the people in `member_ids`, who
        leave this cell. The schedule defaults to this cell's. Clears the plan.
        For whoever coordinates the campus's cells (it creates a cell)."""
        cell = self.get_object()
        if not can_coordinate_campus_cells(request.user, cell.campus):
            raise PermissionDenied("Só a coordenação de células pode multiplicar uma célula.")
        if not cell.is_complete:
            raise ValidationError("Defina o dia, o horário e o público-alvo da célula antes de multiplicá-la.")
        serializer = CellMultiplyInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        leader = get_object_or_404(Person, pk=data["leader_id"])
        if leader.campus_id != cell.campus_id:
            raise ValidationError({"leader_id": "O líder da célula precisa ser do mesmo campus da célula."})
        roles = {lead.person_id: lead.role for lead in cell.leaderships.all()}
        if roles.get(leader.pk) == CellRole.LEADER:
            raise ValidationError({"leader_id": "O líder desta célula continua nela; escolha outra pessoa."})
        moving = set(data["member_ids"]) - {leader.pk}
        if moving - cell.participant_ids():
            raise ValidationError({"member_ids": "Só quem participa da célula pode ir para a nova."})
        if any(roles.get(person_id) == CellRole.LEADER for person_id in moving):
            raise ValidationError({"member_ids": "O líder desta célula continua nela."})
        if Cell.objects.filter(campus=cell.campus, season=cell.season, name=data["name"]).exists():
            raise ValidationError({"name": f"Já existe a célula “{data['name']}” nesta temporada."})

        with transaction.atomic():
            daughter = Cell.objects.create(
                campus=cell.campus,
                season=cell.season,
                parent_cell=cell,
                multiplied_on=timezone.localdate(),
                name=data["name"],
                meeting_day=data.get("meeting_day", cell.meeting_day),
                meeting_time=data.get("meeting_time", cell.meeting_time),
                target_audience=data.get("target_audience", cell.target_audience),
                address=data.get("address", cell.address),
                start_date=cell.start_date,
                end_date=cell.end_date,
            )
            CellLeadership.objects.create(cell=daughter, person=leader, role=CellRole.LEADER)
            daughter.members.set(moving)
            # Who moved leaves the mother cell (as member and from any role).
            leaving = moving | {leader.pk}
            cell.members.remove(*leaving)
            cell.leaderships.filter(person_id__in=leaving).delete()
            cell.multiplication_date = None
            cell.multiplication_leader = None
            cell.save(update_fields=["multiplication_date", "multiplication_leader", "updated_at"])

        return Response(
            CellSerializer(self.get_queryset().get(pk=daughter.pk), context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def lineage(self, request, pk=None):
        """Gerações: the cell's generation, its mothers up the line and the
        cells born from it (or from its earlier versions, across seasons)."""
        cell = self.get_object()
        ancestors, seen, current = [], {cell.pk}, cell.parent_cell
        while current is not None and current.pk not in seen:
            ancestors.append(current)
            seen.add(current.pk)
            current = current.parent_cell
        # The same cell in earlier seasons: a daughter may hang from any of them.
        versions, current = [cell.pk], cell.previous_cell
        while current is not None and current.pk not in versions:
            versions.append(current.pk)
            current = current.previous_cell
        daughters = (
            Cell.objects.filter(parent_cell_id__in=versions, multiplied_on__isnull=False)
            .select_related("season")
            .prefetch_related("leaderships__person")
            .order_by("multiplied_on", "name")
        )

        def mini(c):
            return {
                "id": str(c.pk),
                "name": c.name,
                "season": c.season.name if c.season_id else None,
                "multiplied_on": c.multiplied_on,
            }

        return Response(
            {
                "generation": len(ancestors) + 1,
                "ancestors": [mini(c) for c in ancestors],
                "daughters": [
                    {
                        **mini(c),
                        "leaders": [
                            lead.person.name for lead in c.leaderships.all() if lead.role == CellRole.LEADER
                        ],
                    }
                    for c in daughters
                ],
            }
        )

    @action(detail=True, methods=["get", "post"], url_path="members")
    def members(self, request, pk=None):
        cell = self.get_object()  # enforces can_manage_cell for POST via has_object_permission
        if request.method == "GET":
            return Response(PersonMiniSerializer(cell.members.all(), many=True).data)

        serializer = CellMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        cell.members.add(person)
        return Response(PersonMiniSerializer(cell.members.all(), many=True).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path=r"members/(?P<person_id>[0-9a-fA-F-]+)")
    def remove_member(self, request, pk=None, person_id=None):
        cell = self.get_object()  # enforces can_manage_cell via has_object_permission
        person = get_object_or_404(Person, pk=person_id)
        cell.members.remove(person)
        return Response(status=status.HTTP_204_NO_CONTENT)


class CellMeetingViewSet(viewsets.ModelViewSet):
    """Meetings of a cell, with attendance. ?cell=<uuid> to list one cell's
    meetings. Read: whoever sees the cell; write: whoever can manage it."""

    serializer_class = CellMeetingSerializer
    permission_classes = [CellMeetingPermission]
    filterset_class = CellMeetingFilter

    def get_queryset(self):
        qs = CellMeeting.objects.select_related("cell", "created_by").prefetch_related("attendees", "visitors")
        return scope_cell_meetings(qs, self.request.user)

    def perform_create(self, serializer):
        cell = serializer.validated_data["cell"]
        if not can_manage_cell(self.request.user, cell):
            raise PermissionDenied("Você não pode registrar encontros desta célula.")
        if CellMeeting.objects.filter(cell=cell, date=serializer.validated_data["date"]).exists():
            raise ValidationError({"date": "Já existe um encontro registrado nesta data."})
        serializer.save(created_by=self.request.user)

    def perform_update(self, serializer):
        meeting = serializer.instance
        new_date = serializer.validated_data.get("date", meeting.date)
        if CellMeeting.objects.filter(cell=meeting.cell, date=new_date).exclude(pk=meeting.pk).exists():
            raise ValidationError({"date": "Já existe um encontro registrado nesta data."})
        serializer.save()


class CellSupervisionVisitViewSet(viewsets.ModelViewSet):
    """Supervision visits. ?cell=<uuid> for one cell. Only the cell's
    supervisor and coordination see them (the notes are about the leaders)."""

    serializer_class = CellSupervisionVisitSerializer
    permission_classes = [CellSupervisionVisitPermission]

    def get_queryset(self):
        qs = CellSupervisionVisit.objects.select_related("cell", "cell__campus", "created_by").filter(
            cell__in=scope_supervised_cells(Cell.objects.all(), self.request.user)
        )
        cell_id = self.request.query_params.get("cell")
        if cell_id:
            qs = qs.filter(cell_id=cell_id)
        return qs

    def perform_create(self, serializer):
        if not can_supervise_cell(self.request.user, serializer.validated_data["cell"]):
            raise PermissionDenied("Você não supervisiona esta célula.")
        serializer.save(created_by=self.request.user)


class CellVisitorViewSet(viewsets.ModelViewSet):
    """A cell's named guests. ?cell=<uuid> for one cell. Only whoever manages
    the cell sees them (they carry contact data). Actions:
    - POST {id}/forward/: hands the visitor to Reconciliação as a pending lead
      of the cell's campus (its triage queue assigns a reconciliador).
    - POST {id}/make-member/: the visitor joins the cell — an existing person
      (person_id) or a new one registered with the visitor's data (email).
    """

    serializer_class = CellVisitorSerializer
    permission_classes = [CellVisitorPermission]

    def get_queryset(self):
        qs = (
            CellVisitor.objects.select_related("cell", "cell__campus", "person")
            .filter(cell__in=scope_managed_cells(Cell.objects.all(), self.request.user))
            .annotate(
                visit_count=Count("meetings", filter=Q(meetings__held=True)),
                first_visit=Min("meetings__date", filter=Q(meetings__held=True)),
                last_visit=Max("meetings__date", filter=Q(meetings__held=True)),
            )
        )
        cell_id = self.request.query_params.get("cell")
        if cell_id:
            qs = qs.filter(cell_id=cell_id)
        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return qs

    def perform_create(self, serializer):
        if not can_manage_cell(self.request.user, serializer.validated_data["cell"]):
            raise PermissionDenied("Você não pode cadastrar visitantes nesta célula.")
        serializer.save(created_by=self.request.user)

    def _fresh(self, visitor):
        return Response(self.get_serializer(self.get_queryset().get(pk=visitor.pk)).data)

    @action(detail=True, methods=["post"])
    def forward(self, request, pk=None):
        visitor = self.get_object()
        if visitor.reconciliation_lead_id:
            raise ValidationError("Este visitante já foi encaminhado à Reconciliação.")
        if visitor.status == VisitorStatus.MEMBER:
            raise ValidationError("Este visitante já é membro da célula.")
        cell = visitor.cell
        with transaction.atomic():
            visitor.reconciliation_lead = ReconciliationLead.objects.create(
                name=visitor.name,
                phone=visitor.phone,
                email=visitor.email,
                campus=cell.campus,
                # Shown in the triage queue: where the lead came from.
                raw_campus_label=f"{cell.campus.name} · célula {cell.name}",
                submitted_at=timezone.now(),
                dedup_key=f"cell-visitor:{visitor.pk}",
                raw_row={"source": "cell_visitor", "cell_id": str(cell.pk), "cell": cell.name},
            )
            visitor.status = VisitorStatus.FORWARDED
            visitor.save(update_fields=["reconciliation_lead", "status", "updated_at"])
        return self._fresh(visitor)

    @action(detail=True, methods=["post"], url_path="make-member")
    def make_member(self, request, pk=None):
        visitor = self.get_object()
        if visitor.status == VisitorStatus.MEMBER:
            raise ValidationError("Este visitante já é membro da célula.")
        serializer = MakeVisitorMemberInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = serializer.validated_data.get("person_id")
        with transaction.atomic():
            if person is None:
                email = serializer.validated_data["email"]
                if Person.objects.filter(email__iexact=email).exists():
                    raise ValidationError(
                        {"email": "Já existe uma pessoa com este e-mail. Escolha-a na busca."}
                    )
                person = Person.objects.create_user(
                    email=email,
                    name=visitor.name,
                    phone=visitor.phone,
                    campus=visitor.cell.campus,
                    password=None,
                )
            visitor.cell.members.add(person)
            visitor.person = person
            visitor.status = VisitorStatus.MEMBER
            visitor.save(update_fields=["person", "status", "updated_at"])
        return self._fresh(visitor)


class TeamViewSet(viewsets.ModelViewSet):
    serializer_class = TeamSerializer
    permission_classes = [TeamPermission]

    def get_queryset(self):
        qs = Team.objects.select_related("ministry").prefetch_related(
            "leaderships__person", "members", "point_awards"
        )
        ministry_id = self.request.query_params.get("ministry")
        if ministry_id:
            qs = qs.filter(ministry_id=ministry_id)
        return scope_teams(qs, self.request.user)

    @action(detail=True, methods=["get", "post"], url_path="leaderships")
    def leaderships(self, request, pk=None):
        team = self.get_object()
        if request.method == "GET":
            qs = team.leaderships.select_related("person")
            return Response(TeamLeadershipSerializer(qs, many=True).data)

        # One level up manages leadership assignment (same rule as Ministry): the
        # team's own leader/co-leader cannot promote/demote themselves or others —
        # only whoever manages the parent ministry (or the overall manager) can.
        if not can_manage_ministry(request.user, team.ministry):
            raise PermissionDenied("Só quem lidera o ministério (ou o gestor) pode atribuir liderança de equipe.")
        serializer = TeamLeadershipInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        leadership, _ = TeamLeadership.objects.update_or_create(
            team=team, person=person, defaults={"role": serializer.validated_data["role"]}
        )
        return Response(TeamLeadershipSerializer(leadership).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["delete"], url_path=r"leaderships/(?P<person_id>[0-9a-fA-F-]+)")
    def remove_leadership(self, request, pk=None, person_id=None):
        team = self.get_object()
        if not can_manage_ministry(request.user, team.ministry):
            raise PermissionDenied("Só quem lidera o ministério (ou o gestor) pode remover liderança de equipe.")
        deleted, _ = TeamLeadership.objects.filter(team=team, person_id=person_id).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="members")
    def members(self, request, pk=None):
        team = self.get_object()  # enforces can_manage_team for POST via has_object_permission
        if request.method == "GET":
            return Response(PersonMiniSerializer(team.members.all(), many=True).data)

        serializer = TeamMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        if not team.ministry.members.filter(pk=person.pk).exists():
            raise ValidationError(
                "Só pode adicionar à equipe quem já participa do ministério."
            )
        team.members.add(person)
        return Response(
            PersonMiniSerializer(team.members.all(), many=True).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["delete"], url_path=r"members/(?P<person_id>[0-9a-fA-F-]+)")
    def remove_member(self, request, pk=None, person_id=None):
        team = self.get_object()  # enforces can_manage_team via has_object_permission
        person = get_object_or_404(Person, pk=person_id)
        team.members.remove(person)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"], url_path="point-awards")
    def point_awards(self, request, pk=None):
        team = self.get_object()  # enforces can_manage_team for POST via has_object_permission
        if request.method == "GET":
            return Response(TeamPointAwardSerializer(team.point_awards.all(), many=True).data)

        serializer = TeamPointAwardInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        award = TeamPointAward.objects.create(
            team=team, awarded_by=request.user, **serializer.validated_data
        )
        return Response(TeamPointAwardSerializer(award).data, status=status.HTTP_201_CREATED)


class ActivityViewSet(viewsets.ModelViewSet):
    serializer_class = ActivitySerializer
    permission_classes = [ActivityPermission]

    def get_queryset(self):
        qs = Activity.objects.select_related("team", "team__ministry", "created_by")
        return scope_activities(qs, self.request.user)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        # Manual lookup (not self.get_object()) — visibility comes from the scoped
        # queryset, but authorization here is deliberately looser than can_manage_team:
        # any team member may mark their OWN completion; only marking on behalf of
        # someone else requires managing the team.
        activity = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = ActivityCompletionInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        person_id = serializer.validated_data.get("person_id")
        if person_id and str(person_id) != str(request.user.pk):
            if not can_manage_team(request.user, activity.team):
                raise PermissionDenied(
                    "Só quem lidera a equipe pode marcar conclusão em nome de outra pessoa."
                )
            person = get_object_or_404(Person, pk=person_id)
        else:
            person = request.user
            if not (
                activity.team.members.filter(pk=person.pk).exists()
                or can_manage_team(person, activity.team)
            ):
                raise PermissionDenied("Você não participa desta equipe.")

        date = serializer.validated_data.get("date") or timezone.localdate()
        if activity.kind == ActivityKind.EVENTUAL and activity.completions.filter(person=person).exists():
            raise ValidationError("Esta atividade eventual já foi concluída por essa pessoa.")
        if activity.completions.filter(person=person, date=date).exists():
            raise ValidationError("Essa pessoa já concluiu esta atividade nessa data.")

        completion = ActivityCompletion.objects.create(
            activity=activity, person=person, date=date, points_awarded=activity.points
        )
        return Response(ActivityCompletionSerializer(completion).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="completions")
    def completions(self, request, pk=None):
        activity = get_object_or_404(self.get_queryset(), pk=pk)
        return Response(
            ActivityCompletionSerializer(activity.completions.select_related("person"), many=True).data
        )
