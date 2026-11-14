"""Linking a self-registered person to a campus: by invite code (immediate) or by
a join request a campus leader approves. Either way the person becomes a plain
member; roles only come from appointment."""

import secrets

from django.db import transaction
from django.utils import timezone

from .choices import JoinRequestStatus
from .models import Campus, CampusJoinRequest

# No 0/O or 1/I, so a code read off a screen or QR caption is typed right.
INVITE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 8


def new_invite_code() -> str:
    while True:
        code = "".join(secrets.choice(INVITE_CODE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))
        if not Campus.objects.filter(invite_code=code).exists():
            return code


def campus_for_invite(raw_code):
    code = (raw_code or "").strip().upper()
    if not code:
        return None
    return Campus.objects.select_related("church").filter(invite_code=code).first()


@transaction.atomic
def link_to_campus(person, campus):
    """Makes the person a member of the campus and closes any request still pending."""
    person.campus = campus
    person.save(update_fields=["campus"])
    CampusJoinRequest.objects.filter(person=person, status=JoinRequestStatus.PENDING).update(
        status=JoinRequestStatus.CANCELLED, decided_at=timezone.now()
    )
