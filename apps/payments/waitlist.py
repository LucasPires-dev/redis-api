"""Places at an event, and the waitlist of sold-out events.

A registration takes `seats` places (the person plus companions) while active
(pending or confirmed). When the event is full, people may join the waitlist.
When places open (a cancellation, an expired charge, more capacity), the first
in line gets them held for OFFER_HOURS: only they can take them, by registering
as usual. An offer not taken in time is dropped and the places go to the next.

Offers are handed out lazily, whenever places are counted (registration,
cancellation, the event page) and every hour by the reminders, so nothing
depends on the scheduler alone.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from apps.notifications import triggers

from .models import ACTIVE_STATUSES, EventRegistration, EventTicketConfig, EventWaitlistEntry

OFFER_HOURS = 48


def _sum(qs, field="seats"):
    return qs.aggregate(total=Sum(field))["total"] or 0


def seats_taken(event):
    return _sum(EventRegistration.objects.filter(event=event, payment_status__in=ACTIVE_STATUSES))


def type_taken(ticket_type):
    return _sum(ticket_type.registrations.filter(payment_status__in=ACTIVE_STATUSES))


def held(event, exclude_person=None):
    """Places held by offers still valid (but the given person's own)."""
    offers = EventWaitlistEntry.objects.filter(event=event, offer_expires_at__gte=timezone.now())
    if exclude_person is not None:
        offers = offers.exclude(person=exclude_person)
    return _sum(offers)


def config_of(event):
    try:
        return event.ticket_config
    except EventTicketConfig.DoesNotExist:
        return None


def free_seats(event, for_person=None):
    """Places anyone (or `for_person`, counting their own offer) may still take.
    None: no limit."""
    config = config_of(event)
    if config is None or config.max_capacity is None:
        return None
    return max(0, config.max_capacity - seats_taken(event) - held(event, exclude_person=for_person))


def _offer_deadline(event, now):
    expires = now + timedelta(hours=OFFER_HOURS)
    config = config_of(event)
    limits = [event.starts_at]
    if config and config.registration_deadline:
        limits.append(config.registration_deadline)
    return min([expires, *limits])


def refresh(event):
    """Drops expired offers and offers the open places to whoever is next."""
    now = timezone.now()
    offered = []
    with transaction.atomic():
        EventWaitlistEntry.objects.filter(event=event, offer_expires_at__lt=now).delete()
        waiting = list(
            EventWaitlistEntry.objects.select_for_update()
            .filter(event=event, offer_expires_at__isnull=True)
            .order_by("created_at")
        )
        if not waiting or event.starts_at <= now:
            return []
        free = free_seats(event)
        expires = _offer_deadline(event, now)
        for entry in waiting:
            if free is not None and entry.seats > free:
                break  # strictly in line: nobody jumps ahead because they need fewer places
            entry.offer_expires_at = expires
            entry.save(update_fields=["offer_expires_at"])
            offered.append(entry)
            if free is not None:
                free -= entry.seats
    for entry in offered:
        triggers.event_waitlist_offer(entry)
    return offered


def entry_of(event, person):
    return EventWaitlistEntry.objects.filter(event=event, person=person).first()


def position(entry):
    """1-based place in line (offers first, they're ahead of everyone)."""
    return EventWaitlistEntry.objects.filter(event_id=entry.event_id, created_at__lt=entry.created_at).count() + 1


def join(event, person, seats):
    config = config_of(event)
    if config is None or config.max_capacity is None:
        raise serializers.ValidationError({"detail": "Este evento não tem limite de vagas; faça a inscrição."})
    if EventRegistration.objects.filter(event=event, person=person, payment_status__in=ACTIVE_STATUSES).exists():
        raise serializers.ValidationError({"detail": "Você já está inscrito neste evento."})
    if config.registration_deadline and timezone.now() > config.registration_deadline:
        raise serializers.ValidationError({"detail": "Prazo de inscrição encerrado."})
    if (free_seats(event) or 0) >= seats:
        raise serializers.ValidationError({"detail": "Ainda há vagas: faça a inscrição.", "code": "has_seats"})
    try:
        with transaction.atomic():
            return EventWaitlistEntry.objects.create(event=event, person=person, seats=seats)
    except IntegrityError:
        raise serializers.ValidationError({"detail": "Você já está na lista de espera."})


def leave(entry):
    event = entry.event
    entry.delete()
    refresh(event)


def taken_by_registration(event, person):
    """The person registered: their place in line (and any offer) is used."""
    EventWaitlistEntry.objects.filter(event=event, person=person).delete()
