import logging

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied
from rest_framework.response import Response

from apps.people.cpf import apply_cpf, require_cpf
from apps.production.models import Event
from apps.production.permissions import can_manage_event, scope_events
from apps.production.realtime import broadcast_event_update

from .asaas import AsaasError, cancel_charge, create_charge, find_or_create_customer
from .models import ACTIVE_STATUSES, EventRegistration, EventTicketConfig, PaymentStatus
from .permissions import RegistrationPermission, TicketConfigPermission
from .serializers import (
    EventRegistrationSerializer,
    EventTicketConfigSerializer,
    RegistrationStatsSerializer,
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


class EventRegistrationViewSet(viewsets.ModelViewSet):
    serializer_class = EventRegistrationSerializer
    permission_classes = [RegistrationPermission]
    http_method_names = ["get", "post", "head", "options"]

    def get_queryset(self):
        qs = EventRegistration.objects.select_related("person", "event")
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
        event = serializer.validated_data["event"]
        cpf = serializer.validated_data.pop("cpf", "")

        # Only events the person can see (knowing the id is not enough).
        if not scope_events(Event.objects.filter(pk=event.pk), self.request.user).exists():
            raise drf_serializers.ValidationError({"event_id": "Evento não encontrado."})

        try:
            config = event.ticket_config
        except EventTicketConfig.DoesNotExist:
            raise drf_serializers.ValidationError(
                {"event_id": "Este evento não possui inscrição habilitada."}
            )

        if config.registration_deadline and timezone.now() > config.registration_deadline:
            raise drf_serializers.ValidationError(
                {"event_id": "Prazo de inscrição encerrado."}
            )

        if config.max_capacity:
            current = EventRegistration.objects.filter(
                event=event, payment_status__in=ACTIVE_STATUSES
            ).count()
            if current >= config.max_capacity:
                raise drf_serializers.ValidationError(
                    {"event_id": "Vagas esgotadas."}
                )

        if config.price_cents > 0:
            apply_cpf(self.request.user, cpf)
            require_cpf(self.request.user)

        already = drf_serializers.ValidationError({"event_id": "Você já está inscrito neste evento."})
        # Only active registrations count: after cancelling, the person may register again.
        if EventRegistration.objects.filter(
            event=event, person=self.request.user, payment_status__in=ACTIVE_STATUSES
        ).exists():
            raise already
        try:
            with transaction.atomic():
                registration = serializer.save(
                    person=self.request.user, amount_cents=config.price_cents
                )
        except IntegrityError:
            # Two simultaneous requests (double click): the DB constraint wins.
            raise already

        if config.price_cents > 0:
            try:
                customer_id = find_or_create_customer(
                    email=self.request.user.email,
                    name=self.request.user.name,
                    cpf=self.request.user.cpf,
                )
                charge = create_charge(
                    customer_id=customer_id,
                    amount_cents=config.price_cents,
                    description=f"Inscrição: {event.title}",
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
                raise PaymentProviderUnavailable()
        else:
            registration.payment_status = PaymentStatus.CONFIRMED
            registration.paid_at = timezone.now()
            registration.save(update_fields=["payment_status", "paid_at"])

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
            "pending": regs.filter(payment_status=PaymentStatus.PENDING).count(),
            "cancelled": regs.filter(payment_status=PaymentStatus.CANCELLED).count(),
            "capacity": capacity,
            # What was actually charged, not count x today's price.
            "revenue_cents": confirmed.aggregate(total=Sum("amount_cents"))["total"] or 0,
        }
        serializer = RegistrationStatsSerializer(data)
        return Response(serializer.data)
