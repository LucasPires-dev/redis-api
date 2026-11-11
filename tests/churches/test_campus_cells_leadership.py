from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import LeadershipRole
from apps.churches.models import CampusCellsLeadership, Season
from tests.conftest import CELL_REQUIRED_PAYLOAD, make_cell, make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def season(church):
    return Season.objects.create(
        church=church, name="Atual", start_date=TODAY - timedelta(days=10), end_date=TODAY + timedelta(days=90)
    )


@pytest.fixture
def next_season(church, season):
    start = season.end_date + timedelta(days=1)
    return Season.objects.create(church=church, name="Próxima", start_date=start, end_date=start + timedelta(days=90))


@pytest.fixture
def campus_cells_leader(campus):
    person = make_person(email="celulas.campus@ilan.church", name="Resp Células Campus", campus=campus)
    CampusCellsLeadership.objects.create(campus=campus, person=person, role=LeadershipRole.LEADER)
    return person


@pytest.fixture
def cell(campus, season):
    return make_cell(campus=campus, season=season, name="GT Vida")


@pytest.fixture
def other_cell(other_campus, season):
    return make_cell(campus=other_campus, season=season, name="GT Fora")


def cell_payload(campus, season, name="GT Novo"):
    return {"campus_id": str(campus.id), "season_id": str(season.id), "name": name, **CELL_REQUIRED_PAYLOAD}


# --- Assigning the campus cells leader -----------------------------------------------


def test_campus_leader_assigns_campus_cells_leader(api_client, campus_leader, campus, regular_person):
    api_client.force_authenticate(campus_leader)
    response = api_client.post(
        f"/api/campuses/{campus.id}/cells-leaderships/",
        {"person_id": str(regular_person.id), "role": "leader"},
        format="json",
    )
    assert response.status_code == 201, response.data
    listed = api_client.get(f"/api/campuses/{campus.id}/cells-leaderships/").data
    assert [row["person"]["id"] for row in listed] == [str(regular_person.id)]


def test_campus_leader_cannot_assign_on_another_campus(api_client, campus_leader, other_campus):
    outsider = make_person(email="x@ilan.church", name="X", campus=other_campus)
    api_client.force_authenticate(campus_leader)
    response = api_client.post(
        f"/api/campuses/{other_campus.id}/cells-leaderships/",
        {"person_id": str(outsider.id), "role": "leader"},
        format="json",
    )
    assert response.status_code == 403


def test_campus_cells_leader_must_belong_to_campus(api_client, campus_leader, campus, other_campus):
    outsider = make_person(email="x@ilan.church", name="X", campus=other_campus)
    api_client.force_authenticate(campus_leader)
    response = api_client.post(
        f"/api/campuses/{campus.id}/cells-leaderships/",
        {"person_id": str(outsider.id), "role": "leader"},
        format="json",
    )
    assert response.status_code == 400


def test_regular_person_cannot_assign_campus_cells_leader(api_client, regular_person, campus):
    api_client.force_authenticate(regular_person)
    response = api_client.post(
        f"/api/campuses/{campus.id}/cells-leaderships/",
        {"person_id": str(regular_person.id), "role": "leader"},
        format="json",
    )
    assert response.status_code == 403


def test_campus_leader_removes_campus_cells_leader(api_client, campus_leader, campus, campus_cells_leader):
    api_client.force_authenticate(campus_leader)
    url = f"/api/campuses/{campus.id}/cells-leaderships/{campus_cells_leader.id}/"
    assert api_client.delete(url).status_code == 204
    assert not CampusCellsLeadership.objects.exists()


# --- What the campus cells leader can do --------------------------------------------------


def test_campus_cells_leader_sees_only_their_campus_cells(api_client, campus_cells_leader, cell, other_cell):
    api_client.force_authenticate(campus_cells_leader)
    names = [c["name"] for c in api_client.get("/api/cells/").data["results"]]
    assert names == ["GT Vida"]


def test_campus_cells_leader_creates_cell_in_their_campus(api_client, campus_cells_leader, campus, season):
    api_client.force_authenticate(campus_cells_leader)
    response = api_client.post("/api/cells/", cell_payload(campus, season), format="json")
    assert response.status_code == 201, response.data
    assert response.data["can_manage"] is True


def test_campus_cells_leader_cannot_create_cell_elsewhere(api_client, campus_cells_leader, other_campus, season):
    api_client.force_authenticate(campus_cells_leader)
    response = api_client.post("/api/cells/", cell_payload(other_campus, season), format="json")
    assert response.status_code == 403


def test_campus_cells_leader_cannot_move_cell_to_another_campus(api_client, campus_cells_leader, cell, other_campus):
    api_client.force_authenticate(campus_cells_leader)
    response = api_client.patch(f"/api/cells/{cell.id}/", {"campus_id": str(other_campus.id)}, format="json")
    assert response.status_code == 403


def test_campus_cells_leader_assigns_cell_leader(api_client, campus_cells_leader, cell, regular_person):
    api_client.force_authenticate(campus_cells_leader)
    response = api_client.post(
        f"/api/cells/{cell.id}/leaderships/", {"person_id": str(regular_person.id), "role": "leader"}, format="json"
    )
    assert response.status_code == 201


def test_campus_cells_leader_renews_cell(api_client, campus_cells_leader, cell, next_season):
    api_client.force_authenticate(campus_cells_leader)
    response = api_client.post(f"/api/cells/{cell.id}/renew/", {"season_id": str(next_season.id)}, format="json")
    assert response.status_code == 201


def test_campus_cells_leader_cannot_create_season(api_client, campus_cells_leader, church):
    api_client.force_authenticate(campus_cells_leader)
    response = api_client.post(
        "/api/seasons/",
        {"church_id": str(church.id), "name": "X", "start_date": "2030-01-01", "end_date": "2030-06-30"},
        format="json",
    )
    assert response.status_code == 403


def test_campus_leader_creates_cell_in_their_campus(api_client, campus_leader, campus, season):
    api_client.force_authenticate(campus_leader)
    response = api_client.post("/api/cells/", cell_payload(campus, season), format="json")
    assert response.status_code == 201


def test_me_lists_campuses_cells(api_client, campus_cells_leader, campus):
    api_client.force_authenticate(campus_cells_leader)
    data = api_client.get("/api/auth/me/").data
    assert [c["id"] for c in data["led_campuses_cells"]] == [str(campus.id)]


# --- People pickers -------------------------------------------------------------------


def test_cell_people_search_for_members_spans_campuses(api_client, campus_cells_leader, cell, other_campus):
    make_person(email="ana.fora@ilan.church", name="Ana de Fora", campus=other_campus)
    api_client.force_authenticate(campus_cells_leader)
    data = api_client.get(f"/api/cells/{cell.id}/people-search/?q=ana").data
    assert [p["name"] for p in data] == ["Ana de Fora"]
    assert set(data[0]) == {"id", "name", "email"}


def test_cell_people_search_for_leaders_only_cell_campus(api_client, campus_cells_leader, cell, campus, other_campus):
    make_person(email="ana.fora@ilan.church", name="Ana de Fora", campus=other_campus)
    make_person(email="ana.daqui@ilan.church", name="Ana Daqui", campus=campus)
    api_client.force_authenticate(campus_cells_leader)
    data = api_client.get(f"/api/cells/{cell.id}/people-search/?q=ana&for=leaders").data
    assert [p["name"] for p in data] == ["Ana Daqui"]


def test_people_search_needs_two_characters(api_client, campus_cells_leader, cell):
    api_client.force_authenticate(campus_cells_leader)
    assert api_client.get(f"/api/cells/{cell.id}/people-search/?q=a").data == []


def test_cell_member_cannot_search_people(api_client, regular_person, cell):
    cell.members.add(regular_person)
    api_client.force_authenticate(regular_person)
    assert api_client.get(f"/api/cells/{cell.id}/people-search/?q=an").status_code == 403


def test_campus_people_search_for_campus_leader(api_client, campus_leader, campus, other_campus):
    make_person(email="bia@ilan.church", name="Bia Daqui", campus=campus)
    make_person(email="bia.fora@ilan.church", name="Bia de Fora", campus=other_campus)
    api_client.force_authenticate(campus_leader)
    data = api_client.get(f"/api/campuses/{campus.id}/people-search/?q=bia").data
    assert [p["name"] for p in data] == ["Bia Daqui"]
