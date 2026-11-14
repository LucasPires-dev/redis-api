from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.people.cpf import CpfField
from apps.production.models import Event

from .models import ACTIVE_STATUSES, EventRegistration, EventTicketConfig

Person = get_user_model()

# Asaas refuses charges under R$ 5,00, so a paid ticket below that could never
# be paid. 0 means free (registration only, no charge).
MIN_PAID_PRICE_CENTS = 500


def validate_ticket_rules(attrs, starts_at, *, instance=None):
    """Business rules shared by the ticket config endpoint and the ticket sent
    together with a new event. `attrs` may be partial (PATCH): missing values
    fall back to `instance`."""

    def value(name):
        if name in attrs:
            return attrs[name]
        return getattr(instance, name, None)

    errors = {}
    price = value("price_cents") or 0
    if 0 < price < MIN_PAID_PRICE_CENTS:
        errors["price_cents"] = "O valor mínimo de um ingresso pago é R$ 5,00 (ou 0 para gratuito)."

    capacity = value("max_capacity")
    if capacity is not None:
        if capacity < 1:
            errors["max_capacity"] = "Informe pelo menos 1 vaga, ou deixe em branco para ilimitado."
        elif instance is not None:
            taken = EventRegistration.objects.filter(
                event_id=instance.event_id, payment_status__in=ACTIVE_STATUSES
            ).count()
            if capacity < taken:
                errors["max_capacity"] = f"Já existem {taken} inscrições ativas; o limite não pode ser menor."

    deadline = value("registration_deadline")
    if deadline and starts_at and deadline > starts_at:
        errors["registration_deadline"] = "O prazo de inscrição precisa terminar até o início do evento."

    if errors:
        raise serializers.ValidationError(errors)
    return attrs


class EventTicketInputSerializer(serializers.Serializer):
    """Ticket sent together with a new event (POST /production-events/ `ticket`),
    so both are created at once."""

    price_cents = serializers.IntegerField(min_value=0)
    max_capacity = serializers.IntegerField(required=False, allow_null=True)
    registration_deadline = serializers.DateTimeField(required=False, allow_null=True)
    description = serializers.CharField(required=False, allow_blank=True, default="")


class EventTicketConfigSerializer(serializers.ModelSerializer):
    event_id = serializers.PrimaryKeyRelatedField(
        queryset=Event.objects.all(), source="event", write_only=True
    )

    class Meta:
        model = EventTicketConfig
        fields = [
            "id",
            "event_id",
            "price_cents",
            "max_capacity",
            "registration_deadline",
            "description",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def validate_event_id(self, event):
        # Permission is checked against the config's current event, so moving it to
        # another event would bypass that check (onto an event the user doesn't manage).
        if self.instance is not None and event.pk != self.instance.event_id:
            raise serializers.ValidationError("Não é possível mover o ingresso para outro evento.")
        return event

    def validate(self, attrs):
        event = attrs.get("event") or getattr(self.instance, "event", None)
        return validate_ticket_rules(
            attrs, event.starts_at if event else None, instance=self.instance
        )


class RegistrationPersonMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = ["id", "name", "email"]


class RegistrationEventMiniSerializer(serializers.ModelSerializer):
    """Enough of the event to list a person's own registrations."""

    campus_name = serializers.CharField(source="campus.name", default=None, read_only=True)

    class Meta:
        model = Event
        fields = ["id", "title", "event_type", "starts_at", "status", "campus_name"]


class EventRegistrationSerializer(serializers.ModelSerializer):
    person = RegistrationPersonMiniSerializer(read_only=True)
    event = RegistrationEventMiniSerializer(read_only=True)
    event_id = serializers.PrimaryKeyRelatedField(
        queryset=Event.objects.all(), source="event", write_only=True
    )
    # Asked when the person has none on file: Asaas needs it to charge.
    cpf = CpfField(write_only=True)

    class Meta:
        model = EventRegistration
        fields = [
            "id",
            "event_id",
            "event",
            "cpf",
            "person",
            "payment_status",
            "amount_cents",
            "payment_url",
            "paid_at",
            "ticket_code",
            "checked_in_at",
            "created_at",
        ]
        read_only_fields = [
            "payment_status", "amount_cents", "payment_url", "paid_at", "ticket_code", "checked_in_at", "created_at",
        ]


class CheckinInputSerializer(serializers.Serializer):
    """The ticket code (typed or read from the QR code) or, when found by name,
    the registration id."""

    event_id = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all(), source="event")
    code = serializers.CharField(required=False, allow_blank=True, default="")
    registration_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if not attrs["code"].strip() and not attrs["registration_id"]:
            raise serializers.ValidationError({"code": "Informe o código do ingresso."})
        return attrs


class CheckinRowSerializer(serializers.ModelSerializer):
    """One line of the door list (no ticket code: it's the person's credential)."""

    person = RegistrationPersonMiniSerializer(read_only=True)

    class Meta:
        model = EventRegistration
        fields = ["id", "person", "payment_status", "checked_in_at"]


class RegistrationStatsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    confirmed = serializers.IntegerField()
    checked_in = serializers.IntegerField()
    pending = serializers.IntegerField()
    cancelled = serializers.IntegerField()
    capacity = serializers.IntegerField(allow_null=True)
    revenue_cents = serializers.IntegerField()
