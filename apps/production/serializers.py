from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import serializers

from apps.churches.choices import PRODUCTION_MINISTRY_KINDS, MinistryKind
from apps.churches.models import Campus, Ministry
from apps.payments.models import EventTicketConfig
from apps.payments.serializers import EventTicketInputSerializer, validate_ticket_rules

from .choices import ItemStatus, RosterStatus, SwapStatus
from .models import (
    Demand,
    Event,
    EventChecklistItem,
    EventRosterEntry,
    RosterSwapRequest,
    RundownItem,
    RundownItemStatus,
)
from .permissions import can_manage_demand_source, can_manage_demand_target, can_manage_event

Person = get_user_model()


class ProductionCampusMiniSerializer(serializers.ModelSerializer):
    """Local, read-only — same rationale as ReconciliationCampusMiniSerializer:
    avoids a cross-app import and a component name clash in the schema."""

    class Meta:
        model = Campus
        fields = ["id", "name", "city"]


class ProductionMinistryMiniSerializer(serializers.ModelSerializer):
    """Local, read-only — same rationale as ProductionCampusMiniSerializer."""

    class Meta:
        model = Ministry
        fields = ["id", "name", "kind"]


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


class ProducerMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = ["id", "name"]


class EventSerializer(serializers.ModelSerializer):
    campus = ProductionCampusMiniSerializer(read_only=True)
    campus_id = serializers.PrimaryKeyRelatedField(
        queryset=Campus.objects.all(), source="campus", write_only=True, required=False, allow_null=True
    )
    producer = ProducerMiniSerializer(read_only=True)
    producer_id = serializers.PrimaryKeyRelatedField(
        queryset=Person.objects.all(), source="producer", write_only=True, required=False, allow_null=True
    )
    rundown_items = RundownItemSerializer(many=True, read_only=True)
    # Optional on create: opens registrations together with the event, in one
    # transaction. Later changes go through /payments/ticket-configs/.
    ticket = EventTicketInputSerializer(write_only=True, required=False, allow_null=True)
    # Read-only summary for lists and cards (price badge, capacity).
    ticket_config = serializers.SerializerMethodField()
    # Edit the event, its rundown, ticket and attendee list (can_manage_event).
    can_manage = serializers.SerializerMethodField()
    # Check people in at the door: managers plus the confirmed roster.
    can_check_in = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            "id",
            "title",
            "description",
            "event_type",
            "campus",
            "campus_id",
            "ministry_kind",
            "starts_at",
            "ends_at",
            "status",
            "producer",
            "producer_id",
            "rundown_items",
            "ticket",
            "ticket_config",
            "can_manage",
            "can_check_in",
            "created_at",
        ]
        read_only_fields = ["created_at"]

    def get_can_manage(self, obj):
        request = self.context.get("request")
        return bool(request) and can_manage_event(request.user, obj)

    def get_can_check_in(self, obj):
        from apps.payments.checkin import can_check_in  # payments depends on production

        request = self.context.get("request")
        return bool(request) and can_check_in(request.user, obj)

    def get_ticket_config(self, obj):
        try:
            config = obj.ticket_config
        except EventTicketConfig.DoesNotExist:
            return None
        return {
            "id": str(config.id),
            "price_cents": config.price_cents,
            "max_capacity": config.max_capacity,
            "registration_deadline": config.registration_deadline,
        }

    def validate(self, attrs):
        attrs = super().validate(attrs)
        ticket = attrs.get("ticket")
        if ticket is not None:
            if self.instance is not None:
                raise serializers.ValidationError(
                    {"ticket": "Para alterar o ingresso, use a seção Ingresso do evento."}
                )
            try:
                validate_ticket_rules(ticket, attrs.get("starts_at"))
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({"ticket": exc.detail})
        return attrs

    def create(self, validated_data):
        ticket = validated_data.pop("ticket", None)
        with transaction.atomic():
            event = super().create(validated_data)
            if ticket is not None:
                EventTicketConfig.objects.create(event=event, **ticket)
        return event

    def update(self, instance, validated_data):
        validated_data.pop("ticket", None)
        return super().update(instance, validated_data)


class EventMiniSerializer(serializers.ModelSerializer):
    """Slim view of Event used in Demand.event — avoids serializing the whole
    rundown every time a demand is listed."""

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
    can_answer = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    event_id = serializers.PrimaryKeyRelatedField(
        queryset=Event.objects.all(), source="event", write_only=True, required=False, allow_null=True
    )
    from_ministry_kind = serializers.ChoiceField(
        choices=MinistryKind.choices, required=False, allow_null=True, allow_blank=True
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
            "can_answer",
            "can_edit",
        ]
        read_only_fields = ["status", "rejection_reason", "created_by", "created_at", "updated_at"]

    def _user(self):
        request = self.context.get("request")
        return request.user if request else None

    def get_can_answer(self, obj):
        """Accept / complete / reject: the side that received the demand."""
        user = self._user()
        return bool(user) and can_manage_demand_target(user, obj)

    def get_can_edit(self, obj):
        """Edit / cancel while open: the side that opened it."""
        user = self._user()
        return bool(user) and can_manage_demand_source(user, obj)

    def validate_to_ministry_kind(self, value):
        if value not in PRODUCTION_MINISTRY_KINDS:
            raise serializers.ValidationError("Demandas só podem ser endereçadas a um ministério técnico/produção.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        from_kind = attrs.get("from_ministry_kind")
        if not from_kind:
            attrs["from_ministry_kind"] = None
            if not attrs.get("event"):
                raise serializers.ValidationError(
                    {"event_id": "Evento é obrigatório quando from_ministry_kind não é informado."}
                )
        return attrs


class RejectDemandInputSerializer(serializers.Serializer):
    rejection_reason = serializers.CharField(allow_blank=True, required=False, default="")


class EventRosterEntrySerializer(serializers.ModelSerializer):
    ministry = ProductionMinistryMiniSerializer(read_only=True)
    ministry_id = serializers.PrimaryKeyRelatedField(
        queryset=Ministry.objects.all(), source="ministry", write_only=True
    )
    person = DemandPersonMiniSerializer(read_only=True)
    person_id = serializers.PrimaryKeyRelatedField(
        queryset=Person.objects.all(), source="person", write_only=True
    )
    event_id = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all(), source="event", write_only=True)
    event = EventMiniSerializer(read_only=True)
    conflicts = serializers.SerializerMethodField()
    pending_swap = serializers.SerializerMethodField()

    class Meta:
        model = EventRosterEntry
        fields = [
            "id",
            "event",
            "event_id",
            "ministry",
            "ministry_id",
            "person",
            "person_id",
            "function",
            "status",
            "responded_at",
            "created_at",
            "conflicts",
            "pending_swap",
        ]
        read_only_fields = ["status", "responded_at", "created_at"]

    def get_conflicts(self, entry):
        """Other events at the same time where the person serves (a warning)."""
        from .roster import conflicts

        if entry.status == RosterStatus.DECLINED:
            return []
        found = conflicts(entry.person_id, entry.event)
        return [{"id": e.pk, "title": e.title, "starts_at": e.starts_at} for e in found]

    def get_pending_swap(self, entry):
        swap = next((s for s in entry.swap_requests.all() if s.status == SwapStatus.PENDING), None)
        if swap is None:
            return None
        return {"id": swap.pk, "substitute": {"id": swap.substitute_id, "name": swap.substitute.name}}


class RosterSwapEntrySerializer(serializers.ModelSerializer):
    event = EventMiniSerializer(read_only=True)
    ministry = ProductionMinistryMiniSerializer(read_only=True)

    class Meta:
        model = EventRosterEntry
        fields = ["id", "event", "ministry", "function"]


class RosterSwapSerializer(serializers.ModelSerializer):
    entry = RosterSwapEntrySerializer(read_only=True)
    requested_by = DemandPersonMiniSerializer(read_only=True)
    substitute = DemandPersonMiniSerializer(read_only=True)

    class Meta:
        model = RosterSwapRequest
        fields = ["id", "entry", "requested_by", "substitute", "message", "status", "responded_at", "created_at"]
        read_only_fields = fields


class SwapInputSerializer(serializers.Serializer):
    substitute_id = serializers.PrimaryKeyRelatedField(queryset=Person.objects.filter(is_active=True))
    message = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class EventChecklistItemSerializer(serializers.ModelSerializer):
    done_by = DemandPersonMiniSerializer(read_only=True)
    event_id = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all(), source="event", write_only=True)

    class Meta:
        model = EventChecklistItem
        fields = ["id", "event_id", "ministry_kind", "text", "is_done", "done_by", "done_at", "created_at"]
        read_only_fields = ["is_done", "done_by", "done_at", "created_at"]
