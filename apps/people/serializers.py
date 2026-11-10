from django.db.models import Sum
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.churches.choices import MinistryKind
from apps.churches.models import ActivityCompletion, Campus, Cell, Ministry, Team
from apps.churches.permissions import (
    general_led_kinds,
    is_cells_general_leader,
    led_campuses,
    led_campuses_cells,
    led_ministries,
)
from apps.courses.permissions import is_courses_general_leader
from apps.production.permissions import is_any_event_producer, producer_campuses

from .models import Person


def personal_total_points(person) -> int:
    """Total points the person earned by completing activities.

    Uses `points_awarded` (the points recorded at completion time), not the
    activity's current points: if the activity is edited later, the person's
    history doesn't change. Returns 0 when the person has completed nothing
    (`aggregate` returns None in that case).

    Note: runs one query per person. In list endpoints that becomes N queries.
    """
    return (
        ActivityCompletion.objects.filter(person=person).aggregate(total=Sum("points_awarded"))[
            "total"
        ]
        or 0
    )


# --- "Mini" serializers (read-only) -------------------------------------------
#
# Compact versions of churches models, used to nest data inside Person without
# returning the full object. They live here, instead of being imported from
# apps.churches.serializers, to avoid a people <-> churches import cycle.


class CampusMiniSerializer(serializers.ModelSerializer):
    """Campus summary: id, name and city."""

    class Meta:
        model = Campus
        fields = ["id", "name", "city"]


class MinistryMiniSerializer(serializers.ModelSerializer):
    """Ministry summary. `kind` is the ministry type (MinistryKind), e.g. Jovens, Reconciliação."""

    class Meta:
        model = Ministry
        fields = ["id", "name", "kind"]


class TeamMiniSerializer(serializers.ModelSerializer):
    """Team summary. `ministry` is returned as the ministry id only."""

    class Meta:
        model = Team
        fields = ["id", "name", "ministry"]


class CellMiniSerializer(serializers.ModelSerializer):
    """Cell summary. `campus` is returned as the campus id only."""

    class Meta:
        model = Cell
        fields = ["id", "name", "campus"]


# --- Person ------------------------------------------------------------------


class PersonSerializer(serializers.ModelSerializer):
    """People CRUD (used by PersonViewSet at /api/people/).

    Who can see or edit each person is decided by PersonPermission and the
    viewset's `get_queryset`, not here.

    Campus read vs. write:
    - `campus`    → read: the campus comes nested ({id, name, city}).
    - `campus_id` → write: the client sends only the campus UUID. It can be
                    omitted or sent as null (person without a campus).
    """

    # Output: nested campus. Not writable.
    campus = CampusMiniSerializer(read_only=True)

    # Input: campus UUID, written to the model's `campus` field (source="campus").
    campus_id = serializers.PrimaryKeyRelatedField(
        source="campus", queryset=Campus.objects.all(), write_only=True, required=False, allow_null=True
    )

    # Never returned in responses. Optional in the schema because on update
    # the password only changes if sent; on create it is required in `create`.
    password = serializers.CharField(write_only=True, required=False, style={"input_type": "password"})

    # Computed: total activity points (see personal_total_points).
    total_points = serializers.SerializerMethodField()

    # Ministries the person is a member of (Ministry.members). Read-only:
    # ministry membership is managed by the churches app.
    ministries = MinistryMiniSerializer(many=True, read_only=True)

    class Meta:
        model = Person
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "campus",
            "campus_id",
            "baptized",
            "baptized_at",
            "joined_at",
            "is_manager",
            "password",
            "total_points",
            "ministries",
        ]
        # The API never lets anyone make someone a manager. That is done
        # through the Django Admin or management commands.
        read_only_fields = ["is_manager"]

    def get_total_points(self, obj):
        return personal_total_points(obj)

    def validate(self, attrs):
        """Email and password are login credentials: on update, only the person
        themselves may change them through the API. Otherwise anyone allowed to
        edit a person (ministry/campus leader, reconciliador) could take over
        their account. A manager who needs to reset them uses the Django Admin.

        Resending the current email unchanged is allowed, so a full-form PUT
        doesn't fail. Registration (create) is not affected."""
        request = self.context.get("request")
        if self.instance is not None and request and request.user.pk != self.instance.pk:
            email_changed = "email" in attrs and attrs["email"] != self.instance.email
            if email_changed or attrs.get("password"):
                raise PermissionDenied("Apenas a própria pessoa pode alterar o e-mail e a senha.")
        return attrs

    def create(self, validated_data):
        """Creates the person and, with it, their access account. Password is required.

        Uses `create_user` (instead of the default `create`) so the email is
        normalized and the password is hashed.
        """
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "Obrigatório ao cadastrar uma pessoa."})
        return Person.objects.create_user(password=password, **validated_data)

    def update(self, instance, validated_data):
        """Updates the person. The password only changes if it is in the payload.

        The password is popped from `validated_data` before the default update
        so it is never saved as plain text; it is then applied with
        `set_password` (hashed).
        """
        password = validated_data.pop("password", None)
        person = super().update(instance, validated_data)
        if password:
            person.set_password(password)
            person.save(update_fields=["password"])
        return person


class MeSerializer(serializers.ModelSerializer):
    """Profile of the logged-in user (used by MeView at /api/auth/me/). Read-only.

    Besides the basic data, it reports what the person leads. The frontend uses
    this to build the navigation and decide which screens and actions to show.
    Leaderships count for both leader and co-leader (same access level).
    """

    campus = CampusMiniSerializer(read_only=True)

    # Ministries where the person is leader or co-leader (MinistryLeadership).
    led_ministries = serializers.SerializerMethodField()

    # Ministry types (MinistryKind) the person leads across every campus, as
    # "general leader" (MinistryGeneralLeadership). E.g. ["jovens"].
    general_led_kinds = serializers.SerializerMethodField()

    # Teams where the person holds a leadership (TeamLeadership).
    led_teams = serializers.SerializerMethodField()

    # Cells where the person holds a leadership (CellLeadership).
    led_cells = serializers.SerializerMethodField()

    # Campuses where the person is leader or co-leader (CampusLeadership).
    led_campuses = serializers.SerializerMethodField()

    # General cells leader (CellGeneralLeadership): defines seasons
    # and manages every cell.
    is_cells_general_leader = serializers.SerializerMethodField()

    # Campuses whose cells the person is in charge of (CampusCellsLeadership).
    led_campuses_cells = serializers.SerializerMethodField()

    # General courses leader (CourseGeneralLeadership): manages every course on every campus.
    is_courses_general_leader = serializers.SerializerMethodField()

    # Campuses whose courses the person is in charge of (CampusCoursesLeadership).
    led_campuses_courses = serializers.SerializerMethodField()

    # Whether the person serves in the Reconciliação ministry (see get_is_reconciliador).
    is_reconciliador = serializers.SerializerMethodField()

    # Produtor de Eventos (EventProducerAssignment).
    is_event_producer = serializers.SerializerMethodField()
    producer_campuses = serializers.SerializerMethodField()

    # Total activity points (see personal_total_points).
    total_points = serializers.SerializerMethodField()

    class Meta:
        model = Person
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "campus",
            "baptized",
            "is_manager",
            "led_ministries",
            "general_led_kinds",
            "led_teams",
            "led_cells",
            "led_campuses",
            "is_cells_general_leader",
            "led_campuses_cells",
            "is_courses_general_leader",
            "led_campuses_courses",
            "is_reconciliador",
            "is_event_producer",
            "producer_campuses",
            "total_points",
        ]

    def get_led_ministries(self, obj):
        return MinistryMiniSerializer(led_ministries(obj), many=True).data

    def get_general_led_kinds(self, obj):
        # Comes back as a set; sorted so the response order is stable.
        return sorted(general_led_kinds(obj))

    def get_led_teams(self, obj):
        # distinct(): guards against the leaderships join returning the same
        # team more than once.
        return TeamMiniSerializer(Team.objects.filter(leaderships__person=obj).distinct(), many=True).data

    def get_led_cells(self, obj):
        # distinct() for the same reason as get_led_teams.
        return CellMiniSerializer(Cell.objects.filter(leaderships__person=obj).distinct(), many=True).data

    def get_led_campuses(self, obj):
        return CampusMiniSerializer(led_campuses(obj), many=True).data

    def get_is_cells_general_leader(self, obj):
        return is_cells_general_leader(obj)

    def get_led_campuses_cells(self, obj):
        return CampusMiniSerializer(led_campuses_cells(obj), many=True).data

    def get_is_courses_general_leader(self, obj):
        return is_courses_general_leader(obj)

    def get_led_campuses_courses(self, obj):
        return CampusMiniSerializer(Campus.objects.filter(courses_leaderships__person=obj).distinct(), many=True).data

    def get_is_reconciliador(self, obj):
        return Ministry.objects.filter(kind=MinistryKind.RECONCILIACAO, members=obj).exists()

    def get_is_event_producer(self, obj):
        return is_any_event_producer(obj)

    def get_producer_campuses(self, obj):
        assignments = producer_campuses(obj)
        if not assignments.exists():
            return []
        if assignments.filter(campus__isnull=True).exists():
            return None  # null = church-wide (all campuses)
        return CampusMiniSerializer(
            Campus.objects.filter(event_producers__person=obj), many=True
        ).data

    def get_total_points(self, obj):
        return personal_total_points(obj)
