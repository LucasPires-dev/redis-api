from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .choices import ActivityKind
from .filters import CellFilter, CellMeetingFilter
from .models import (
    Activity,
    ActivityCompletion,
    Campus,
    CampusCellsLeadership,
    CampusLeadership,
    Cell,
    CellLeadership,
    CellMeeting,
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
    MinistryPermission,
    SeasonPermission,
    TeamPermission,
    can_assign_campus_cells_leader,
    can_coordinate_campus_cells,
    can_manage_cell,
    can_manage_ministry,
    can_manage_team,
    scope_activities,
    scope_cell_meetings,
    scope_cells,
    scope_ministries,
    scope_teams,
)
from .serializers import (
    ActivityCompletionInputSerializer,
    ActivityCompletionSerializer,
    ActivitySerializer,
    CampusCellsLeadershipSerializer,
    CampusLeadershipInputSerializer,
    CampusLeadershipSerializer,
    CampusSerializer,
    CellLeadershipInputSerializer,
    CellLeadershipSerializer,
    CellMeetingSerializer,
    CellMemberSerializer,
    CellRenewInputSerializer,
    CellSerializer,
    ChurchSerializer,
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


class ChurchViewSet(viewsets.ReadOnlyModelViewSet):
    """Creation happens via seed/admin for now — see docs/access-levels equivalent
    in the plan: only Ministry has a dedicated create endpoint at this stage."""

    queryset = Church.objects.all()
    serializer_class = ChurchSerializer
    permission_classes = [IsAuthenticated]


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

        # Assigning leadership is manager-only — a campus leader cannot promote/
        # demote themselves or others, same rule as Ministry/Cell/Team leadership.
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
        """Responsáveis pelas células do campus. Defined by the campus leader
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

    @action(detail=True, methods=["get"], url_path="people-search")
    def people_search(self, request, pk=None):
        """?q= — people of this campus, for the cells-leader picker."""
        campus = self.get_object()
        if not can_assign_campus_cells_leader(request.user, campus):
            raise PermissionDenied()
        return search_people(Person.objects.filter(campus=campus), request)


class MinistryGeneralLeadershipViewSet(viewsets.ReadOnlyModelViewSet):
    """Assignment happens via admin/seed for now (see plan) — read-only endpoint so
    the frontend can show "líder geral" on the ministry list/detail screens."""

    queryset = MinistryGeneralLeadership.objects.prefetch_related("leaders").all()
    serializer_class = MinistryGeneralLeadershipSerializer
    permission_classes = [IsAuthenticated]


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

        # Assigning leadership is manager-only, regardless of can_manage_ministry —
        # a ministry leader cannot promote/demote themselves or others.
        if not request.user.is_manager:
            raise PermissionDenied("Só o gestor da mega igreja pode atribuir liderança de ministério.")
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
        if not request.user.is_manager:
            raise PermissionDenied("Só o gestor da mega igreja pode remover liderança de ministério.")
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

    queryset = Season.objects.select_related("church")
    serializer_class = SeasonSerializer
    permission_classes = [SeasonPermission]
    filterset_fields = ["church"]

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
        qs = Cell.objects.select_related("campus", "campus__church", "season").prefetch_related(
            "leaderships__person", "members"
        )
        return scope_cells(qs, self.request.user)

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

        # Assigning leadership is for the manager or the general cells leader,
        # regardless of can_manage_cell — a célula leader cannot promote/demote
        # themselves or others.
        if not can_coordinate_campus_cells(request.user, cell.campus):
            raise PermissionDenied("Você não pode atribuir liderança às células deste campus.")
        serializer = CellLeadershipInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=serializer.validated_data["person_id"])
        # Members may come from any campus, but leaders must belong to the cell's.
        if person.campus_id != cell.campus_id:
            raise ValidationError(
                {"person_id": "O líder da célula precisa ser do mesmo campus da célula."}
            )
        leadership, _ = CellLeadership.objects.update_or_create(
            cell=cell,
            person=person,
            defaults={"role": serializer.validated_data["role"]},
        )
        return Response(
            CellLeadershipSerializer(leadership).data, status=status.HTTP_201_CREATED
        )

    @action(detail=True, methods=["delete"], url_path=r"leaderships/(?P<person_id>[0-9a-fA-F-]+)")
    def remove_leadership(self, request, pk=None, person_id=None):
        cell = self.get_object()
        if not can_coordinate_campus_cells(request.user, cell.campus):
            raise PermissionDenied("Você não pode remover liderança das células deste campus.")
        deleted, _ = CellLeadership.objects.filter(cell=cell, person_id=person_id).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)

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
        qs = CellMeeting.objects.select_related("cell", "created_by").prefetch_related("attendees")
        return scope_cell_meetings(qs, self.request.user)

    def perform_create(self, serializer):
        cell = serializer.validated_data["cell"]
        if not can_manage_cell(self.request.user, cell):
            raise PermissionDenied("Você não pode registrar encontros desta célula.")
        if CellMeeting.objects.filter(cell=cell, date=serializer.validated_data["date"]).exists():
            raise ValidationError({"date": "Já existe um encontro registrado nesta data."})
        serializer.save(created_by=self.request.user)


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
