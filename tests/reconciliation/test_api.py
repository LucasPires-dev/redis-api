from uuid import uuid4

import pytest

from apps.reconciliation.models import Reconciliation

pytestmark = pytest.mark.django_db


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def test_assign_creates_person_with_unusable_password(
    api_client, manager, campus, reconciliacao_ministry, reconciliador, pending_lead
):
    resp = auth(api_client, manager).post(
        f"/api/reconciliation-leads/{pending_lead.id}/assign/",
        {"reconciliador_id": str(reconciliador.id), "campus_id": str(campus.id)},
    )
    assert resp.status_code == 201
    reconciliation = Reconciliation.objects.get()
    assert reconciliation.convert.has_usable_password() is False
    assert reconciliation.reconciliador_id == reconciliador.id

    pending_lead.refresh_from_db()
    assert pending_lead.status == "assigned"


def test_assign_rejects_reconciliador_not_in_ministry(
    api_client, manager, campus, reconciliacao_ministry, regular_person, pending_lead
):
    resp = auth(api_client, manager).post(
        f"/api/reconciliation-leads/{pending_lead.id}/assign/",
        {"reconciliador_id": str(regular_person.id), "campus_id": str(campus.id)},
    )
    assert resp.status_code == 400
    assert not Reconciliation.objects.exists()


def test_assign_twice_rejected(api_client, manager, campus, reconciliacao_ministry, reconciliador, pending_lead):
    resp = auth(api_client, manager).post(
        f"/api/reconciliation-leads/{pending_lead.id}/assign/",
        {"reconciliador_id": str(reconciliador.id), "campus_id": str(campus.id)},
    )
    assert resp.status_code == 201

    resp = auth(api_client, manager).post(
        f"/api/reconciliation-leads/{pending_lead.id}/assign/",
        {"reconciliador_id": str(reconciliador.id), "campus_id": str(campus.id)},
    )
    assert resp.status_code == 400


def _make_reconciliation(manager, campus, reconciliador):
    convert = manager.__class__.objects.create_user(
        email=f"convertido-{uuid4()}@ilan.church", name="Convertido", campus=campus, password=None
    )
    return Reconciliation.objects.create(
        convert=convert, reconciliador=reconciliador, campus=campus, assigned_by=manager
    )


def test_add_to_cell_rejects_cross_campus_cell(
    api_client, manager, campus, other_campus, reconciliacao_ministry, reconciliador
):
    from apps.churches.models import Cell

    reconciliation = _make_reconciliation(manager, campus, reconciliador)
    other_cell = Cell.objects.create(campus=other_campus, name="GT de Fora")

    resp = auth(api_client, reconciliador).post(
        f"/api/reconciliations/{reconciliation.id}/cell/", {"cell_id": str(other_cell.id)}
    )
    assert resp.status_code == 400


def test_add_to_cell_by_reconciliador_succeeds(
    api_client, manager, campus, reconciliacao_ministry, reconciliador
):
    from apps.churches.models import Cell

    reconciliation = _make_reconciliation(manager, campus, reconciliador)
    cell = Cell.objects.create(campus=campus, name="GT Central")

    resp = auth(api_client, reconciliador).post(
        f"/api/reconciliations/{reconciliation.id}/cell/", {"cell_id": str(cell.id)}
    )
    assert resp.status_code == 201
    assert cell.members.filter(pk=reconciliation.convert_id).exists()
    # Regression: the response itself must reflect in_cell=True immediately, not a
    # stale prefetch-cache value from before the .add() (found by hand-testing).
    assert resp.data["in_cell"] is True


def test_add_to_ministry_response_reflects_serving_immediately(
    api_client, manager, campus, reconciliacao_ministry, reconciliador, brave
):
    """Regression: same staleness issue as in_cell, for the serving flag."""
    reconciliation = _make_reconciliation(manager, campus, reconciliador)

    resp = auth(api_client, reconciliador).post(
        f"/api/reconciliations/{reconciliation.id}/ministry/", {"ministry_id": str(brave.id)}
    )
    assert resp.status_code == 201
    assert resp.data["serving"] is True


def test_add_to_ministry_rejects_cross_campus_ministry(
    api_client, manager, campus, other_campus, reconciliacao_ministry, reconciliador, other_ministry
):
    reconciliation = _make_reconciliation(manager, campus, reconciliador)
    resp = auth(api_client, reconciliador).post(
        f"/api/reconciliations/{reconciliation.id}/ministry/", {"ministry_id": str(other_ministry.id)}
    )
    assert resp.status_code == 400


def test_reconciliador_can_update_jornada(api_client, manager, campus, reconciliacao_ministry, reconciliador):
    reconciliation = _make_reconciliation(manager, campus, reconciliador)
    resp = auth(api_client, reconciliador).patch(
        f"/api/reconciliations/{reconciliation.id}/jornada/", {"jornada_completed": True}
    )
    assert resp.status_code == 200
    reconciliation.refresh_from_db()
    assert reconciliation.jornada_completed is True
    assert reconciliation.jornada_completed_at is not None


def test_reconciliador_cannot_reassign_their_own_convert(
    api_client, manager, campus, reconciliacao_ministry, reconciliador
):
    other_reconciliador = manager.__class__.objects.create_user(
        email="outro-reconciliador@ilan.church", name="Outro", campus=campus, password=None
    )
    reconciliacao_ministry.members.add(other_reconciliador)
    reconciliation = _make_reconciliation(manager, campus, reconciliador)

    resp = auth(api_client, reconciliador).post(
        f"/api/reconciliations/{reconciliation.id}/reassign/",
        {"reconciliador_id": str(other_reconciliador.id)},
    )
    assert resp.status_code == 403


def test_campus_leader_can_reassign(
    api_client, manager, campus, campus_leader, reconciliacao_ministry, reconciliador
):
    other_reconciliador = manager.__class__.objects.create_user(
        email="outro-reconciliador@ilan.church", name="Outro", campus=campus, password=None
    )
    reconciliacao_ministry.members.add(other_reconciliador)
    reconciliation = _make_reconciliation(manager, campus, reconciliador)

    resp = auth(api_client, campus_leader).post(
        f"/api/reconciliations/{reconciliation.id}/reassign/",
        {"reconciliador_id": str(other_reconciliador.id)},
    )
    assert resp.status_code == 200
    reconciliation.refresh_from_db()
    assert reconciliation.reconciliador_id == other_reconciliador.id


def test_dashboard_buckets_by_reconciliador_and_campus(
    api_client, manager, campus, other_campus, reconciliacao_ministry, reconciliador
):
    r1 = _make_reconciliation(manager, campus, reconciliador)
    r1.convert.baptized = True
    r1.convert.save(update_fields=["baptized"])

    other_ministry = reconciliacao_ministry.__class__.objects.create(
        campus=other_campus, kind="reconciliacao", name="Reconciliação Outro"
    )
    other_reconciliador = manager.__class__.objects.create_user(
        email="outro-reconciliador@ilan.church", name="Outro", campus=other_campus, password=None
    )
    other_ministry.members.add(other_reconciliador)
    _make_reconciliation(manager, other_campus, other_reconciliador)

    resp = auth(api_client, manager).get("/api/reconciliations/dashboard/")
    assert resp.status_code == 200
    assert resp.data["overall"]["total"] == 2
    assert resp.data["overall"]["baptized"] == 1
    assert {row["campus"]["id"] for row in resp.data["by_campus"]} == {str(campus.id), str(other_campus.id)}
    assert len(resp.data["by_reconciliador"]) == 2


def test_non_reconciliador_cannot_see_others_reconciliation(
    api_client, manager, campus, reconciliacao_ministry, reconciliador, regular_person
):
    reconciliation = _make_reconciliation(manager, campus, reconciliador)
    resp = auth(api_client, regular_person).get(f"/api/reconciliations/{reconciliation.id}/")
    assert resp.status_code == 404


def test_rank_and_file_reconciliador_can_update_convert_baptized(
    api_client, manager, campus, reconciliacao_ministry, reconciliador
):
    """Regression test: reconciliador is a plain Ministry.members entry (no
    leadership, no campus leadership) — PersonViewSet.get_queryset must still
    surface their assigned convert, or the PATCH 404s before permission is even
    checked (found by hand-testing with a real non-privileged account)."""
    reconciliation = _make_reconciliation(manager, campus, reconciliador)
    resp = auth(api_client, reconciliador).patch(
        f"/api/people/{reconciliation.convert_id}/", {"baptized": True, "baptized_at": "2026-01-01"}
    )
    assert resp.status_code == 200
    reconciliation.convert.refresh_from_db()
    assert reconciliation.convert.baptized is True


def test_regular_person_still_cannot_update_someone_elses_baptized(
    api_client, manager, campus, reconciliacao_ministry, reconciliador, regular_person
):
    reconciliation = _make_reconciliation(manager, campus, reconciliador)
    resp = auth(api_client, regular_person).patch(
        f"/api/people/{reconciliation.convert_id}/", {"baptized": True}
    )
    assert resp.status_code == 404


def test_rank_and_file_reconciliador_sees_available_cells_and_ministries_at_campus(
    api_client, manager, campus, other_campus, reconciliacao_ministry, reconciliador, other_ministry
):
    """Regression test: a rank-and-file reconciliador has no read access to the
    generic /api/cells//api/ministries/ endpoints (scope_cells/scope_ministries
    only grant visibility via leadership or membership in that specific
    cell/ministry) — the dedicated available-cells/available-ministries actions
    must work regardless."""
    from apps.churches.models import Cell

    reconciliation = _make_reconciliation(manager, campus, reconciliador)
    cell = Cell.objects.create(campus=campus, name="GT Central")
    Cell.objects.create(campus=other_campus, name="GT de Fora")  # different campus, must not appear

    resp = auth(api_client, reconciliador).get(f"/api/reconciliations/{reconciliation.id}/available-cells/")
    assert resp.status_code == 200
    assert [c["id"] for c in resp.data] == [str(cell.id)]

    resp = auth(api_client, reconciliador).get(
        f"/api/reconciliations/{reconciliation.id}/available-ministries/"
    )
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.data}
    assert ids == {str(reconciliacao_ministry.id)}  # only this campus's ministry, not other_ministry
