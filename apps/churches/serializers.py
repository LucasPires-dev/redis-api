from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from . import meeting_calendar
from .choices import LeadershipRole
from .models import (
    Activity,
    ActivityCompletion,
    Campus,
    CampusCellsLeadership,
    CampusLeadership,
    Cell,
    CellLeadership,
    CellMeeting,
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
from .permissions import can_coordinate_campus_cells, can_manage_cell

Person = get_user_model()


class PersonMiniSerializer(serializers.ModelSerializer):
    """Small, local read-only view of a Person — defined here (instead of importing
    apps.people.serializers) to avoid a churches<->people serializer import cycle."""

    class Meta:
        model = Person
        fields = ["id", "name", "email"]


class ChurchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Church
        fields = ["id", "name", "abbreviation"]


class CampusMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Campus
        fields = ["id", "name", "city"]


class CampusSerializer(serializers.ModelSerializer):
    church = ChurchSerializer(read_only=True)
    leaders = serializers.SerializerMethodField()
    co_leaders = serializers.SerializerMethodField()

    class Meta:
        model = Campus
        fields = ["id", "church", "name", "city", "leaders", "co_leaders"]

    def get_leaders(self, obj):
        return PersonMiniSerializer(obj.leaders(), many=True).data

    def get_co_leaders(self, obj):
        return PersonMiniSerializer(obj.co_leaders(), many=True).data


class CampusLeadershipSerializer(serializers.ModelSerializer):
    """Read-only representation — creation/removal is handled explicitly in the view
    (see CampusLeadershipInputSerializer), not through ModelSerializer.create()."""

    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = CampusLeadership
        fields = ["id", "campus", "person", "role"]


class CampusLeadershipInputSerializer(serializers.Serializer):
    person_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=LeadershipRole.choices)


class CampusCellsLeadershipSerializer(serializers.ModelSerializer):
    """Read-only; assignment goes through CampusViewSet.cells_leaderships."""

    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = CampusCellsLeadership
        fields = ["id", "campus", "person", "role"]


class GeneralLeaderInputSerializer(serializers.Serializer):
    """Payload for adding someone to a general (church-wide) leadership."""

    person_id = serializers.UUIDField()


class CampusCoursesLeadershipSerializer(serializers.ModelSerializer):
    """Read-only; assignment goes through CampusViewSet.courses_leaderships."""

    person = PersonMiniSerializer(read_only=True)

    class Meta:
        from apps.courses.models import CampusCoursesLeadership

        model = CampusCoursesLeadership
        fields = ["id", "campus", "person", "role"]


class MinistryLeadershipSerializer(serializers.ModelSerializer):
    """Read-only representation — creation/removal is handled explicitly in the view
    (see MinistryLeadershipInputSerializer), not through ModelSerializer.create()."""

    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = MinistryLeadership
        fields = ["id", "ministry", "person", "role"]


class MinistryLeadershipInputSerializer(serializers.Serializer):
    person_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=LeadershipRole.choices)


class MinistrySerializer(serializers.ModelSerializer):
    campus = CampusMiniSerializer(read_only=True)
    campus_id = serializers.PrimaryKeyRelatedField(
        source="campus", queryset=Campus.objects.all(), write_only=True
    )
    leaders = serializers.SerializerMethodField()
    co_leaders = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(source="members.count", read_only=True)

    class Meta:
        model = Ministry
        fields = [
            "id",
            "campus",
            "campus_id",
            "kind",
            "name",
            "description",
            "min_age",
            "max_age",
            "leaders",
            "co_leaders",
            "member_count",
        ]

    def get_leaders(self, obj):
        return PersonMiniSerializer(obj.leaders(), many=True).data

    def get_co_leaders(self, obj):
        return PersonMiniSerializer(obj.co_leaders(), many=True).data


class MinistryMemberSerializer(serializers.Serializer):
    """Input payload for adding/removing a participant from a ministry."""

    person_id = serializers.UUIDField()


class MinistryMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ministry
        fields = ["id", "name", "kind"]


class CellLeadershipSerializer(serializers.ModelSerializer):
    """Read-only representation — creation/removal is handled explicitly in the view
    (see CellLeadershipInputSerializer), not through ModelSerializer.create()."""

    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = CellLeadership
        fields = ["id", "cell", "person", "role"]


class CellLeadershipInputSerializer(serializers.Serializer):
    person_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=LeadershipRole.choices)


class CellMemberSerializer(serializers.Serializer):
    """Input payload for adding/removing a participant from a célula."""

    person_id = serializers.UUIDField()


class SeasonSerializer(serializers.ModelSerializer):
    church_id = serializers.PrimaryKeyRelatedField(
        source="church", queryset=Church.objects.all(), write_only=True
    )
    # Computed from today's date, never stored (see Season docstring).
    is_current = serializers.BooleanField(read_only=True)

    class Meta:
        model = Season
        fields = ["id", "church", "church_id", "name", "start_date", "end_date", "is_current"]
        read_only_fields = ["church"]

    def validate(self, attrs):
        # Run the model's clean() (end after start, no overlap with another
        # season of the same church) on the merged instance.
        instance = Season(**{**self._current_values(), **attrs})
        if self.instance:
            instance.pk = self.instance.pk
        try:
            instance.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                exc.message_dict if hasattr(exc, "error_dict") else {"non_field_errors": exc.messages}
            )
        return attrs

    def _current_values(self):
        if not self.instance:
            return {}
        return {
            "church": self.instance.church,
            "name": self.instance.name,
            "start_date": self.instance.start_date,
            "end_date": self.instance.end_date,
        }


class SeasonMiniSerializer(serializers.ModelSerializer):
    is_current = serializers.BooleanField(read_only=True)

    class Meta:
        model = Season
        fields = ["id", "name", "start_date", "end_date", "is_current"]


class CellSerializer(serializers.ModelSerializer):
    campus = CampusMiniSerializer(read_only=True)
    campus_id = serializers.PrimaryKeyRelatedField(
        source="campus", queryset=Campus.objects.all(), write_only=True
    )
    season = SeasonMiniSerializer(read_only=True)
    season_id = serializers.PrimaryKeyRelatedField(
        source="season", queryset=Season.objects.all(), write_only=True
    )
    # The dates that actually apply: the cell's own, or else its season's.
    effective_start = serializers.DateField(read_only=True)
    effective_end = serializers.DateField(read_only=True)
    # Whether the requesting user may edit this cell, its members and meetings —
    # lets the UI show or hide the management buttons.
    can_manage = serializers.SerializerMethodField()
    # Whether the requesting user may assign leaders and renew this cell
    # (can_coordinate_campus_cells for its campus).
    can_coordinate = serializers.SerializerMethodField()
    leaders = serializers.SerializerMethodField()
    co_leaders = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(source="members.count", read_only=True)
    # Everyone in the cell: leaders, co-leaders and members, each counted once
    # (a leader isn't always in `members`). What the screens show as "membros".
    participant_count = serializers.SerializerMethodField()
    # Past scheduled meetings nobody registered (see meeting_calendar.py).
    pending_meetings = serializers.SerializerMethodField()
    # Required fields still empty (cells created before they were required);
    # while any is missing the cell isn't active.
    missing_fields = serializers.ListField(read_only=True)
    # Human label for `meeting_day` (e.g. "Quarta-feira"); empty string when unset.
    meeting_day_display = serializers.CharField(source="get_meeting_day_display", read_only=True)
    # "HH:MM" instead of DRF's default "HH:MM:SS" — seconds are never used here.
    meeting_time = serializers.TimeField(
        format="%H:%M", input_formats=["%H:%M", "%H:%M:%S"], required=False, allow_null=True
    )

    class Meta:
        model = Cell
        fields = [
            "id",
            "campus",
            "campus_id",
            "name",
            "meeting_day",
            "meeting_day_display",
            "meeting_time",
            "target_audience",
            "address",
            "description",
            "season",
            "season_id",
            "start_date",
            "end_date",
            "effective_start",
            "effective_end",
            "previous_cell",
            "leaders",
            "co_leaders",
            "member_count",
            "participant_count",
            "pending_meetings",
            "missing_fields",
            "can_manage",
            "can_coordinate",
        ]
        # Set only by the "renew" action.
        read_only_fields = ["previous_cell"]

    def get_participant_count(self, obj):
        # Iterates the prefetched relations (CellViewSet) instead of querying.
        people = {leadership.person_id for leadership in obj.leaderships.all()}
        people.update(member.pk for member in obj.members.all())
        return len(people)

    def get_leaders(self, obj):
        return PersonMiniSerializer(obj.leaders(), many=True).data

    def get_co_leaders(self, obj):
        return PersonMiniSerializer(obj.co_leaders(), many=True).data

    def get_can_manage(self, obj):
        request = self.context.get("request")
        return bool(request) and can_manage_cell(request.user, obj)

    def get_pending_meetings(self, obj):
        return meeting_calendar.pending_count(obj, obj.meetings.all())

    def get_can_coordinate(self, obj):
        request = self.context.get("request")
        return bool(request) and can_coordinate_campus_cells(request.user, obj.campus)

    def validate(self, attrs):
        def current(field):
            if field in attrs:
                return attrs[field]
            return getattr(self.instance, field, None)

        campus = current("campus")
        season = current("season")
        start, end = current("start_date"), current("end_date")

        # Day, time and audience are required on create, can't be cleared, and
        # editing an older cell that lacks them means filling them in.
        missing = {
            field: message
            for field, message in Cell.REQUIRED_FIELDS.items()
            if current(field) in (None, "")
        }
        if missing:
            raise serializers.ValidationError(missing)

        if season and campus and season.church_id != campus.church_id:
            raise serializers.ValidationError(
                {"season_id": "A temporada precisa ser da mesma igreja do campus."}
            )
        if start and end and end < start:
            raise serializers.ValidationError(
                {"end_date": "A data de fim deve ser depois da data de início."}
            )
        if season:
            for field, value in (("start_date", start), ("end_date", end)):
                if value and not (season.start_date <= value <= season.end_date):
                    raise serializers.ValidationError(
                        {field: f"A data precisa estar dentro da temporada {season.name} "
                                f"({season.start_date:%d/%m/%Y} a {season.end_date:%d/%m/%Y})."}
                    )
        return attrs


class CellRenewInputSerializer(serializers.Serializer):
    """Payload for POST /cells/{id}/renew/: carry a cell over to another season."""

    season_id = serializers.PrimaryKeyRelatedField(queryset=Season.objects.all())
    # Copy the current members into the new cell (leaders are always copied).
    copy_members = serializers.BooleanField(default=True)


class CellVisitorMiniSerializer(serializers.ModelSerializer):
    """Name only — what a meeting shows about its guests (no contact data)."""

    class Meta:
        model = CellVisitor
        fields = ["id", "name", "status"]


class CellVisitorSerializer(serializers.ModelSerializer):
    """A cell's guest. Status changes only through the forward/make-member
    actions. visit_count / first_visit / last_visit come from the meetings the
    visitor is marked in (annotated by CellVisitorViewSet)."""

    cell_id = serializers.PrimaryKeyRelatedField(source="cell", queryset=Cell.objects.all(), write_only=True)
    person = PersonMiniSerializer(read_only=True)
    forwarded = serializers.SerializerMethodField()
    visit_count = serializers.IntegerField(read_only=True, default=0)
    first_visit = serializers.DateField(read_only=True, default=None)
    last_visit = serializers.DateField(read_only=True, default=None)

    class Meta:
        model = CellVisitor
        fields = [
            "id",
            "cell",
            "cell_id",
            "name",
            "phone",
            "email",
            "notes",
            "status",
            "person",
            "forwarded",
            "visit_count",
            "first_visit",
            "last_visit",
            "created_at",
        ]
        read_only_fields = ["cell", "status"]

    def get_forwarded(self, obj):
        return obj.reconciliation_lead_id is not None

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Informe o nome do visitante.")
        return value

    def validate(self, attrs):
        if self.instance and "cell" in attrs and attrs["cell"] != self.instance.cell:
            raise serializers.ValidationError({"cell_id": "Não é possível mover um visitante de célula."})
        return attrs


class MakeVisitorMemberInputSerializer(serializers.Serializer):
    """Either an existing person (person_id) or the e-mail to register the
    visitor as a new person (name and phone come from the visitor)."""

    person_id = serializers.PrimaryKeyRelatedField(queryset=Person.objects.all(), required=False)
    email = serializers.EmailField(required=False)

    def validate(self, attrs):
        if not attrs.get("person_id") and not attrs.get("email"):
            raise serializers.ValidationError(
                "Escolha uma pessoa já cadastrada ou informe o e-mail para cadastrar o visitante."
            )
        return attrs


class CellMeetingSerializer(serializers.ModelSerializer):
    cell_id = serializers.PrimaryKeyRelatedField(
        source="cell", queryset=Cell.objects.all(), write_only=True
    )
    attendees = PersonMiniSerializer(many=True, read_only=True)
    attendee_ids = serializers.PrimaryKeyRelatedField(
        source="attendees", queryset=Person.objects.all(), many=True, write_only=True, required=False
    )
    visitors = CellVisitorMiniSerializer(many=True, read_only=True)
    visitor_ids = serializers.PrimaryKeyRelatedField(
        source="visitors", queryset=CellVisitor.objects.all(), many=True, write_only=True, required=False
    )
    attendance_count = serializers.SerializerMethodField()
    created_by = PersonMiniSerializer(read_only=True)

    class Meta:
        model = CellMeeting
        fields = [
            "id",
            "cell",
            "cell_id",
            "date",
            "held",
            "cancel_reason",
            "topic",
            "notes",
            "decisions_count",
            "attendees",
            "attendee_ids",
            "visitors",
            "visitor_ids",
            "visitors_count",
            "attendance_count",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["cell"]

    def get_attendance_count(self, obj):
        # Registered people present, named guests and guests counted by number.
        return len(obj.attendees.all()) + len(obj.visitors.all()) + obj.visitors_count

    def validate(self, attrs):
        cell = attrs.get("cell") or self.instance.cell
        if self.instance and "cell" in attrs and attrs["cell"] != self.instance.cell:
            raise serializers.ValidationError({"cell_id": "Não é possível mover um encontro de célula."})
        if not self.instance and not cell.is_complete:
            raise serializers.ValidationError(
                {"cell_id": "A célula não está ativa: defina o dia, o horário e o público-alvo "
                            "antes de registrar encontros."}
            )

        date = attrs.get("date") or self.instance.date
        start, end = cell.effective_start, cell.effective_end
        if (start and date < start) or (end and date > end):
            raise serializers.ValidationError(
                {"date": "A data do encontro precisa estar dentro do período da célula."}
            )

        held = attrs["held"] if "held" in attrs else getattr(self.instance, "held", True)
        if not held:
            reason = attrs.get("cancel_reason", getattr(self.instance, "cancel_reason", "")).strip()
            if not reason:
                raise serializers.ValidationError(
                    {"cancel_reason": "Informe por que o encontro não aconteceu."}
                )
            # A meeting that didn't happen has no attendance.
            attrs.update(cancel_reason=reason, attendees=[], visitors=[], visitors_count=0, decisions_count=0)
            return attrs

        attrs["cancel_reason"] = ""
        if date > timezone.localdate():
            raise serializers.ValidationError(
                {"date": "A presença só pode ser registrada depois do encontro. "
                         "Para uma data futura, marque que o encontro não vai acontecer."}
            )

        if "attendees" in attrs:
            allowed = set(cell.members.values_list("id", flat=True)) | set(
                cell.leaderships.values_list("person_id", flat=True)
            )
            outsiders = [p.name for p in attrs["attendees"] if p.id not in allowed]
            if outsiders:
                raise serializers.ValidationError(
                    {"attendee_ids": "Só membros e líderes da célula podem ser marcados como presentes "
                                     f"(adicione-os à célula primeiro): {', '.join(outsiders)}. "
                                     "Visitantes entram em visitor_ids ou visitors_count."}
                )
        if "visitors" in attrs:
            strangers = [v.name for v in attrs["visitors"] if v.cell_id != cell.pk]
            if strangers:
                raise serializers.ValidationError(
                    {"visitor_ids": f"Estes visitantes são de outra célula: {', '.join(strangers)}."}
                )
        return attrs


class TeamLeadershipSerializer(serializers.ModelSerializer):
    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = TeamLeadership
        fields = ["id", "team", "person", "role"]


class TeamLeadershipInputSerializer(serializers.Serializer):
    person_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=LeadershipRole.choices)


class TeamMemberSerializer(serializers.Serializer):
    """Input payload for adding a participant to a team — must already be a member
    of the team's parent ministry (validated in the view)."""

    person_id = serializers.UUIDField()


class TeamSerializer(serializers.ModelSerializer):
    ministry = MinistryMiniSerializer(read_only=True)
    ministry_id = serializers.PrimaryKeyRelatedField(
        source="ministry", queryset=Ministry.objects.all(), write_only=True
    )
    leaders = serializers.SerializerMethodField()
    co_leaders = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(source="members.count", read_only=True)
    total_points = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = [
            "id",
            "ministry",
            "ministry_id",
            "name",
            "description",
            "leaders",
            "co_leaders",
            "member_count",
            "total_points",
        ]

    def get_leaders(self, obj):
        return PersonMiniSerializer(obj.leaders(), many=True).data

    def get_co_leaders(self, obj):
        return PersonMiniSerializer(obj.co_leaders(), many=True).data

    def get_total_points(self, obj):
        # Team-level points only — collective achievements, not a sum of members'
        # personal points (see TeamPointAward docstring).
        return obj.point_awards.aggregate(total=Sum("points"))["total"] or 0


class TeamPointAwardSerializer(serializers.ModelSerializer):
    awarded_by = PersonMiniSerializer(read_only=True)

    class Meta:
        model = TeamPointAward
        fields = ["id", "team", "points", "reason", "awarded_by", "awarded_at"]
        read_only_fields = ["team", "awarded_by", "awarded_at"]


class TeamPointAwardInputSerializer(serializers.Serializer):
    points = serializers.IntegerField(min_value=1)
    reason = serializers.CharField(max_length=255)


class ActivitySerializer(serializers.ModelSerializer):
    team = serializers.PrimaryKeyRelatedField(read_only=True)
    team_id = serializers.PrimaryKeyRelatedField(
        source="team", queryset=Team.objects.all(), write_only=True
    )
    created_by = PersonMiniSerializer(read_only=True)

    class Meta:
        model = Activity
        fields = [
            "id",
            "team",
            "team_id",
            "title",
            "description",
            "kind",
            "points",
            "is_active",
            "created_by",
            "created_at",
        ]
        read_only_fields = ["is_active", "created_by", "created_at"]


class ActivityCompletionSerializer(serializers.ModelSerializer):
    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = ActivityCompletion
        fields = ["id", "activity", "person", "date", "points_awarded", "completed_at"]


class ActivityCompletionInputSerializer(serializers.Serializer):
    """person_id is optional — omit it to mark your own completion; a team
    leader/co-leader may pass it to mark completion on behalf of a member."""

    person_id = serializers.UUIDField(required=False)
    date = serializers.DateField(required=False)


class MinistryGeneralLeadershipSerializer(serializers.ModelSerializer):
    """Read-only representation; leaders are chosen through
    MinistryGeneralLeadershipViewSet.add_leader/remove_leader (manager-only)."""

    leaders = PersonMiniSerializer(many=True, read_only=True)

    class Meta:
        model = MinistryGeneralLeadership
        fields = ["id", "kind", "leaders"]
