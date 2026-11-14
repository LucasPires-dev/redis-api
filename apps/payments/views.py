import logging

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action, api_view, authentication_classes, permission_classes
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.notifications import triggers
from apps.people.cpf import apply_cpf, require_cpf
from apps.production.models import Event
from apps.production.permissions import can_manage_event, scope_events
from apps.production.realtime import broadcast_event_update

from . import checkin, waitlist
from .asaas import AsaasError, cancel_charge, create_charge, find_or_create_customer
from .models import (
    ACTIVE_STATUSES,
    EventRegistration,
    EventRegistrationGuest,
    EventTicketConfig,
    EventTicketType,
    EventWaitlistEntry,
    PaymentStatus,
)
from .permissions import RegistrationPermission, TicketConfigPermission, TicketTypePermission
from .serializers import (
    CheckinInputSerializer,
    EventRegistrationSerializer,
    EventTicketConfigSerializer,
    EventTicketTypeSerializer,
    RegistrationStatsSerializer,
    WaitlistEntrySerializer,
    checkin_row,
    public_event,
)

logger = logging.getLogger(__name__)


class PaymentProviderUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Não foi possível gerar a cobrança agora. Tente novamente em alguns minutos."
    default_code = "payment_provider_unavailable"


class EventTicketConfigViewSet(viewsets.ModelViewSet):
    serializer_class = EventTicketConfigSerializer
    permission_classes = [TicketConfigPermission]

    def get_queryset(self):
        qs = EventTicketConfig.objects.select_related("event", "event__campus")
        event_id = self.request.query_params.get("event")
        if event_id:
            qs = qs.filter(event_id=event_id)
        return qs.filter(event__in=scope_events(Event.objects.all(), self.request.user))

    def perform_update(self, serializer):
        config = serializer.save()
        waitlist.refresh(config.event)  # more places may hand out offers


class EventTicketTypeViewSet(viewsets.ModelViewSet):
    """Ticket types/batches of an event (?event=). Only its organizers change them."""

    serializer_class = EventTicketTypeSerializer
    permission_classes = [TicketTypePermission]

    def get_queryset(self):
        qs = EventTicketType.objects.select_related("config__event")
        event_id = self.request.query_params.get("event")
        if event_id:
            qs = qs.filter(config__event_id=event_id)
        return qs.filter(config__event__in=scope_events(Event.objects.all(), self.request.user))

    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise drf_serializers.ValidationError(
                {"detail": "Já há inscrições neste ingresso. Encerre as vendas dele em vez de apagar."}
            )


class EventRegistrationViewSet(viewsets.ModelViewSet):
    serializer_class = EventRegistrationSerializer
    permission_classes = [RegistrationPermission]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = EventRegistration.objects.select_related(
            "person", "event", "event__campus", "ticket_type"
        ).prefetch_related("guests")
        event_id = self.request.query_params.get("event")
        # ?mine=1: only the caller's own registrations, even for an event manager
        # (who otherwise gets everyone's, for the attendee list).
        mine = self.request.query_params.get("mine") in ("1", "true")
        if event_id:
            qs = qs.filter(event_id=event_id)
            event = Event.objects.filter(pk=event_id).first()
            if not mine and event and can_manage_event(self.request.user, event):
                return qs
        return qs.filter(person=self.request.user)

    def perform_create(self, serializer):
        data = serializer.validated_data
        event = data["event"]
        cpf = data.pop("cpf", "")
        guest_names = data.pop("guest_names", [])
        ticket_type = data.get("ticket_type")
        person = self.request.user
        fail = drf_serializers.ValidationError

        # Only events the person can see (knowing the id is not enough).
        if not scope_events(Event.objects.filter(pk=event.pk), person).exists():
            raise fail({"event_id": "Evento não encontrado."})

        try:
            config = event.ticket_config
        except EventTicketConfig.DoesNotExist:
            raise fail({"event_id": "Este evento não possui inscrição habilitada."})

        now = timezone.now()
        if config.registration_deadline and now > config.registration_deadline:
            raise fail({"event_id": "Prazo de inscrição encerrado."})

        types = list(config.types.all())
        if types:
            if ticket_type is None or ticket_type.config_id != config.pk:
                raise fail({"ticket_type_id": "Escolha o ingresso."})
            if not ticket_type.on_sale(now):
                raise fail({"ticket_type_id": "Este ingresso não está à venda agora."})
        elif ticket_type is not None:
            raise fail({"ticket_type_id": "Este evento tem um ingresso único."})

        if len(guest_names) > config.max_companions:
            if config.max_companions == 0:
                raise fail({"guest_names": "Este evento não aceita acompanhantes."})
            raise fail({"guest_names": f"No máximo {config.max_companions} acompanhante(s) por inscrição."})
        seats = 1 + len(guest_names)
        unit_price = ticket_type.price_cents if ticket_type else config.price_cents
        amount = unit_price * seats

        already = fail({"event_id": "Você já está inscrito neste evento."})
        # Only active registrations count: after cancelling, the person may register again.
        if EventRegistration.objects.filter(event=event, person=person, payment_status__in=ACTIVE_STATUSES).exists():
            raise already

        if amount > 0:
            apply_cpf(person, cpf)
            require_cpf(person)

        try:
            with transaction.atomic():
                # Counted under a lock on the event, so two last-minute
                # registrations can't both take the last place.
                Event.objects.select_for_update().get(pk=event.pk)
                if config.max_capacity:
                    waitlist.refresh(event)  # whoever waits in line comes first
                free = waitlist.free_seats(event, for_person=person)
                if free is not None and seats > free:
                    raise fail({"detail": "Vagas esgotadas.", "code": "full", "free_seats": free})
                if ticket_type and ticket_type.capacity is not None:
                    if waitlist.type_taken(ticket_type) + seats > ticket_type.capacity:
                        raise fail({"ticket_type_id": f"O ingresso {ticket_type.name} esgotou."})
                registration = serializer.save(person=person, amount_cents=amount, seats=seats)
                for name in guest_names:
                    EventRegistrationGuest.objects.create(registration=registration, name=name)
                waitlist.taken_by_registration(event, person)
        except IntegrityError:
            # Two simultaneous requests (double click): the DB constraint wins.
            raise already

        if amount > 0:
            try:
                customer_id = find_or_create_customer(
                    email=self.request.user.email,
                    name=self.request.user.name,
                    cpf=self.request.user.cpf,
                )
                description = f"Inscrição: {event.title}"
                if ticket_type:
                    description += f" ({ticket_type.name})"
                if guest_names:
                    description += f" + {len(guest_names)} acompanhante(s)"
                charge = create_charge(
                    customer_id=customer_id,
                    amount_cents=amount,
                    description=description,
                    external_reference=str(registration.id),
                )
                registration.asaas_payment_id = charge["id"]
                registration.payment_url = charge.get("invoiceUrl", "")
                registration.save(update_fields=["asaas_payment_id", "payment_url"])
            except AsaasError:
                # Without a charge the registration could never be paid — and the
                # one-registration-per-person rule would block a retry. Undo it.
                logger.exception("Failed to create Asaas charge for registration %s", registration.id)
                registration.delete()
                waitlist.refresh(event)
                raise PaymentProviderUnavailable()
        else:
            registration.payment_status = PaymentStatus.CONFIRMED
            registration.paid_at = timezone.now()
            registration.save(update_fields=["payment_status", "paid_at"])
            triggers.event_registration_confirmed(registration)

        broadcast_event_update(event.id, "registration")

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        registration = self.get_object()
        if registration.payment_status == PaymentStatus.CANCELLED:
            return Response(EventRegistrationSerializer(registration).data)
        # A paid registration needs a refund in Asaas first — cancelling it here
        # would silently keep the money. The organizers handle refunds.
        if registration.payment_status == PaymentStatus.CONFIRMED and registration.asaas_payment_id:
            raise drf_serializers.ValidationError(
                {"detail": "Inscrição já paga. Procure a organização do evento para solicitar o reembolso."}
            )
        if registration.payment_status == PaymentStatus.PENDING and registration.asaas_payment_id:
            # Kill the payment link, otherwise the person could still pay a
            # registration that no longer exists.
            try:
                cancel_charge(registration.asaas_payment_id)
            except AsaasError:
                raise PaymentProviderUnavailable(
                    "Não foi possível cancelar a cobrança agora. Tente novamente em alguns minutos."
                )
        registration.payment_status = PaymentStatus.CANCELLED
        registration.save(update_fields=["payment_status", "updated_at"])
        waitlist.refresh(registration.event)  # the freed places go to whoever waits
        broadcast_event_update(registration.event_id, "registration")
        return Response(EventRegistrationSerializer(registration).data)

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        event_id = request.query_params.get("event")
        if not event_id:
            return Response({"detail": "event query param required"}, status=status.HTTP_400_BAD_REQUEST)

        event = Event.objects.filter(pk=event_id).first()
        if not event:
            return Response({"detail": "Evento não encontrado."}, status=status.HTTP_404_NOT_FOUND)
        # Registration counts and revenue are for whoever manages the event only.
        if not can_manage_event(request.user, event):
            raise PermissionDenied()

        regs = EventRegistration.objects.filter(event=event)
        confirmed = regs.filter(payment_status=PaymentStatus.CONFIRMED)

        try:
            capacity = event.ticket_config.max_capacity
        except EventTicketConfig.DoesNotExist:
            capacity = None

        data = {
            "total": regs.exclude(payment_status=PaymentStatus.CANCELLED).count(),
            "confirmed": confirmed.count(),
            "checked_in": confirmed.filter(checked_in_at__isnull=False).count(),
            "pending": regs.filter(payment_status=PaymentStatus.PENDING).count(),
            "cancelled": regs.filter(payment_status=PaymentStatus.CANCELLED).count(),
            "capacity": capacity,
            "seats": waitlist.seats_taken(event),
            "waitlist": EventWaitlistEntry.objects.filter(event=event).count(),
            # What was actually charged, not count x today's price.
            "revenue_cents": confirmed.aggregate(total=Sum("amount_cents"))["total"] or 0,
        }
        serializer = RegistrationStatsSerializer(data)
        return Response(serializer.data)

    # ── Check-in at the door ────────────────────────────────────────────────

    def _door_event(self, event):
        if not checkin.can_check_in(self.request.user, event):
            raise PermissionDenied("Só a organização e a equipe escalada no evento fazem check-in.")
        return event

    @action(detail=False, methods=["get"], url_path="door-list")
    def door_list(self, request):
        """?event=: everyone confirmed or pending, for the check-in screen."""
        event = self._door_event(get_object_or_404(Event, pk=request.query_params.get("event")))
        registrations = list(
            EventRegistration.objects.filter(event=event, payment_status__in=ACTIVE_STATUSES)
            .select_related("person")
            .prefetch_related("guests")
            .order_by("person__name")
        )
        rows = []
        for registration in registrations:
            rows.append(registration)
            for guest in registration.guests.all():
                guest.registration = registration
                rows.append(guest)
        confirmed = [r for r in rows if r.payment_status == PaymentStatus.CONFIRMED]
        return Response(
            {
                "confirmed": len(confirmed),
                "checked_in": sum(1 for r in confirmed if r.checked_in_at),
                "registrations": [checkin_row(r) for r in rows],
            }
        )

    @action(detail=False, methods=["post"], url_path="checkin")
    def checkin(self, request):
        ser = CheckinInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        event = self._door_event(ser.validated_data["event"])
        registration = checkin.find(
            event, code=ser.validated_data["code"], registration_id=ser.validated_data["registration_id"]
        )
        registration, already = checkin.check_in(registration, request.user)
        if not already:
            broadcast_event_update(event.id, "checkin")
        return Response(
            {"registration": checkin_row(registration), "already_checked_in": already}
        )

    @action(detail=False, methods=["post"], url_path="undo-checkin")
    def undo_checkin(self, request):
        ser = CheckinInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        event = self._door_event(ser.validated_data["event"])
        registration = checkin.find(
            event, code=ser.validated_data["code"], registration_id=ser.validated_data["registration_id"]
        )
        checkin.undo(registration)
        broadcast_event_update(event.id, "checkin")
        return Response({"registration": checkin_row(registration)})


class WaitlistViewSet(
    mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet
):
    """The caller's places in waitlists (?event= for one event). Join with
    {event_id, companions}; leave with DELETE."""

    serializer_class = WaitlistEntrySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = EventWaitlistEntry.objects.filter(person=self.request.user).select_related("event", "event__campus")
        event_id = self.request.query_params.get("event")
        if event_id:
            event = Event.objects.filter(pk=event_id).first()
            if event:
                waitlist.refresh(event)  # an offer may be waiting for them
            qs = qs.filter(event_id=event_id)
        return qs

    def perform_create(self, serializer):
        event = serializer.validated_data["event"]
        if not scope_events(Event.objects.filter(pk=event.pk), self.request.user).exists():
            raise drf_serializers.ValidationError({"event_id": "Evento não encontrado."})
        config = waitlist.config_of(event)
        companions = serializer.validated_data.pop("companions", 0)
        if config and companions > config.max_companions:
            raise drf_serializers.ValidationError({"companions": "Acompanhantes acima do permitido."})
        serializer.instance = waitlist.join(event, self.request.user, seats=1 + companions)

    def perform_destroy(self, instance):
        waitlist.leave(instance)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def public_event_view(request, event_id):
    """The public link of an event (/evento/<id>): only when its organizers opened it."""
    event = (
        Event.objects.select_related("campus", "ticket_config")
        .prefetch_related("ticket_config__types")
        .filter(pk=event_id, ticket_config__is_public=True)
        .first()
    )
    if event is None:
        raise NotFound("Evento não encontrado.")
    return Response(public_event(event))
