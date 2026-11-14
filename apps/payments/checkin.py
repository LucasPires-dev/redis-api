"""Check-in at the door: the person shows their ticket (QR code or the code
typed) or is found by name, and the team marks them present.

Who checks people in: whoever manages the event, plus the volunteers confirmed
on its roster (the reception team).
"""

from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from apps.production.choices import RosterStatus
from apps.production.permissions import can_manage_event

from .models import EventRegistration, PaymentStatus


def can_check_in(person, event) -> bool:
    if can_manage_event(person, event):
        return True
    return event.roster_entries.filter(person=person, status=RosterStatus.CONFIRMED).exists()


def normalize_code(raw) -> str:
    """What is typed or read from a QR code → the stored form."""
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())


def find(event, *, code="", registration_id=None):
    qs = EventRegistration.objects.filter(event=event).select_related("person", "event")
    registration = None
    if registration_id:
        registration = qs.filter(pk=registration_id).first()
    elif normalize_code(code):
        registration = qs.filter(ticket_code=normalize_code(code)).first()
    if registration is None:
        raise NotFound("Ingresso não encontrado para este evento.")
    return registration


def check_in(registration, by):
    """Marks the person present. Returns (registration, already_checked_in)."""
    if registration.payment_status == PaymentStatus.PENDING:
        raise serializers.ValidationError(
            {"detail": "Pagamento pendente: a inscrição ainda não foi confirmada.", "code": "pending"}
        )
    if registration.payment_status != PaymentStatus.CONFIRMED:
        raise serializers.ValidationError(
            {"detail": "Esta inscrição foi cancelada.", "code": "cancelled"}
        )
    if registration.checked_in_at:
        return registration, True
    registration.checked_in_at = timezone.now()
    registration.checked_in_by = by
    registration.save(update_fields=["checked_in_at", "checked_in_by", "updated_at"])
    return registration, False


def undo(registration):
    registration.checked_in_at = None
    registration.checked_in_by = None
    registration.save(update_fields=["checked_in_at", "checked_in_by", "updated_at"])
    return registration
