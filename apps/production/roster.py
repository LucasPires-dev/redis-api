"""The serving roster beyond invite/confirm: time conflicts and swaps.

Conflict: the person is already on the roster (not declined) of another event
whose time overlaps. It is a warning, never a block (the leader may know the
person serves only part of each).

Swap: someone who can't serve asks another member of the same ministry to take
their place. When the substitute accepts, the entry passes to them, confirmed.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from apps.notifications import triggers

from .choices import RosterStatus, SwapStatus
from .models import EventRosterEntry, RosterSwapRequest

# Events without an end are taken as lasting this long.
DEFAULT_DURATION = timedelta(hours=2)


def window(event):
    return event.starts_at, event.ends_at or event.starts_at + DEFAULT_DURATION


def conflicts(person_id, event):
    """Other events (overlapping in time) where the person is on the roster."""
    start, end = window(event)
    entries = (
        EventRosterEntry.objects.filter(
            person_id=person_id,
            event__starts_at__lt=end,
            event__starts_at__gt=start - timedelta(days=2),
        )
        .exclude(status=RosterStatus.DECLINED)
        .exclude(event_id=event.pk)
        .select_related("event")
    )
    found = {}
    for entry in entries:
        other_start, other_end = window(entry.event)
        if other_start < end and start < other_end:
            found[entry.event_id] = entry.event
    return sorted(found.values(), key=lambda e: e.starts_at)


def ministry_people(ministry):
    """Who serves in the ministry: its members and its leaders."""
    return (
        get_user_model()
        .objects.filter(Q(ministries=ministry) | Q(ministry_leaderships__ministry=ministry), is_active=True)
        .distinct()
        .order_by("name")
    )


def swap_candidates(entry):
    """People who could take the place: same ministry, not on this event's roster for it."""
    taken = EventRosterEntry.objects.filter(event_id=entry.event_id, ministry_id=entry.ministry_id).values(
        "person_id"
    )
    return ministry_people(entry.ministry).exclude(pk__in=taken)


def _fail(message):
    raise serializers.ValidationError({"detail": message})


def request_swap(entry, by, substitute, message=""):
    if entry.status == RosterStatus.DECLINED:
        _fail("Você recusou esta escala; não há o que trocar.")
    if entry.event.starts_at <= timezone.now():
        _fail("Este evento já começou.")
    if not swap_candidates(entry).filter(pk=substitute.pk).exists():
        _fail(f"Escolha alguém do ministério {entry.ministry.name} que ainda não esteja nesta escala.")
    if entry.swap_requests.filter(status=SwapStatus.PENDING).exists():
        _fail("Já há um pedido de troca aguardando resposta.")
    swap = RosterSwapRequest.objects.create(entry=entry, requested_by=by, substitute=substitute, message=message)
    triggers.roster_swap_requested(swap)
    return swap


def _answer(swap, status):
    if swap.status != SwapStatus.PENDING:
        _fail("Este pedido de troca já foi respondido.")
    swap.status = status
    swap.responded_at = timezone.now()
    swap.save(update_fields=["status", "responded_at"])


def accept_swap(swap):
    with transaction.atomic():
        swap = RosterSwapRequest.objects.select_for_update().select_related("entry__event", "entry__ministry").get(
            pk=swap.pk
        )
        entry = swap.entry
        if swap.status == SwapStatus.PENDING and not swap_candidates(entry).filter(pk=swap.substitute_id).exists():
            _fail("Você já está nesta escala.")
        _answer(swap, SwapStatus.ACCEPTED)
        entry.person_id = swap.substitute_id
        entry.status = RosterStatus.CONFIRMED
        entry.responded_at = swap.responded_at
        entry.save(update_fields=["person", "status", "responded_at"])
    triggers.roster_swap_answered(swap, accepted=True)
    return swap


def decline_swap(swap):
    _answer(swap, SwapStatus.DECLINED)
    triggers.roster_swap_answered(swap, accepted=False)
    return swap


def cancel_swap(swap):
    _answer(swap, SwapStatus.CANCELLED)
    return swap
