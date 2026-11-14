"""Raising notifications.

`notify()` is the single entry point every feature uses. It never breaks the
action that raised it: a notification is a side effect, so a failure here is
logged and swallowed. Enrolling, paying or approving must still succeed.
"""

import logging
from collections.abc import Iterable

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.db import transaction

from .models import Notification

logger = logging.getLogger(__name__)


def group_name(person_id) -> str:
    return f"notifications-{person_id}"


def _as_ids(recipients) -> list:
    if recipients is None:
        return []
    if not isinstance(recipients, Iterable) or isinstance(recipients, (str, bytes)):
        recipients = [recipients]
    ids = []
    for r in recipients:
        pk = getattr(r, "pk", r)
        if pk is not None and pk not in ids:
            ids.append(pk)
    return ids


def notify(recipients, kind: str, title: str, *, body: str = "", link: str = "", dedupe_key: str = ""):
    """Creates one notification per recipient (a Person, an id, or an iterable of
    either) and pushes it to their open screens once the transaction commits.
    With a `dedupe_key`, recipients who already got it are skipped."""
    try:
        ids = _as_ids(recipients)
        if dedupe_key:
            already = set(
                Notification.objects.filter(recipient_id__in=ids, dedupe_key=dedupe_key).values_list(
                    "recipient_id", flat=True
                )
            )
            ids = [i for i in ids if i not in already]
        if not ids:
            return []
        # A savepoint, so a failed insert can't poison the caller's transaction.
        with transaction.atomic():
            created = Notification.objects.bulk_create(
                [
                    Notification(
                        recipient_id=i,
                        kind=kind,
                        title=title[:200],
                        body=body,
                        link=link[:300],
                        dedupe_key=dedupe_key[:150],
                    )
                    for i in ids
                ],
                ignore_conflicts=bool(dedupe_key),
            )
        transaction.on_commit(lambda: _push(ids, title))
        new_ids = [str(n.pk) for n in created]
        if settings.NOTIFICATION_EMAILS_ENABLED or settings.NOTIFICATION_PUSH_ENABLED:
            transaction.on_commit(lambda: _queue_delivery(new_ids))
        return created
    except Exception:
        logger.exception("Failed to raise notification %s", kind)
        return []


def _queue_delivery(notification_ids):
    """Email and push go out from the worker, so a slow provider never holds
    the request."""
    from .tasks import send_notification_emails, send_notification_push

    try:
        if settings.NOTIFICATION_EMAILS_ENABLED:
            send_notification_emails.delay(notification_ids)
        if settings.NOTIFICATION_PUSH_ENABLED:
            send_notification_push.delay(notification_ids)
    except Exception:
        logger.warning("Couldn't queue notification delivery", exc_info=True)


def _push(person_ids, title):
    """Tells the recipients' open screens there is something new. Best effort:
    the notification is saved, and the bell also polls."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    for pk in person_ids:
        try:
            async_to_sync(channel_layer.group_send)(
                group_name(pk), {"type": "notification.new", "title": title}
            )
        except Exception:
            logger.warning("Notification push failed for %s", pk, exc_info=True)
            return  # Redis is down: don't try every recipient
