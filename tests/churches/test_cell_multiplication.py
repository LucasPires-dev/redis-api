"""Leader in training, the multiplication plan, the "multiply" action and the
generations (lineage) of a cell."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import CellRole
from apps.churches.models import Cell, CellLeadership, Season
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def season(church):
    return Season.objects.create(
        church=church, name="Atual", start_date=TODAY - timedelta(weeks=4), end_date=TODAY + timedelta(weeks=8)
    )


@pytest.fixture
def people(campus):
    return {
        key: make_person(email=f"{key}.mult@ilan.church", name=key.capitalize(), campus=campus)
        for key in ("lider", "colider", "timoteo", "ana", "bia", "caio")
    }


@pytest.fixture
def cell(campus, season, people):
    cell = make_cell(campus=campus, season=season, name="GT Mãe")
    CellLeadership.objects.create(cell=cell, person=people["lider"], role=CellRole.LEADER)
    CellLeadership.objects.create(cell=cell, person=people["colider"], role=CellRole.CO_LEADER)
    cell.members.add(people["timoteo"], people["ana"], people["bia"], people["caio"])
    return cell


def as_user(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


# --- Líder em treinamento ---------------------------------------------------------


def test_cell_leader_appoints_and_removes_a_leader_in_training(api_client, cell, people):
    client = as_user(api_client, people["lider"])

    response = client.post(
        f"/api/cells/{cell.id}/leaderships/", {"person_id": str(people["timoteo"].id), "role": "trainee"}
    )
    assert response.status_code == 201
    detail = client.get(f"/api/cells/{cell.id}/").data
    assert [p["name"] for p in detail["trainees"]] == ["Timoteo"]
    assert detail["can_appoint_trainee"] is True

    assert client.delete(f"/api/cells/{cell.id}/leaderships/{people['timoteo'].id}/").status_code == 204
    assert not cell.leaderships.filter(role=CellRole.TRAINEE).exists()


def test_cell_leader_cannot_name_co_leaders_nor_demote_them(api_client, cell, people):
    client = as_user(api_client, people["lider"])
    url = f"/api/cells/{cell.id}/leaderships/"

    assert client.post(url, {"person_id": str(people["ana"].id), "role": "co_leader"}).status_code == 403
    # The co-leader can't be turned into a trainee by the leader either.
    assert client.post(url, {"person_id": str(people["colider"].id), "role": "trainee"}).status_code == 403
    assert client.delete(f"{url}{people['colider'].id}/").status_code == 403
    assert cell.leaderships.get(person=people["colider"]).role == CellRole.CO_LEADER


def test_co_leader_cannot_appoint_trainees(api_client, cell, people):
    client = as_user(api_client, people["colider"])
    assert client.get(f"/api/cells/{cell.id}/").data["can_appoint_trainee"] is False
    response = client.post(
        f"/api/cells/{cell.id}/leaderships/", {"person_id": str(people["ana"].id), "role": "trainee"}
    )
    assert response.status_code == 403


# --- Plano de multiplicação ------------------------------------------------------


def test_leadership_sets_and_clears_the_multiplication_plan(api_client, cell, people):
    client = as_user(api_client, people["lider"])
    url = f"/api/cells/{cell.id}/multiplication-plan/"
    date = (TODAY + timedelta(weeks=6)).isoformat()

    data = client.put(url, {"date": date, "leader_id": str(people["timoteo"].id)}, format="json").data
    assert data["multiplication_date"] == date
    assert data["multiplication_leader"]["name"] == "Timoteo"

    stranger = make_person(email="fora.mult@ilan.church", name="Fora", campus=cell.campus)
    response = client.put(url, {"date": date, "leader_id": str(stranger.id)}, format="json")
    assert response.status_code == 400

    data = client.delete(url).data
    assert data["multiplication_date"] is None
    assert data["multiplication_leader"] is None


def test_plain_member_cannot_set_the_plan(api_client, cell, people):
    response = as_user(api_client, people["ana"]).put(
        f"/api/cells/{cell.id}/multiplication-plan/", {"date": None, "leader_id": None}, format="json"
    )
    assert response.status_code == 403


# --- Multiplicar ----------------------------------------------------------------


def test_coordinator_multiplies_the_cell(api_client, campus_leader, cell, people):
    CellLeadership.objects.create(cell=cell, person=people["timoteo"], role=CellRole.TRAINEE)
    cell.multiplication_date = TODAY
    cell.multiplication_leader = people["timoteo"]
    cell.save()

    response = as_user(api_client, campus_leader).post(
        f"/api/cells/{cell.id}/multiply/",
        {
            "name": "GT Filha",
            "leader_id": str(people["timoteo"].id),
            "member_ids": [str(people["ana"].id), str(people["bia"].id)],
            "meeting_day": 5,
        },
        format="json",
    )

    assert response.status_code == 201
    daughter = Cell.objects.get(pk=response.data["id"])
    assert daughter.parent_cell == cell
    assert daughter.multiplied_on == TODAY
    assert daughter.season == cell.season and daughter.campus == cell.campus
    assert daughter.meeting_day == 5
    assert daughter.meeting_time == cell.meeting_time  # the mother's, when left out
    assert [p.name for p in daughter.leaders()] == ["Timoteo"]
    assert set(daughter.members.values_list("name", flat=True)) == {"Ana", "Bia"}

    cell.refresh_from_db()
    # Who moved left the mother; the plan is done.
    assert set(cell.members.values_list("name", flat=True)) == {"Caio"}
    assert not cell.leaderships.filter(person=people["timoteo"]).exists()
    assert cell.multiplication_date is None and cell.multiplication_leader is None


def test_cell_leader_cannot_multiply(api_client, cell, people):
    response = as_user(api_client, people["lider"]).post(
        f"/api/cells/{cell.id}/multiply/", {"name": "GT Filha", "leader_id": str(people["ana"].id)}, format="json"
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"name": "GT Filha", "leader_id": "lider"}, "leader_id"),
        ({"name": "GT Filha", "leader_id": "ana", "member_ids": ["lider"]}, "member_ids"),
        ({"name": "GT Mãe", "leader_id": "ana"}, "name"),
    ],
)
def test_multiply_validation(api_client, campus_leader, cell, people, payload, field):
    resolve = {k: str(people[v].id) for k, v in payload.items() if k == "leader_id"}
    body = {**payload, **resolve}
    if "member_ids" in payload:
        body["member_ids"] = [str(people[k].id) for k in payload["member_ids"]]

    response = as_user(api_client, campus_leader).post(f"/api/cells/{cell.id}/multiply/", body, format="json")

    assert response.status_code == 400
    assert field in response.data
    assert not Cell.objects.filter(parent_cell=cell).exists()


# --- Gerações -------------------------------------------------------------------


def test_lineage_across_generations_and_seasons(api_client, campus_leader, church, cell, people):
    client = as_user(api_client, campus_leader)
    daughter_id = client.post(
        f"/api/cells/{cell.id}/multiply/", {"name": "GT Filha", "leader_id": str(people["ana"].id)}, format="json"
    ).data["id"]
    # Bia stayed in the mother: put her in the daughter, then multiply it.
    Cell.objects.get(pk=daughter_id).members.add(people["bia"])
    granddaughter_id = client.post(
        f"/api/cells/{daughter_id}/multiply/", {"name": "GT Neta", "leader_id": str(people["bia"].id)}, format="json"
    ).data["id"]

    lineage = client.get(f"/api/cells/{granddaughter_id}/lineage/").data
    assert lineage["generation"] == 3
    assert [c["name"] for c in lineage["ancestors"]] == ["GT Filha", "GT Mãe"]

    mother = client.get(f"/api/cells/{cell.id}/lineage/").data
    assert mother["generation"] == 1
    assert [(d["name"], d["leaders"]) for d in mother["daughters"]] == [("GT Filha", ["Ana"])]

    # Renewed into the next season, the mother still shows the daughter born
    # from its earlier version — and the copy doesn't count as a new one.
    next_season = Season.objects.create(
        church=church, name="Próxima", start_date=TODAY + timedelta(weeks=9), end_date=TODAY + timedelta(weeks=30)
    )
    renewed_id = client.post(f"/api/cells/{daughter_id}/renew/", {"season_id": str(next_season.id)}).data["id"]
    renewed = Cell.objects.get(pk=renewed_id)
    assert renewed.parent_cell_id == cell.pk and renewed.multiplied_on is None
    assert [d["name"] for d in client.get(f"/api/cells/{renewed_id}/lineage/").data["daughters"]] == ["GT Neta"]


def test_report_counts_multiplications_and_trainees(api_client, campus_leader, cell, people):
    CellLeadership.objects.create(cell=cell, person=people["caio"], role=CellRole.TRAINEE)
    client = as_user(api_client, campus_leader)
    client.post(
        f"/api/cells/{cell.id}/multiply/", {"name": "GT Filha", "leader_id": str(people["ana"].id)}, format="json"
    )

    data = client.get("/api/cells/report/", {"season": "current"}).data
    rows = {row["name"]: row for row in data["cells"]}
    assert rows["GT Mãe"]["multiplications"] == 1
    assert rows["GT Mãe"]["trainees"] == ["Caio"]
    assert rows["GT Filha"]["parent"] == {"id": str(cell.id), "name": "GT Mãe"}
    assert data["totals"]["multiplications"] == 1
    assert data["totals"]["trainees"] == 1
