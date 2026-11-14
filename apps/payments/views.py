import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action, api_view, authentication_classes, permission_classes
from rest_framework.exceptions import APIException, NotFound, PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.churches.choices import Visibility
from apps.notifications import triggers
from apps.people.cpf import apply_cpf, require_cpf
from apps.production.models import Event
from apps.production.permissions import can_manage_event, scope_events
from apps.production.realtime import broadcast_event_update

from . import checkin, reports, waitlist
from .asaas import AsaasError, cancel_charge, create_charge, find_or_create_customer, refund_payment
from .models import (
    ACTIVE_STATUSES,
    EventExpense,
    EventRegistration,
    EventRegistrationGuest,
    EventTicketConfig,
    EventTicketType,
    EventWaitlistEntry,
    PaymentStatus,
)
from .permissions import (
    ExpensePermission,
    RegistrationPermission,
    TicketConfigPermission,
    TicketTypePermission,
)
from .serializers import (
    CheckinInputSerializer,
    EventExpenseSerializer,
    EventRegistrationSerializer,
    EventTicketConfigSerializer,
    EventTicketTypeSerializer,
    RefundInputSerializer,
    RegistrationStatsSerializer,
    WaitlistEntrySerializer,
    checkin_row,
    public_event,
)

logger = logging.getLogger(__name__)

MANUAL_REFUND_MESSAGE = (
    "O Asaas não estorna este pagamento (só cartão e Pix são estornados; boleto não). "
    "Devolva o valor por fora, por exemplo com um Pix da igreja, e confirme a devolução."
)


def _event_or_none(event_id):
    """The event for an id from the query string; None when missing or malformed."""
    if not event_id:
        return None
    try:
        return Event.objects.select_related("campus").filter(pk=event_id).first()
    except (DjangoValidationError, ValueError):
        return None


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
        # Cancel and refund find any registration; the permission decides
        # (the person themselves, or the event's organizers).
        if self.action in ("cancel", "refund"):
            return qs
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
        by_organizers = registration.person_id != request.user.pk
        if registration.payment_status in (PaymentStatus.CANCELLED, PaymentStatus.REFUNDED):
            return Response(EventRegistrationSerializer(registration).data)
        # A paid registration needs a refund — cancelling it here would
        # silently keep the money. The organizers refund it.
        if registration.payment_status == PaymentStatus.CONFIRMED and registration.amount_cents:
            raise drf_serializers.ValidationError(
                {
                    "detail": "Inscrição já paga: use Reembolsar."
                    if by_organizers
                    else "Inscrição já paga. Procure a organização do evento para solicitar o reembolso."
                }
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
        if by_organizers:
            triggers.event_registration_cancelled(registration)
        return Response(EventRegistrationSerializer(registration).data)

    @action(detail=True, methods=["post"], url_path="refund")
    def refund(self, request, pk=None):
        """Organizers give a paid registration's money back: through Asaas
        (card, Pix) or, when Asaas can't (boleto), by hand with `manual`."""
        registration = self.get_object()
        if registration.payment_status == PaymentStatus.REFUNDED:
            return Response(EventRegistrationSerializer(registration).data)
        if registration.payment_status != PaymentStatus.CONFIRMED or not registration.amount_cents:
            raise drf_serializers.ValidationError(
                {"detail": "Só inscrições pagas são reembolsadas. Para as demais, use Cancelar."}
            )
        params = RefundInputSerializer(data=request.data)
        params.is_valid(raise_exception=True)
        note = params.validated_data["note"].strip()
        manual = params.validated_data["manual"]

        if registration.asaas_payment_id:
            try:
                refund_payment(registration.asaas_payment_id)
            except AsaasError as exc:
                if exc.status_code != 400:
                    raise PaymentProviderUnavailable(
                        "Não foi possível fazer o reembolso agora. Tente novamente em alguns minutos."
                    )
                if not manual:
                    raise drf_serializers.ValidationError(
                        {"detail": MANUAL_REFUND_MESSAGE, "code": "manual_refund_required"}
                    )
                note = f"Devolvido por fora{f' — {note}' if note else ''}"
        elif not manual:
            # Paid outside Asaas: the money goes back the same way.
            raise drf_serializers.ValidationError(
                {"detail": "Este pagamento não passou pelo Asaas. Devolva o valor por fora e confirme.",
                 "code": "manual_refund_required"}
            )
        else:
            note = f"Devolvido por fora{f' — {note}' if note else ''}"

        registration.payment_status = PaymentStatus.REFUNDED
        registration.refunded_at = timezone.now()
        registration.refunded_by = request.user
        registration.refund_note = note[:200]
        registration.save(
            update_fields=["payment_status", "refunded_at", "refunded_by", "refund_note", "updated_at"]
        )
        waitlist.refresh(registration.event)
        broadcast_event_update(registration.event_id, "registration")
        triggers.event_registration_refunded(registration)
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


class EventExpenseViewSet(viewsets.ModelViewSet):
    """An event's expenses (?event=), for its organizers only."""

    serializer_class = EventExpenseSerializer
    permission_classes = [ExpensePermission]
    pagination_class = None

    def get_queryset(self):
        qs = EventExpense.objects.select_related("event", "created_by")
        if self.action != "list":
            return qs
        event = _event_or_none(self.request.query_params.get("event"))
        if event is None:
            return qs.none()
        if not can_manage_event(self.request.user, event):
            raise PermissionDenied()
        return qs.filter(event=event)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)


def _managed_event(request, event_id):
    event = _event_or_none(event_id)
    if event is None:
        raise NotFound("Evento não encontrado.")
    if not can_manage_event(request.user, event):
        raise PermissionDenied("Os relatórios são da organização do evento.")
    return event


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def event_reports_view(request):
    """The events the caller organizes, side by side (newest first).

    ?events=<id>,<id> picks them; otherwise ?series= (its occurrences),
    ?from=/?to= (dates) and ?limit= (default 30, up to 100). Without dates or
    events, only events that already started."""
    qs = scope_events(Event.objects.select_related("campus"), request.user)
    ids = [i for i in request.query_params.get("events", "").split(",") if i]
    if ids:
        try:
            qs = qs.filter(pk__in=ids[:12])
        except (DjangoValidationError, ValueError):
            return Response({"detail": "Eventos inválidos."}, status=status.HTTP_400_BAD_REQUEST)
    else:
        series = request.query_params.get("series")
        if series:
            qs = qs.filter(series_id=series)
        start = request.query_params.get("from")
        end = request.query_params.get("to")
        if start:
            qs = qs.filter(starts_at__date__gte=start)
        if end:
            qs = qs.filter(starts_at__date__lte=end)
        if not (start or end):
            qs = qs.filter(starts_at__lte=timezone.now())
    try:
        limit = min(max(int(request.query_params.get("limit", 30)), 1), 100)
    except ValueError:
        limit = 30
    try:
        events = [e for e in qs.order_by("-starts_at")[:300] if can_manage_event(request.user, e)][:limit]
    except DjangoValidationError:
        return Response({"detail": "Filtro inválido."}, status=status.HTTP_400_BAD_REQUEST)
    return Response({"results": [reports.compact(e) for e in events]})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def event_report_view(request, event_id):
    """One event's report: attendance, tickets, money in and out."""
    return Response(reports.summary(_managed_event(request, event_id)))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def event_report_csv_view(request, event_id):
    """?kind=attendees (default) | finance — a CSV to open in a spreadsheet."""
    event = _managed_event(request, event_id)
    kind = request.query_params.get("kind", "attendees")
    if kind not in ("attendees", "finance"):
        return Response({"detail": "kind: attendees ou finance."}, status=status.HTTP_400_BAD_REQUEST)
    content = reports.attendees_csv(event) if kind == "attendees" else reports.finance_csv(event)
    name = reports.filename(event, "inscritos" if kind == "attendees" else "financeiro")
    response = HttpResponse(content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def public_event_view(request, event_id):
    """The public link of an event (/evento/<id>): only for public events with registration."""
    event = (
        Event.objects.select_related("campus", "church", "ticket_config")
        .prefetch_related("ticket_config__types")
        .filter(pk=event_id, visibility=Visibility.PUBLIC, ticket_config__isnull=False)
        .first()
    )
    if event is None:
        raise NotFound("Evento não encontrado.")
    return Response(public_event(event))
