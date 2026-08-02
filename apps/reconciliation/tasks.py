from celery import shared_task
from django.core.management import call_command


@shared_task
def sync_reconciliation_leads() -> None:
    """Roda periodicamente via Celery Beat (ver CELERY_BEAT_SCHEDULE) — reaproveita
    o management command existente em vez de duplicar a lógica de sincronização."""
    call_command("sync_reconciliation_leads")
