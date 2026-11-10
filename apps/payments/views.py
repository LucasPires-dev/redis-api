import logging

from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers as drf_serializers
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.production.models import Event
from apps.production.permissions import can_manage_event, scope_events
from apps.production.realtime import broadcast_event_update

from .asaas import AsaasError, create_charge, find_or_create_customer
from .models import EventRegistration, EventTicketConfig, PaymentStatus
from .permissions import RegistrationPermission, TicketConfigPermission
from .serializers import (
    EventRegistrationSerializer,
    EventTicketConfigSerializer,
    RegistrationStatsSerializer,
)

logger = logging.getLogger(__name__)


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
        if event_id:
            qs = qs.filter(event_id=event_id)
            event = Event.objects.filter(pk=event_id).first()
            if event and can_manage_event(self.request.user, event):
                return qs
        return qs.filter(person=self.request.user)

    def perform_create(self, serializer):
        event = serializer.validated_data["event"]

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
                event=event,
                payment_status__in=[PaymentStatus.PENDING, PaymentStatus.CONFIRMED],
            ).count()
            if current >= config.max_capacity:
                raise drf_serializers.ValidationError(
                    {"event_id": "Vagas esgotadas."}
                )

        if EventRegistration.objects.filter(event=event, person=self.request.user).exists():
            raise drf_serializers.ValidationError(
                {"event_id": "Você já está inscrito neste evento."}
            )

        registration = serializer.save(person=self.request.user)

        if config.price_cents > 0:
            try:
                customer_id = find_or_create_customer(
                    email=self.request.user.email,
                    name=self.request.user.name,
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
                logger.exception("Failed to create Asaas charge for registration %s", registration.id)
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

        regs = EventRegistration.objects.filter(event=event)
        confirmed = regs.filter(payment_status=PaymentStatus.CONFIRMED)

        try:
            capacity = event.ticket_config.max_capacity
        except EventTicketConfig.DoesNotExist:
            capacity = None

        try:
            price = event.ticket_config.price_cents
        except EventTicketConfig.DoesNotExist:
            price = 0

        data = {
            "total": regs.exclude(payment_status=PaymentStatus.CANCELLED).count(),
            "confirmed": confirmed.count(),
            "pending": regs.filter(payment_status=PaymentStatus.PENDING).count(),
            "cancelled": regs.filter(payment_status=PaymentStatus.CANCELLED).count(),
            "capacity": capacity,
            "revenue_cents": (confirmed.count() * price) if price else 0,
        }
        serializer = RegistrationStatsSerializer(data)
        return Response(serializer.data)
