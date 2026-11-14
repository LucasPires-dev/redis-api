"""Waitlist of full classes.

A member may join the waitlist of an open class with no seats left. When a
seat opens (a cancellation, an expired reservation, more seats), the first in
line gets it held for the class's `reservation_days`: only they can take it,
by enrolling as usual. An offer not taken in time is dropped and the seat goes
to the next in line.

Like expired reservations (billing.expire_overdue), offers are handed out and
expired lazily, whenever the class's seats are counted — no scheduler needed.
"""

from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers

from . import billing
from .models import WaitlistEntry


def held_offers(course_class, exclude_person=None):
    """Offers currently holding a seat (but the given person's own)."""
    offers = course_class.waitlist.filter(offer_expires_at__gte=timezone.now())
    if exclude_person is not None:
        offers = offers.exclude(person=exclude_person)
    return offers


def refresh(course_class):
    """Drops expired offers and offers the open seats to whoever is next."""
    now = timezone.now()
    with transaction.atomic():
        course_class.waitlist.filter(offer_expires_at__lt=now).delete()
        waiting = course_class.waitlist.filter(offer_expires_at__isnull=True).order_by("created_at")
        if not waiting.exists():
            return
        if course_class.max_capacity is None:
            # No limit anymore: everyone waiting may enroll.
            free = waiting.count()
        else:
            taken = billing.active_enrollments(course_class).count()
            free = course_class.max_capacity - taken - held_offers(course_class).count()
        if free <= 0:
            return
        expires = now + timedelta(days=course_class.reservation_days)
        ids = list(waiting.values_list("id", flat=True)[:free])
        WaitlistEntry.objects.filter(id__in=ids).update(offer_expires_at=expires)


def entry_of(course_class, person):
    return course_class.waitlist.filter(person=person).first()


def position(entry):
    """1 for the first still waiting; None once a seat is held for them."""
    if entry.offer_expires_at is not None:
        return None
    return entry.course_class.waitlist.filter(
        offer_expires_at__isnull=True, created_at__lt=entry.created_at
    ).count() + 1


def status_of(entry):
    """What the person sees about their place in line."""
    if entry is None:
        return None
    return {
        "position": position(entry),
        "offer_expires_at": entry.offer_expires_at,
        "joined_at": entry.created_at,
    }


def join(course_class, person):
    if course_class.max_capacity is None or billing.seats_left(course_class, for_person=person):
        raise serializers.ValidationError({"detail": "Ainda há vagas: faça a inscrição."})
    if billing.active_enrollments(course_class).filter(person=person).exists():
        raise serializers.ValidationError({"detail": "Você já está matriculado(a) nesta turma."})
    try:
        entry, _ = WaitlistEntry.objects.get_or_create(course_class=course_class, person=person)
    except IntegrityError:  # double click
        entry = entry_of(course_class, person)
    return entry


def leave(course_class, person):
    """Leaves the line (or gives up a held seat, which goes to the next)."""
    course_class.waitlist.filter(person=person).delete()
    refresh(course_class)


def enrolled(course_class, person):
    """The person got a seat: they're out of the line."""
    course_class.waitlist.filter(person=person).delete()
