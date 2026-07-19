from django.contrib.auth import get_user_model
from django.db.models import Sum
from rest_framework import serializers

from .choices import LeadershipRole
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

    class Meta:
        model = Campus
        fields = ["id", "church", "name", "city"]


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
    """Read-only for now — general leadership is assigned via admin/seed in this pass,
    same as Church/Campus creation (see docs: only local ministry leadership has a
    write endpoint right now)."""

    leaders = PersonMiniSerializer(many=True, read_only=True)

    class Meta:
        model = MinistryGeneralLeadership
        fields = ["id", "kind", "leaders"]
