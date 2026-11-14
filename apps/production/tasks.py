import logging

from celery import shared_task

from . import series

logger = logging.getLogger(__name__)


@shared_task
def generate_series_events():
    """Keeps each recurring event's next weeks created (CELERY_BEAT_SCHEDULE)."""
    created = series.generate_all()
    logger.info("Recurring events: %s occurrence(s) created", created)
    return created
