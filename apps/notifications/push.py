"""Sending notifications as Web Push, to the devices people turned it on in.

Runs in the Celery worker (tasks.send_notification_push). The payload is what
the app's service worker (public/sw.js) shows: title, body and the app link.
A device the push service reports as gone (404/410) is forgotten.
"""

import json
import logging

from django.conf import settings
from pywebpush import WebPushException, webpush

from .categories import category_of
from .models import Notification, NotificationPreference, PushSubscription

logger = logging.getLogger(__name__)

GONE = {404, 410}
# Kept by the push service while the device is offline (seconds).
TTL = 24 * 60 * 60


def payload(notification) -> str:
    return json.dumps(
        {
            "id": str(notification.pk),
            "title": notification.title,
            "body": notification.body,
            "link": notification.link or "/app/notificacoes",
        }
    )


def send_push(notification_ids) -> int:
    """Pushes the given notifications to their recipients' devices. Returns how
    many deliveries the push services accepted."""
    if not settings.NOTIFICATION_PUSH_ENABLED:
        return 0
    notifications = list(Notification.objects.filter(pk__in=notification_ids))
    recipients = {n.recipient_id for n in notifications}
    preferences = {p.person_id: p for p in NotificationPreference.objects.filter(person_id__in=recipients)}
    devices = {}
    for sub in PushSubscription.objects.filter(person_id__in=recipients):
        devices.setdefault(sub.person_id, []).append(sub)

    delivered = 0
    for notification in notifications:
        preference = preferences.get(notification.recipient_id)
        category = category_of(notification.kind)
        if category is None or (preference and not preference.wants_push(category)):
            continue
        for sub in devices.get(notification.recipient_id, []):
            try:
                webpush(
                    subscription_info=sub.as_info(),
                    data=payload(notification),
                    vapid_private_key=settings.VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": settings.VAPID_SUBJECT},
                    ttl=TTL,
                    timeout=10,
                )
                delivered += 1
            except WebPushException as exc:
                status = getattr(exc.response, "status_code", None)
                if status in GONE:
                    sub.delete()
                else:
                    logger.warning("Push to %s failed (%s)", sub.pk, status, exc_info=True)
            except Exception:
                logger.exception("Push to %s failed", sub.pk)
    return delivered
