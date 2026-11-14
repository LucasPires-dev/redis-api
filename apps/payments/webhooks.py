"""Asaas payment webhook.

Asaas calls this endpoint on every payment status change. It is public (no JWT),
so two checks guard it before any registration is touched:

1. Authenticity: Asaas sends the token configured in its panel in the
   `asaas-access-token` header; it must match ASAAS_WEBHOOK_TOKEN. Without a
   configured token the webhook rejects everything (fail closed) — otherwise
   anyone could POST "PAYMENT_CONFIRMED" for their own registration id and get
   a paid ticket for free.
2. Consistency: the payment in the body must be the one created for that
   registration, and a confirmation is re-checked against the Asaas API, so a
   forged or replayed body can't confirm a payment Asaas doesn't report as paid.
"""

import hmac
import logging

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from apps.notifications import triggers
from apps.production.realtime import broadcast_event_update

from . import waitlist
from .asaas import AsaasError, get_payment_status
from .models import EventRegistration, PaymentStatus

logger = logging.getLogger(__name__)

# Course enrollments use "enrollment:<uuid>"; event registrations the bare uuid.
ENROLLMENT_PREFIX = "enrollment:"

ASAAS_CONFIRMED_EVENTS = {"PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"}
ASAAS_REFUNDED_EVENTS = {"PAYMENT_REFUNDED", "PAYMENT_REFUND_IN_PROGRESS"}
ASAAS_CANCELLED_EVENTS = {"PAYMENT_DELETED", "PAYMENT_OVERDUE"}
# Statuses the Asaas API reports for a payment that has really been paid.
ASAAS_PAID_STATUSES = {"CONFIRMED", "RECEIVED", "RECEIVED_IN_CASH"}


def _is_authentic(request) -> bool:
    expected = getattr(settings, "ASAAS_WEBHOOK_TOKEN", "")
    received = request.headers.get("asaas-access-token", "")
    if not expected:
        logger.error("Asaas webhook rejected: ASAAS_WEBHOOK_TOKEN is not configured")
        return False
    # Constant-time comparison, so the token can't be guessed by timing responses.
    return hmac.compare_digest(received.encode(), expected.encode())


@csrf_exempt
@api_view(["POST"])
@authentication_classes([])
@permission_classes([])
def asaas_webhook(request):
    if not _is_authentic(request):
        return Response({"detail": "Invalid webhook token."}, status=status.HTTP_401_UNAUTHORIZED)

    event = request.data.get("event")
    payment = request.data.get("payment") or {}
    external_ref = payment.get("externalReference", "")

    # Unknown/unrelated payments get 200: Asaas pauses the webhook queue after
    # repeated non-2xx responses, which would also block legitimate events.
    if not external_ref:
        return Response({"ok": True})

    if external_ref.startswith(ENROLLMENT_PREFIX):
        return _handle_enrollment(event, payment, external_ref)

    try:
        registration = EventRegistration.objects.select_related("event").get(pk=external_ref)
    except (EventRegistration.DoesNotExist, ValueError, DjangoValidationError):
        logger.warning("Asaas webhook: registration not found for ref %s", external_ref)
        return Response({"ok": True})

    payment_id = payment.get("id", "")
    if not registration.asaas_payment_id or payment_id != registration.asaas_payment_id:
        logger.warning(
            "Asaas webhook: payment %s doesn't belong to registration %s", payment_id, registration.pk
        )
        return Response({"ok": True})

    if event in ASAAS_CONFIRMED_EVENTS:
        rejected = _verify_paid(event, payment_id)
        if rejected:
            return rejected
        registration.payment_status = PaymentStatus.CONFIRMED
        registration.paid_at = timezone.now()
        try:
            with transaction.atomic():
                registration.save(update_fields=["payment_status", "paid_at", "updated_at"])
        except IntegrityError:
            # The person paid an old, cancelled registration's charge after
            # registering again. Keep the active one; the organizers must refund
            # this payment. 200 so Asaas doesn't keep retrying.
            logger.error(
                "Asaas webhook: payment %s for cancelled registration %s, but the person "
                "already has an active one — needs a manual refund",
                payment_id,
                registration.pk,
            )
            return Response({"ok": True})
        logger.info("Registration %s confirmed via Asaas", registration.pk)
        triggers.event_registration_confirmed(registration)
    elif event in ASAAS_REFUNDED_EVENTS:
        registration.payment_status = PaymentStatus.REFUNDED
        registration.save(update_fields=["payment_status", "updated_at"])
        logger.info("Registration %s refunded via Asaas", registration.pk)
    elif event in ASAAS_CANCELLED_EVENTS:
        registration.payment_status = PaymentStatus.CANCELLED
        registration.save(update_fields=["payment_status", "updated_at"])
        logger.info("Registration %s cancelled via Asaas", registration.pk)
    else:
        return Response({"ok": True})

    if registration.payment_status in (PaymentStatus.REFUNDED, PaymentStatus.CANCELLED):
        waitlist.refresh(registration.event)  # the freed places go to whoever waits
    broadcast_event_update(registration.event_id, "registration")
    return Response({"ok": True})


def _verify_paid(event, payment_id):
    """Re-checks a confirmation against the Asaas API. Returns a Response to
    send back when it must not be applied, or None when the payment is paid."""
    try:
        asaas_status = get_payment_status(payment_id)
    except AsaasError:
        # Non-2xx makes Asaas retry later, when its API may be reachable again.
        logger.exception("Asaas webhook: couldn't verify payment %s", payment_id)
        return Response({"detail": "Verification failed."}, status=status.HTTP_502_BAD_GATEWAY)
    if asaas_status not in ASAAS_PAID_STATUSES:
        logger.warning(
            "Asaas webhook: %s for payment %s, but Asaas reports %s — ignored",
            event,
            payment_id,
            asaas_status,
        )
        return Response({"ok": True})
    return None


def _handle_enrollment(event, payment, external_ref):
    """Course enrollment payments (externalReference "enrollment:<uuid>"); each
    installment arrives as its own payment. See apps.courses.billing."""
    from apps.courses import billing

    enrollment = billing.find_for_asaas_payment(external_ref, payment)
    if enrollment is None:
        logger.warning("Asaas webhook: payment %s doesn't match enrollment ref %s", payment.get("id"), external_ref)
        return Response({"ok": True})

    if event in ASAAS_CONFIRMED_EVENTS:
        rejected = _verify_paid(event, payment.get("id", ""))
        if rejected:
            return rejected
        billing.apply_asaas_paid(enrollment, payment)
        logger.info("Enrollment %s: payment %s received via Asaas", enrollment.pk, payment.get("id"))
    elif event in ASAAS_REFUNDED_EVENTS:
        billing.apply_asaas_refunded(enrollment, payment)
    elif event == "PAYMENT_OVERDUE":
        billing.apply_asaas_overdue(enrollment, payment)
    # PAYMENT_DELETED: our own cancellations (the ids are cleared first, so they
    # don't even match); nothing to do.
    return Response({"ok": True})
