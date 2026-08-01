from datetime import datetime
from unittest.mock import patch

import pytest

from apps.reconciliation.choices import LeadStatus
from apps.reconciliation.models import ReconciliationLead
from apps.reconciliation.sheets import _normalize_header, compute_dedup_key


def test_compute_dedup_key_is_stable_for_same_identity_and_timestamp():
    ts = datetime(2026, 1, 1, 10, 0, 0)
    key1 = compute_dedup_key("a@b.com", "", ts)
    key2 = compute_dedup_key("A@B.com ", "", ts)
    assert key1 == key2


def test_compute_dedup_key_differs_for_different_timestamps():
    key1 = compute_dedup_key("a@b.com", "", datetime(2026, 1, 1))
    key2 = compute_dedup_key("a@b.com", "", datetime(2026, 1, 2))
    assert key1 != key2


def test_column_alias_matching_is_case_and_whitespace_insensitive():
    assert _normalize_header("  Nome Completo  ") == "nome completo"
    assert _normalize_header("E-MAIL") == "e-mail"


@pytest.mark.django_db
def test_sync_command_is_idempotent():
    from django.core.management import call_command

    rows = [
        {
            "name": "Fulano",
            "email": "fulano@example.com",
            "phone": "",
            "raw_campus_label": "Arena ILAN",
            "submitted_at": "01/01/2026 10:00:00",
            "raw_row": {"Nome": "Fulano"},
        },
        {
            "name": "Ciclana",
            "email": "ciclana@example.com",
            "phone": "",
            "raw_campus_label": "Arena ILAN",
            "submitted_at": "01/01/2026 11:00:00",
            "raw_row": {"Nome": "Ciclana"},
        },
    ]
    with patch("apps.reconciliation.management.commands.sync_reconciliation_leads.fetch_rows", return_value=rows):
        call_command("sync_reconciliation_leads")
        call_command("sync_reconciliation_leads")

    assert ReconciliationLead.objects.count() == 2

    lead = ReconciliationLead.objects.get(email="fulano@example.com")
    lead.status = LeadStatus.ASSIGNED
    lead.save(update_fields=["status"])

    with patch("apps.reconciliation.management.commands.sync_reconciliation_leads.fetch_rows", return_value=rows):
        call_command("sync_reconciliation_leads")

    lead.refresh_from_db()
    assert lead.status == LeadStatus.ASSIGNED
    assert ReconciliationLead.objects.count() == 2
