"""Sending notifications by email.

Runs in the Celery worker (tasks.send_notification_emails), never inside the
request. A notification goes out by email when emails are enabled, the person
has an address and hasn't turned that category off. `emailed_at` makes each
one go out at most once.
"""

import logging
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils import timezone

from .categories import category_of
from .models import Notification, NotificationPreference

logger = logging.getLogger(__name__)

PREFERENCES_PATH = "/app/notificacoes/preferencias"


def _absolute(path: str) -> str:
    base = (settings.FRONTEND_URL or "").rstrip("/")
    return f"{base}{path}" if base and path else ""


def should_email(notification, preference) -> bool:
    person = notification.recipient
    if not person.is_active or not person.email:
        return False
    category = category_of(notification.kind)
    if preference is None:
        return category is not None
    return preference.wants_email(category)


def build_message(notification) -> EmailMultiAlternatives:
    person = notification.recipient
    link = _absolute(notification.link)
    preferences = _absolute(PREFERENCES_PATH)
    first_name = person.name.split(" ")[0] if person.name else ""

    text = [f"Olá, {first_name}!" if first_name else "Olá!", "", notification.title]
    if notification.body:
        text.append(notification.body)
    if link:
        text += ["", f"Abrir: {link}"]
    text += ["", "—", "Você recebeu este e-mail pelo Redis."]
    if preferences:
        text.append(f"Para escolher o que recebe por e-mail: {preferences}")

    html = [
        '<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.5;color:#1f2937;max-width:520px">',
        f"<p>{escape(text[0])}</p>",
        f'<p style="font-size:17px;font-weight:bold;margin:0 0 4px">{escape(notification.title)}</p>',
    ]
    if notification.body:
        html.append(f'<p style="margin:0 0 16px">{escape(notification.body)}</p>')
    if link:
        html.append(
            f'<p><a href="{escape(link)}" style="display:inline-block;background:#2563eb;color:#fff;'
            'padding:10px 18px;border-radius:8px;text-decoration:none">Abrir no Redis</a></p>'
        )
    footer = "Você recebeu este e-mail pelo Redis."
    if preferences:
        footer += f' <a href="{escape(preferences)}" style="color:#6b7280">Escolher o que recebo por e-mail</a>.'
    html.append(f'<p style="font-size:12px;color:#6b7280;margin-top:24px">{footer}</p></div>')

    message = EmailMultiAlternatives(
        subject=notification.title, body="\n".join(text), to=[person.email]
    )
    message.attach_alternative("".join(html), "text/html")
    return message


def send_emails(notification_ids) -> int:
    """Emails the given notifications that should go out. Returns how many went."""
    if not settings.NOTIFICATION_EMAILS_ENABLED:
        return 0
    notifications = list(
        Notification.objects.filter(pk__in=notification_ids, emailed_at__isnull=True).select_related("recipient")
    )
    preferences = {
        p.person_id: p
        for p in NotificationPreference.objects.filter(person_id__in={n.recipient_id for n in notifications})
    }
    sent = 0
    connection = get_connection()
    for notification in notifications:
        if not should_email(notification, preferences.get(notification.recipient_id)):
            continue
        try:
            message = build_message(notification)
            message.connection = connection
            message.send()
        except Exception:
            logger.exception("Notification email %s failed", notification.pk)
            continue
        Notification.objects.filter(pk=notification.pk).update(emailed_at=timezone.now())
        sent += 1
    return sent
