from django.db.models import Sum
from rest_framework import serializers

from apps.churches.models import ActivityCompletion, Campus, Ministry, Team
from apps.churches.permissions import general_led_kinds, led_ministries

from .models import Person


def personal_total_points(person) -> int:
    return (
        ActivityCompletion.objects.filter(person=person).aggregate(total=Sum("points_awarded"))[
            "total"
        ]
        or 0
    )


class CampusMiniSerializer(serializers.ModelSerializer):
    """Small, local read-only view of a Campus — defined here (instead of importing
    apps.churches.serializers) to avoid a people<->churches serializer import cycle."""

    class Meta:
        model = Campus
        fields = ["id", "name", "city"]


class MinistryMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ministry
        fields = ["id", "name", "kind"]


class TeamMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ["id", "name", "ministry"]


class PersonSerializer(serializers.ModelSerializer):
    campus = CampusMiniSerializer(read_only=True)
    campus_id = serializers.PrimaryKeyRelatedField(
        source="campus", queryset=Campus.objects.all(), write_only=True, required=False, allow_null=True
    )
    password = serializers.CharField(write_only=True, required=False, style={"input_type": "password"})
    total_points = serializers.SerializerMethodField()

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
        ]
        read_only_fields = ["is_manager"]

    def get_total_points(self, obj):
        return personal_total_points(obj)

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        if not password:
            raise serializers.ValidationError({"password": "Obrigatório ao cadastrar uma pessoa."})
        return Person.objects.create_user(password=password, **validated_data)

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        person = super().update(instance, validated_data)
        if password:
            person.set_password(password)
            person.save(update_fields=["password"])
        return person


class MeSerializer(serializers.ModelSerializer):
    campus = CampusMiniSerializer(read_only=True)
    led_ministries = serializers.SerializerMethodField()
    general_led_kinds = serializers.SerializerMethodField()
    led_teams = serializers.SerializerMethodField()
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
            "total_points",
        ]

    def get_led_ministries(self, obj):
        return MinistryMiniSerializer(led_ministries(obj), many=True).data

    def get_general_led_kinds(self, obj):
        return sorted(general_led_kinds(obj))

    def get_led_teams(self, obj):
        return TeamMiniSerializer(Team.objects.filter(leaderships__person=obj).distinct(), many=True).data

    def get_total_points(self, obj):
        return personal_total_points(obj)
