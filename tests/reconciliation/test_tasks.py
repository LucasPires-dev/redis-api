from unittest.mock import patch

import pytest

from apps.reconciliation.models import ReconciliationLead
from apps.reconciliation.tasks import sync_reconciliation_leads

pytestmark = pytest.mark.django_db


def test_sync_reconciliation_leads_task_creates_leads():
    rows = [
        {
            "name": "Novo Convertido",
            "email": "novo-convertido@example.com",
            "phone": "",
            "raw_campus_label": "Arena ILAN",
            "submitted_at": "01/01/2026 10:00:00",
            "raw_row": {"Nome": "Novo Convertido"},
        }
    ]
    with patch("apps.reconciliation.management.commands.sync_reconciliation_leads.fetch_rows", return_value=rows):
        sync_reconciliation_leads()

    assert ReconciliationLead.objects.filter(email="novo-convertido@example.com").exists()
