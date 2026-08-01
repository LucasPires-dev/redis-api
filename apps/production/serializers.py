from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.churches.choices import PRODUCTION_MINISTRY_KINDS, MinistryKind
from apps.churches.models import Campus

from .choices import ItemStatus
from .models import Demand, Event, RundownItem, RundownItemStatus

Person = get_user_model()


class ProductionCampusMiniSerializer(serializers.ModelSerializer):
    """Local, read-only — mesmo racional de ReconciliationCampusMiniSerializer:
    evita import cruzado e colisão de nome de componente no schema."""

    class Meta:
        model = Campus
        fields = ["id", "name", "city"]


class RundownItemStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = RundownItemStatus
        fields = ["ministry_kind", "status", "updated_at"]
        read_only_fields = fields


class RundownItemSerializer(serializers.ModelSerializer):
    statuses = RundownItemStatusSerializer(many=True, read_only=True)
    event_id = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all(), source="event", write_only=True)

    class Meta:
        model = RundownItem
        fields = [
            "id",
            "event_id",
            "order",
            "title",
            "scheduled_time",
            "duration_minutes",
            "ministry_kind",
            "notes",
            "statuses",
        ]


class SetItemStatusInputSerializer(serializers.Serializer):
    ministry_kind = serializers.ChoiceField(choices=MinistryKind.choices)
    status = serializers.ChoiceField(choices=ItemStatus.choices)


class EventSerializer(serializers.ModelSerializer):
    campus = ProductionCampusMiniSerializer(read_only=True)
    campus_id = serializers.PrimaryKeyRelatedField(
        queryset=Campus.objects.all(), source="campus", write_only=True, required=False, allow_null=True
    )
    rundown_items = RundownItemSerializer(many=True, read_only=True)

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "event_type",
            "campus",
            "campus_id",
            "ministry_kind",
            "starts_at",
            "status",
            "rundown_items",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class EventMiniSerializer(serializers.ModelSerializer):
    """Vista enxuta de Event usada em Demand.event — evita serializar o rundown
    inteiro toda vez que uma demanda é listada."""

    campus = ProductionCampusMiniSerializer(read_only=True)

    class Meta:
        model = Event
        fields = ["id", "title", "event_type", "campus", "ministry_kind", "starts_at", "status"]


class DemandPersonMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = ["id", "name", "email"]


class DemandSerializer(serializers.ModelSerializer):
    event = EventMiniSerializer(read_only=True)
    event_id = serializers.PrimaryKeyRelatedField(
        queryset=Event.objects.all(), source="event", write_only=True, required=False, allow_null=True
    )
    from_campus = ProductionCampusMiniSerializer(read_only=True)
    from_campus_id = serializers.PrimaryKeyRelatedField(
        queryset=Campus.objects.all(), source="from_campus", write_only=True, required=False, allow_null=True
    )
    to_campus = ProductionCampusMiniSerializer(read_only=True)
    to_campus_id = serializers.PrimaryKeyRelatedField(
        queryset=Campus.objects.all(), source="to_campus", write_only=True, required=False, allow_null=True
    )
    created_by = DemandPersonMiniSerializer(read_only=True)

    class Meta:
        model = Demand
        fields = [
            "id",
            "event",
            "event_id",
            "from_ministry_kind",
            "from_campus",
            "from_campus_id",
            "to_ministry_kind",
            "to_campus",
            "to_campus_id",
            "demand_type",
            "priority",
            "description",
            "due_date",
            "status",
            "rejection_reason",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["status", "rejection_reason", "created_by", "created_at", "updated_at"]

    def validate_to_ministry_kind(self, value):
        if value not in PRODUCTION_MINISTRY_KINDS:
            raise serializers.ValidationError("Demandas só podem ser endereçadas a um ministério técnico/produção.")
        return value


class RejectDemandInputSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(allow_blank=True, required=False, default="")
