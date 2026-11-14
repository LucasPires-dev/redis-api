from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from apps.people.cpf import CpfField
from apps.production.models import Event

from . import waitlist
from .models import (
    EventRegistration,
    EventRegistrationGuest,
    EventTicketConfig,
    EventTicketType,
    EventWaitlistEntry,
)

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
            taken = waitlist.seats_taken(instance.event)
            if capacity < taken:
                errors["max_capacity"] = f"Já existem {taken} vagas ocupadas; o limite não pode ser menor."

    companions = value("max_companions")
    if companions is not None and companions > 10:
        errors["max_companions"] = "No máximo 10 acompanhantes por inscrição."

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
    max_companions = serializers.IntegerField(required=False, min_value=0, default=0)
    is_public = serializers.BooleanField(required=False, default=False)


class EventTicketTypeSerializer(serializers.ModelSerializer):
    """A ticket type or batch. `sold` (places taken) and `sold_out` are counted
    for the event page and the organizers."""

    config_id = serializers.PrimaryKeyRelatedField(
        queryset=EventTicketConfig.objects.all(), source="config", write_only=True
    )
    on_sale = serializers.SerializerMethodField()
    sold = serializers.SerializerMethodField()
    sold_out = serializers.SerializerMethodField()

    class Meta:
        model = EventTicketType
        fields = [
            "id",
            "config_id",
            "name",
            "price_cents",
            "capacity",
            "sales_start",
            "sales_end",
            "order",
            "on_sale",
            "sold",
            "sold_out",
        ]

    def get_on_sale(self, obj):
        return obj.on_sale(timezone.now())

    def get_sold(self, obj):
        return waitlist.type_taken(obj)

    def get_sold_out(self, obj):
        return obj.capacity is not None and waitlist.type_taken(obj) >= obj.capacity

    def validate_config_id(self, config):
        if self.instance is not None and config.pk != self.instance.config_id:
            raise serializers.ValidationError("Não é possível mover o ingresso para outro evento.")
        return config

    def validate(self, attrs):
        def value(name):
            return attrs[name] if name in attrs else getattr(self.instance, name, None)

        errors = {}
        price = value("price_cents") or 0
        if 0 < price < MIN_PAID_PRICE_CENTS:
            errors["price_cents"] = "O valor mínimo de um ingresso pago é R$ 5,00 (ou 0 para gratuito)."
        capacity = value("capacity")
        if capacity is not None:
            if capacity < 1:
                errors["capacity"] = "Informe pelo menos 1 vaga, ou deixe em branco."
            elif self.instance is not None and capacity < waitlist.type_taken(self.instance):
                errors["capacity"] = "Já há mais inscrições neste ingresso do que esse limite."
        start, end = value("sales_start"), value("sales_end")
        if start and end and end <= start:
            errors["sales_end"] = "O fim das vendas precisa ser depois do início."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


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
            "max_companions",
            "is_public",
            "types",
            "free_seats",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    types = EventTicketTypeSerializer(many=True, read_only=True)
    free_seats = serializers.SerializerMethodField()

    def get_free_seats(self, obj):
        """Places still open (None: no limit). Counts the viewer's own offer as theirs."""
        request = self.context.get("request")
        person = request.user if request and request.user.is_authenticated else None
        return waitlist.free_seats(obj.event, for_person=person)

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


class RegistrationGuestSerializer(serializers.ModelSerializer):
    class Meta:
        model = EventRegistrationGuest
        fields = ["id", "name", "ticket_code", "checked_in_at"]
        read_only_fields = fields


class EventRegistrationSerializer(serializers.ModelSerializer):
    person = RegistrationPersonMiniSerializer(read_only=True)
    event = RegistrationEventMiniSerializer(read_only=True)
    event_id = serializers.PrimaryKeyRelatedField(
        queryset=Event.objects.all(), source="event", write_only=True
    )
    # Asked when the person has none on file: Asaas needs it to charge.
    cpf = CpfField(write_only=True)
    ticket_type_id = serializers.PrimaryKeyRelatedField(
        queryset=EventTicketType.objects.all(), source="ticket_type", write_only=True, required=False,
        allow_null=True,
    )
    ticket_type = serializers.SerializerMethodField()
    # Companions' names, one ticket each.
    guest_names = serializers.ListField(
        child=serializers.CharField(max_length=150), write_only=True, required=False, default=list
    )
    guests = RegistrationGuestSerializer(many=True, read_only=True)

    def get_ticket_type(self, obj):
        return {"id": obj.ticket_type_id, "name": obj.ticket_type.name} if obj.ticket_type_id else None

    def validate_guest_names(self, names):
        names = [n.strip() for n in names]
        if any(not n for n in names):
            raise serializers.ValidationError("Informe o nome de cada acompanhante.")
        return names

    class Meta:
        model = EventRegistration
        fields = [
            "id",
            "event_id",
            "event",
            "cpf",
            "person",
            "ticket_type_id",
            "ticket_type",
            "seats",
            "guest_names",
            "guests",
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
            "seats",
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


def checkin_row(obj):
    """One line of the door list (no ticket code: it's the person's credential).
    A companion shows under their own name, with who brought them."""
    if isinstance(obj, EventRegistrationGuest):
        host = obj.registration.person
        return {
            "id": obj.pk,
            "person": {"id": None, "name": obj.name, "email": ""},
            "guest_of": host.name,
            "payment_status": obj.payment_status,
            "checked_in_at": obj.checked_in_at,
        }
    return {
        "id": obj.pk,
        "person": RegistrationPersonMiniSerializer(obj.person).data,
        "guest_of": None,
        "payment_status": obj.payment_status,
        "checked_in_at": obj.checked_in_at,
    }


class RegistrationStatsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    confirmed = serializers.IntegerField()
    checked_in = serializers.IntegerField()
    pending = serializers.IntegerField()
    cancelled = serializers.IntegerField()
    capacity = serializers.IntegerField(allow_null=True)
    seats = serializers.IntegerField()
    waitlist = serializers.IntegerField()
    revenue_cents = serializers.IntegerField()


class WaitlistEntrySerializer(serializers.ModelSerializer):
    event = RegistrationEventMiniSerializer(read_only=True)
    event_id = serializers.PrimaryKeyRelatedField(queryset=Event.objects.all(), source="event", write_only=True)
    companions = serializers.IntegerField(write_only=True, min_value=0, max_value=10, default=0)
    position = serializers.SerializerMethodField()

    class Meta:
        model = EventWaitlistEntry
        fields = ["id", "event", "event_id", "companions", "seats", "offer_expires_at", "position", "created_at"]
        read_only_fields = ["seats", "offer_expires_at", "created_at"]

    def get_position(self, obj):
        return waitlist.position(obj)


class PublicTicketTypeSerializer(serializers.ModelSerializer):
    sold_out = serializers.SerializerMethodField()

    class Meta:
        model = EventTicketType
        fields = ["id", "name", "price_cents", "sales_end", "sold_out"]

    def get_sold_out(self, obj):
        return obj.capacity is not None and waitlist.type_taken(obj) >= obj.capacity


def public_event(event):
    """What the public link shows: enough to decide and sign up, nothing internal."""
    config = event.ticket_config
    now = timezone.now()
    types = [t for t in config.types.all() if t.on_sale(now)]
    free = waitlist.free_seats(event)
    closed = (config.registration_deadline and now > config.registration_deadline) or event.starts_at <= now
    return {
        "id": event.pk,
        "title": event.title,
        "description": event.description,
        "starts_at": event.starts_at,
        "ends_at": event.ends_at,
        "campus": {"name": event.campus.name, "city": event.campus.city} if event.campus_id else None,
        "ticket": {
            "description": config.description,
            "price_cents": config.price_cents,
            "registration_deadline": config.registration_deadline,
            "max_companions": config.max_companions,
            "types": PublicTicketTypeSerializer(types, many=True).data,
        },
        "sold_out": free is not None and free <= 0,
        "registration_open": not closed,
    }
