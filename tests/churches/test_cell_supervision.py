"""Supervisão: coordination names a cell's supervisor; the supervisor follows
the cell (manages it, names its leaders, registers visits) and the visits are
seen only by the supervisor and coordination."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import CellRole
from apps.churches.models import Cell, CellLeadership, CellSupervisionVisit, Season
from apps.people.capabilities import cells_capabilities
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def season(church):
    return Season.objects.create(
        church=church, name="Atual", start_date=TODAY - timedelta(weeks=4), end_date=TODAY + timedelta(weeks=8)
    )


@pytest.fixture
def cell(campus, season):
    cell = make_cell(campus=campus, season=season, name="GT Supervisionada")
    leader = make_person(email="lider.sup@ilan.church", name="Líder Sup", campus=campus)
    CellLeadership.objects.create(cell=cell, person=leader, role=CellRole.LEADER)
    cell.leader = leader
    return cell


@pytest.fixture
def other_cell(campus, season):
    return make_cell(campus=campus, season=season, name="GT Outra")


@pytest.fixture
def supervisor(campus, cell):
    person = make_person(email="supervisor@ilan.church", name="Supervisora", campus=campus)
    cell.supervisor = person
    cell.save()
    return person


def as_user(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


# --- Naming the supervisor --------------------------------------------------------


def test_coordination_names_and_clears_the_supervisor(api_client, campus_leader, campus, cell):
    person = make_person(email="nova.sup@ilan.church", name="Nova Sup", campus=campus)
    client = as_user(api_client, campus_leader)
    url = f"/api/cells/{cell.id}/supervisor/"

    data = client.put(url, {"person_id": str(person.id)}, format="json").data
    assert data["supervisor"]["name"] == "Nova Sup"

    assert client.put(url, {"person_id": None}, format="json").data["supervisor"] is None


def test_supervisor_must_be_of_the_cells_campus(api_client, campus_leader, other_campus, cell):
    outsider = make_person(email="fora.sup@ilan.church", name="Fora", campus=other_campus)
    response = as_user(api_client, campus_leader).put(
        f"/api/cells/{cell.id}/supervisor/", {"person_id": str(outsider.id)}, format="json"
    )
    assert response.status_code == 400


def test_neither_the_leader_nor_the_supervisor_names_supervisors(api_client, cell, supervisor):
    for person in (cell.leader, supervisor):
        response = as_user(api_client, person).put(
            f"/api/cells/{cell.id}/supervisor/", {"person_id": str(person.id)}, format="json"
        )
        assert response.status_code == 403


# --- What the supervisor does -----------------------------------------------------


def test_supervisor_follows_and_manages_the_cell(api_client, cell, other_cell, supervisor):
    client = as_user(api_client, supervisor)

    assert cells_capabilities(supervisor)["supervise"] is True
    listed = client.get("/api/cells/", {"supervised": "true"}).data
    assert [c["name"] for c in listed["results"]] == ["GT Supervisionada"]

    detail = client.get(f"/api/cells/{cell.id}/").data
    assert detail["can_manage"] is True
    assert detail["can_supervise"] is True
    assert detail["can_coordinate"] is False  # doesn't renew nor multiply
    assert detail["health"] is not None
    assert client.get(f"/api/cells/{cell.id}/care/").status_code == 200

    # A cell they don't supervise isn't theirs.
    assert client.get(f"/api/cells/{other_cell.id}/").status_code == 404


def test_supervisor_names_the_leaders_of_their_cells_only(api_client, campus, cell, other_cell, supervisor):
    new_leader = make_person(email="co.sup@ilan.church", name="Colíder Sup", campus=campus)
    client = as_user(api_client, supervisor)

    response = client.post(
        f"/api/cells/{cell.id}/leaderships/", {"person_id": str(new_leader.id), "role": "co_leader"}
    )
    assert response.status_code == 201
    assert client.delete(f"/api/cells/{cell.id}/leaderships/{new_leader.id}/").status_code == 204

    response = client.post(
        f"/api/cells/{other_cell.id}/leaderships/", {"person_id": str(new_leader.id), "role": "leader"}
    )
    assert response.status_code in (403, 404)


def test_renewal_keeps_the_supervisor(api_client, campus_leader, church, cell, supervisor):
    next_season = Season.objects.create(
        church=church, name="Próxima", start_date=TODAY + timedelta(weeks=9), end_date=TODAY + timedelta(weeks=30)
    )
    renewed_id = as_user(api_client, campus_leader).post(
        f"/api/cells/{cell.id}/renew/", {"season_id": str(next_season.id)}
    ).data["id"]
    assert Cell.objects.get(pk=renewed_id).supervisor == supervisor


# --- Visits -------------------------------------------------------------------------


def test_supervisor_registers_visits_seen_only_by_supervision(api_client, campus_leader, cell, supervisor):
    client = as_user(api_client, supervisor)
    response = client.post(
        "/api/cell-supervision-visits/",
        {
            "cell_id": str(cell.id),
            "date": (TODAY - timedelta(days=2)).isoformat(),
            "kind": "meeting",
            "notes": "Encontro animado; líder precisa de apoio no louvor.",
        },
    )
    assert response.status_code == 201
    assert response.data["created_by"]["name"] == "Supervisora"
    assert response.data["kind_display"] == "Visita ao encontro"
    assert client.get(f"/api/cells/{cell.id}/").data["last_supervision_visit"] == TODAY - timedelta(days=2)

    # Coordination sees it; the cell's leader doesn't, nor its date.
    coordination = as_user(api_client, campus_leader).get("/api/cell-supervision-visits/", {"cell": str(cell.id)})
    assert len(coordination.data["results"]) == 1

    leader = as_user(api_client, cell.leader)
    assert leader.get("/api/cell-supervision-visits/", {"cell": str(cell.id)}).data["results"] == []
    assert leader.get(f"/api/cells/{cell.id}/").data["last_supervision_visit"] is None
    response = leader.post(
        "/api/cell-supervision-visits/", {"cell_id": str(cell.id), "date": TODAY.isoformat(), "kind": "leadership"}
    )
    assert response.status_code == 403


def test_only_the_author_or_coordination_edits_a_visit(api_client, campus, campus_leader, cell, supervisor):
    visit = CellSupervisionVisit.objects.create(cell=cell, date=TODAY, created_by=supervisor)
    # A new supervisor takes over the cell: reads the old visit, can't edit it.
    successor = make_person(email="sucessor@ilan.church", name="Sucessor", campus=campus)
    cell.supervisor = successor
    cell.save()

    url = f"/api/cell-supervision-visits/{visit.id}/"
    assert as_user(api_client, successor).patch(url, {"notes": "x"}).status_code == 403
    # The former supervisor no longer sees the cell's supervision.
    assert as_user(api_client, supervisor).patch(url, {"notes": "Ajustado"}).status_code == 404
    assert as_user(api_client, campus_leader).patch(url, {"notes": "Pela coordenação"}).status_code == 200


def test_report_shows_supervisor_and_last_visit(api_client, campus_leader, cell, supervisor):
    CellSupervisionVisit.objects.create(cell=cell, date=TODAY - timedelta(days=5), created_by=supervisor)
    rows = as_user(api_client, campus_leader).get("/api/cells/report/", {"season": "current"}).data["cells"]
    row = next(r for r in rows if r["name"] == "GT Supervisionada")
    assert row["supervisor"] == "Supervisora"
    assert row["last_supervision_visit"] == TODAY - timedelta(days=5)
