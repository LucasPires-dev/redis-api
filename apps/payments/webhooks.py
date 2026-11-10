import logging

from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from apps.production.realtime import broadcast_event_update

from .models import EventRegistration, PaymentStatus

logger = logging.getLogger(__name__)

ASAAS_CONFIRMED_EVENTS = {"PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"}
ASAAS_REFUNDED_EVENTS = {"PAYMENT_REFUNDED", "PAYMENT_REFUND_IN_PROGRESS"}
ASAAS_CANCELLED_EVENTS = {"PAYMENT_DELETED", "PAYMENT_OVERDUE"}


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([])
def asaas_webhook(request):
    event = request.data.get("event")
    payment = request.data.get("payment", {})
    external_ref = payment.get("externalReference", "")

    if not external_ref:
        return Response({"ok": True})

    try:
        registration = EventRegistration.objects.select_related("event").get(pk=external_ref)
    except (EventRegistration.DoesNotExist, ValueError):
        logger.warning("Asaas webhook: registration not found for ref %s", external_ref)
        return Response({"ok": True})

    if event in ASAAS_CONFIRMED_EVENTS:
        registration.payment_status = PaymentStatus.CONFIRMED
        registration.paid_at = timezone.now()
        registration.save(update_fields=["payment_status", "paid_at", "updated_at"])
        logger.info("Registration %s confirmed via Asaas", registration.pk)
    elif event in ASAAS_REFUNDED_EVENTS:
        registration.payment_status = PaymentStatus.REFUNDED
        registration.save(update_fields=["payment_status", "updated_at"])
        logger.info("Registration %s refunded via Asaas", registration.pk)
    elif event in ASAAS_CANCELLED_EVENTS:
        registration.payment_status = PaymentStatus.CANCELLED
        registration.save(update_fields=["payment_status", "updated_at"])
        logger.info("Registration %s cancelled via Asaas", registration.pk)

    broadcast_event_update(registration.event_id, "registration")
    return Response({"ok": True})
