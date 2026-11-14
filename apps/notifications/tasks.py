import logging

from celery import shared_task

from . import emails, push, reminders

logger = logging.getLogger(__name__)


def run(batch) -> dict:
    """Runs each reminder on its own: one failing doesn't stop the others."""
    results = {}
    for reminder in batch:
        try:
            results[reminder.__name__] = reminder()
        except Exception:
            logger.exception("Reminder %s failed", reminder.__name__)
            results[reminder.__name__] = "error"
    logger.info("Reminders: %s", results)
    return results


@shared_task
def send_notification_emails(notification_ids):
    return emails.send_emails(notification_ids)


@shared_task
def send_notification_push(notification_ids):
    return push.send_push(notification_ids)


@shared_task
def send_hourly_reminders():
    return run(reminders.HOURLY)


@shared_task
def send_daily_reminders():
    return run(reminders.DAILY)


@shared_task
def send_weekly_reminders():
    return run(reminders.WEEKLY)
