from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import LeadershipRole
from apps.churches.models import Cell, CellGeneralLeadership, CellLeadership, CellMeeting, Season
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def current_season(church):
    return Season.objects.create(
        church=church, name="Atual", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=60)
    )


@pytest.fixture
def next_season(church, current_season):
    start = current_season.end_date + timedelta(days=1)
    return Season.objects.create(church=church, name="Próxima", start_date=start, end_date=start + timedelta(days=120))


@pytest.fixture
def past_season(church, current_season):
    end = current_season.start_date - timedelta(days=1)
    return Season.objects.create(church=church, name="Passada", start_date=end - timedelta(days=120), end_date=end)


@pytest.fixture
def cells_leader(church, campus):
    person = make_person(email="geral.celulas@ilan.church", name="Geral Células", campus=campus)
    leadership = CellGeneralLeadership.objects.create(church=church)
    leadership.leaders.add(person)
    return person


@pytest.fixture
def cell(campus, current_season):
    return Cell.objects.create(campus=campus, season=current_season, name="GT Vida")


@pytest.fixture
def cell_leader(campus, cell):
    person = make_person(email="lider.gt@ilan.church", name="Líder GT", campus=campus)
    CellLeadership.objects.create(cell=cell, person=person, role=LeadershipRole.LEADER)
    return person


@pytest.fixture
def member(campus, cell):
    person = make_person(email="membro.gt@ilan.church", name="Membro GT", campus=campus)
    cell.members.add(person)
    return person


# --- Seasons -----------------------------------------------------------------


def test_current_season_is_detected_from_today(api_client, regular_person, current_season, next_season, past_season):
    api_client.force_authenticate(regular_person)
    response = api_client.get("/api/seasons/current/")
    assert response.status_code == 200
    assert response.data["id"] == str(current_season.id)
    assert response.data["is_current"] is True


def test_current_season_404_between_seasons(api_client, regular_person, church):
    Season.objects.create(church=church, name="Futura", start_date=TODAY + timedelta(days=10), end_date=TODAY + timedelta(days=20))
    api_client.force_authenticate(regular_person)
    assert api_client.get("/api/seasons/current/").status_code == 404


def test_cells_general_leader_creates_season(api_client, cells_leader, church):
    api_client.force_authenticate(cells_leader)
    response = api_client.post(
        "/api/seasons/",
        {"church_id": str(church.id), "name": "2027.1", "start_date": "2027-02-01", "end_date": "2027-06-30"},
        format="json",
    )
    assert response.status_code == 201, response.data


def test_regular_person_cannot_create_season(api_client, regular_person, church):
    api_client.force_authenticate(regular_person)
    response = api_client.post(
        "/api/seasons/",
        {"church_id": str(church.id), "name": "X", "start_date": "2027-02-01", "end_date": "2027-06-30"},
        format="json",
    )
    assert response.status_code == 403


def test_overlapping_season_is_rejected(api_client, manager, church, current_season):
    api_client.force_authenticate(manager)
    response = api_client.post(
        "/api/seasons/",
        {
            "church_id": str(church.id),
            "name": "Sobreposta",
            "start_date": str(current_season.end_date),
            "end_date": str(current_season.end_date + timedelta(days=30)),
        },
        format="json",
    )
    assert response.status_code == 400


def test_season_end_before_start_is_rejected(api_client, manager, church):
    api_client.force_authenticate(manager)
    response = api_client.post(
        "/api/seasons/",
        {"church_id": str(church.id), "name": "Invertida", "start_date": "2027-06-30", "end_date": "2027-02-01"},
        format="json",
    )
    assert response.status_code == 400


def test_season_with_cells_cannot_be_deleted(api_client, manager, cell, current_season):
    api_client.force_authenticate(manager)
    assert api_client.delete(f"/api/seasons/{current_season.id}/").status_code == 400


# --- Cells within seasons ------------------------------------------------------


def test_cells_general_leader_creates_cell_and_sees_all(api_client, cells_leader, campus, other_campus, current_season):
    Cell.objects.create(campus=other_campus, season=current_season, name="GT Fora")
    api_client.force_authenticate(cells_leader)
    response = api_client.post(
        "/api/cells/",
        {"campus_id": str(campus.id), "season_id": str(current_season.id), "name": "GT Novo"},
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["can_manage"] is True
    assert response.data["effective_start"] == str(current_season.start_date)
    names = [c["name"] for c in api_client.get("/api/cells/").data["results"]]
    assert sorted(names) == ["GT Fora", "GT Novo"]


def test_cell_leader_cannot_create_cell(api_client, cell_leader, campus, current_season):
    api_client.force_authenticate(cell_leader)
    response = api_client.post(
        "/api/cells/",
        {"campus_id": str(campus.id), "season_id": str(current_season.id), "name": "GT Meu"},
        format="json",
    )
    assert response.status_code == 403


def test_cell_requires_season(api_client, manager, campus):
    api_client.force_authenticate(manager)
    response = api_client.post("/api/cells/", {"campus_id": str(campus.id), "name": "Sem temporada"}, format="json")
    assert response.status_code == 400
    assert "season_id" in response.data


def test_cell_dates_must_be_within_season(api_client, manager, cell, current_season):
    api_client.force_authenticate(manager)
    response = api_client.patch(
        f"/api/cells/{cell.id}/", {"end_date": str(current_season.end_date + timedelta(days=1))}, format="json"
    )
    assert response.status_code == 400
    assert "end_date" in response.data


def test_filter_cells_by_current_season(api_client, manager, campus, cell, past_season):
    Cell.objects.create(campus=campus, season=past_season, name="GT Antigo")
    api_client.force_authenticate(manager)
    names = [c["name"] for c in api_client.get("/api/cells/?season=current").data["results"]]
    assert names == ["GT Vida"]
    names = [c["name"] for c in api_client.get(f"/api/cells/?season={past_season.id}").data["results"]]
    assert names == ["GT Antigo"]


def test_active_cells_respect_cell_end_date(campus, current_season):
    Cell.objects.create(campus=campus, season=current_season, name="Encerrada", end_date=TODAY - timedelta(days=1))
    Cell.objects.create(campus=campus, season=current_season, name="Rodando")
    assert [c.name for c in Cell.objects.active()] == ["Rodando"]


# --- Renewing into a new season ----------------------------------------------------


def test_renew_copies_cell_leaders_and_members(api_client, cells_leader, cell, cell_leader, member, next_season):
    api_client.force_authenticate(cells_leader)
    response = api_client.post(f"/api/cells/{cell.id}/renew/", {"season_id": str(next_season.id)}, format="json")
    assert response.status_code == 201, response.data

    new_cell = Cell.objects.get(pk=response.data["id"])
    assert new_cell.season == next_season
    assert new_cell.previous_cell == cell
    assert new_cell.name == cell.name
    assert new_cell.leaders() == [cell_leader]
    assert list(new_cell.members.all()) == [member]
    # The old cell is untouched — it's the history.
    assert list(cell.members.all()) == [member]


def test_renew_without_members(api_client, manager, cell, member, next_season):
    api_client.force_authenticate(manager)
    response = api_client.post(
        f"/api/cells/{cell.id}/renew/", {"season_id": str(next_season.id), "copy_members": False}, format="json"
    )
    assert response.status_code == 201
    assert Cell.objects.get(pk=response.data["id"]).members.count() == 0


def test_renew_twice_into_same_season_is_rejected(api_client, manager, cell, next_season):
    api_client.force_authenticate(manager)
    url = f"/api/cells/{cell.id}/renew/"
    assert api_client.post(url, {"season_id": str(next_season.id)}, format="json").status_code == 201
    assert api_client.post(url, {"season_id": str(next_season.id)}, format="json").status_code == 400


def test_cell_leader_cannot_renew(api_client, cell_leader, cell, next_season):
    api_client.force_authenticate(cell_leader)
    response = api_client.post(f"/api/cells/{cell.id}/renew/", {"season_id": str(next_season.id)}, format="json")
    assert response.status_code == 403


# --- Leadership and campus -----------------------------------------------------------


def test_leader_must_be_from_cell_campus(api_client, manager, cell, other_campus):
    outsider = make_person(email="fora@ilan.church", name="De Fora", campus=other_campus)
    api_client.force_authenticate(manager)
    response = api_client.post(
        f"/api/cells/{cell.id}/leaderships/", {"person_id": str(outsider.id), "role": "leader"}, format="json"
    )
    assert response.status_code == 400


def test_member_may_come_from_another_campus(api_client, cell_leader, cell, other_campus):
    outsider = make_person(email="fora@ilan.church", name="De Fora", campus=other_campus)
    api_client.force_authenticate(cell_leader)
    response = api_client.post(f"/api/cells/{cell.id}/members/", {"person_id": str(outsider.id)}, format="json")
    assert response.status_code == 201


def test_cells_general_leader_assigns_cell_leader(api_client, cells_leader, cell, regular_person):
    api_client.force_authenticate(cells_leader)
    response = api_client.post(
        f"/api/cells/{cell.id}/leaderships/", {"person_id": str(regular_person.id), "role": "co_leader"}, format="json"
    )
    assert response.status_code == 201


# --- Meetings ---------------------------------------------------------------------------


def meeting_payload(cell, **extra):
    return {"cell_id": str(cell.id), "date": str(TODAY), **extra}


def test_cell_leader_registers_meeting_with_attendance(api_client, cell_leader, cell, member):
    api_client.force_authenticate(cell_leader)
    response = api_client.post(
        "/api/cell-meetings/",
        meeting_payload(cell, attendee_ids=[str(member.id), str(cell_leader.id)], visitors_count=2, notes="Romanos 8"),
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["attendance_count"] == 4
    assert response.data["created_by"]["id"] == str(cell_leader.id)


def test_member_cannot_register_meeting(api_client, member, cell):
    api_client.force_authenticate(member)
    assert api_client.post("/api/cell-meetings/", meeting_payload(cell), format="json").status_code == 403


def test_member_can_see_meetings(api_client, member, cell):
    CellMeeting.objects.create(cell=cell, date=TODAY)
    api_client.force_authenticate(member)
    response = api_client.get(f"/api/cell-meetings/?cell={cell.id}")
    assert response.data["count"] == 1


def test_outsider_does_not_see_meetings(api_client, regular_person, cell):
    CellMeeting.objects.create(cell=cell, date=TODAY)
    api_client.force_authenticate(regular_person)
    assert api_client.get("/api/cell-meetings/").data["count"] == 0


def test_attendee_must_be_member_or_leader(api_client, cell_leader, cell, regular_person):
    api_client.force_authenticate(cell_leader)
    response = api_client.post(
        "/api/cell-meetings/", meeting_payload(cell, attendee_ids=[str(regular_person.id)]), format="json"
    )
    assert response.status_code == 400
    assert "attendee_ids" in response.data


def test_meeting_date_must_be_within_cell_period(api_client, cell_leader, cell, current_season):
    api_client.force_authenticate(cell_leader)
    response = api_client.post(
        "/api/cell-meetings/",
        {"cell_id": str(cell.id), "date": str(current_season.start_date - timedelta(days=1))},
        format="json",
    )
    assert response.status_code == 400


def test_one_meeting_per_day(api_client, cell_leader, cell):
    api_client.force_authenticate(cell_leader)
    assert api_client.post("/api/cell-meetings/", meeting_payload(cell), format="json").status_code == 201
    assert api_client.post("/api/cell-meetings/", meeting_payload(cell), format="json").status_code == 400


def test_cell_leader_edits_meeting_attendance(api_client, cell_leader, cell, member):
    meeting = CellMeeting.objects.create(cell=cell, date=TODAY)
    api_client.force_authenticate(cell_leader)
    response = api_client.patch(
        f"/api/cell-meetings/{meeting.id}/", {"attendee_ids": [str(member.id)]}, format="json"
    )
    assert response.status_code == 200, response.data
    assert list(meeting.attendees.all()) == [member]


def test_me_exposes_cells_general_leader_flag(api_client, cells_leader):
    api_client.force_authenticate(cells_leader)
    response = api_client.get("/api/auth/me/")
    assert response.data["is_cells_general_leader"] is True


def test_reconciliation_only_offers_active_cells(campus, cell, past_season):
    Cell.objects.create(campus=campus, season=past_season, name="GT Antigo")
    assert [c.name for c in Cell.objects.active().filter(campus=campus)] == ["GT Vida"]
