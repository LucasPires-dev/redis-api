from celery import shared_task
from django.core.management import call_command


@shared_task
def sync_reconciliation_leads() -> None:
    """Runs periodically via Celery Beat (see CELERY_BEAT_SCHEDULE) — reuses the
    existing management command instead of duplicating the sync logic."""
    call_command("sync_reconciliation_leads")
