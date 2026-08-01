import pytest

from apps.reconciliation.models import Reconciliation
from apps.reconciliation.permissions import (
    can_manage_reconciliation,
    can_triage_campus,
    can_triage_lead,
    is_reconciliador_of,
    scope_reconciliation_leads,
    scope_reconciliations,
)

pytestmark = pytest.mark.django_db


def test_can_triage_lead_unassigned_visible_to_campus_leader(campus_leader, pending_lead):
    assert can_triage_lead(campus_leader, pending_lead)


def test_can_triage_lead_unassigned_not_visible_to_regular_person(regular_person, pending_lead):
    assert not can_triage_lead(regular_person, pending_lead)


def test_can_triage_lead_unassigned_visible_to_local_reconciliacao_leader(
    reconciliador, reconciliacao_ministry, pending_lead
):
    from apps.churches.choices import LeadershipRole
    from apps.churches.models import MinistryLeadership

    MinistryLeadership.objects.create(
        ministry=reconciliacao_ministry, person=reconciliador, role=LeadershipRole.LEADER
    )
    assert can_triage_lead(reconciliador, pending_lead)


def test_can_triage_campus_recognizes_campus_leader_and_local_ministry_leader(
    campus_leader, campus, reconciliador, reconciliacao_ministry
):
    assert can_triage_campus(campus_leader, campus)
    assert not can_triage_campus(reconciliador, campus)  # plain member, not a leader


def test_can_manage_reconciliation_grants_assigned_reconciliador(reconciliador, campus, manager):
    convert = manager.__class__.objects.create_user(
        email="convertido@ilan.church", name="Convertido", campus=campus, password=None
    )
    reconciliation = Reconciliation.objects.create(
        convert=convert, reconciliador=reconciliador, campus=campus, assigned_by=manager
    )
    assert can_manage_reconciliation(reconciliador, reconciliation)
    assert is_reconciliador_of(reconciliador, convert)


def test_scope_reconciliation_leads_regular_person_sees_nothing(regular_person, pending_lead):
    from apps.reconciliation.models import ReconciliationLead

    result = scope_reconciliation_leads(ReconciliationLead.objects.all(), regular_person)
    assert list(result) == []


def test_scope_reconciliations_reconciliador_sees_only_their_assignment(
    reconciliador, campus, manager, other_campus
):
    convert = manager.__class__.objects.create_user(
        email="convertido2@ilan.church", name="Convertido2", campus=campus, password=None
    )
    other_reconciliador = manager.__class__.objects.create_user(
        email="outro-reconciliador@ilan.church", name="Outro Reconciliador", campus=other_campus, password=None
    )
    mine = Reconciliation.objects.create(
        convert=convert, reconciliador=reconciliador, campus=campus, assigned_by=manager
    )
    other_convert = manager.__class__.objects.create_user(
        email="convertido3@ilan.church", name="Convertido3", campus=other_campus, password=None
    )
    Reconciliation.objects.create(
        convert=other_convert, reconciliador=other_reconciliador, campus=other_campus, assigned_by=manager
    )
    result = set(scope_reconciliations(Reconciliation.objects.all(), reconciliador))
    assert result == {mine}
