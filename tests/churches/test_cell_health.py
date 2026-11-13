"""Cell health (green/yellow/red with reasons) and "pessoas para cuidar"
(members who stopped coming, visitors who didn't come back). Both only for
whoever manages the cell."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import LeadershipRole
from apps.churches.models import CellLeadership, CellMeeting, CellVisitor, Season
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()
WEEKS = 8
# The cell meets on today's weekday: 8 past dates, today and the future ones.
PAST_DATES = [TODAY - timedelta(weeks=w) for w in range(WEEKS, 0, -1)]


@pytest.fixture
def season(church):
    return Season.objects.create(
        church=church, name="Atual", start_date=TODAY - timedelta(weeks=WEEKS), end_date=TODAY + timedelta(weeks=8)
    )


@pytest.fixture
def cell(campus, season):
    return make_cell(campus=campus, season=season, name="GT Saúde", meeting_day=TODAY.isoweekday())


@pytest.fixture
def leader(campus, cell):
    person = make_person(email="lider.saude@ilan.church", name="Líder Saúde", campus=campus)
    CellLeadership.objects.create(cell=cell, person=person, role=LeadershipRole.LEADER)
    return person


@pytest.fixture
def members(campus, cell):
    people = [
        make_person(email=f"membro{i}.saude@ilan.church", name=f"Membro {i}", campus=campus, phone=f"1190000000{i}")
        for i in range(3)
    ]
    cell.members.add(*people)
    return people


def meet(cell, day, attendees, visitors=(), visitors_count=0):
    meeting = CellMeeting.objects.create(cell=cell, date=day, visitors_count=visitors_count)
    meeting.attendees.set(attendees)
    meeting.visitors.set(visitors)
    return meeting


def health(api_client, person, cell):
    api_client.force_authenticate(user=person)
    return api_client.get(f"/api/cells/{cell.id}/").data["health"]


def test_healthy_cell(api_client, cell, leader, members):
    for day in PAST_DATES:
        meet(cell, day, [leader, *members], visitors_count=1)

    data = health(api_client, leader, cell)

    assert data["status"] == "healthy"
    assert data["attendance_rate"] == 100
    assert data["trend"] == 0
    assert data["recent_visitors"] == 4
    assert data["reasons"] == []


def test_new_cell_without_meetings_and_nothing_wrong(api_client, campus, church, leader):
    future = Season.objects.create(
        church=church, name="Próxima", start_date=TODAY + timedelta(days=200), end_date=TODAY + timedelta(days=300)
    )
    fresh = make_cell(campus=campus, season=future, name="Nova")
    CellLeadership.objects.create(cell=fresh, person=leader, role=LeadershipRole.LEADER)

    assert health(api_client, leader, fresh)["status"] == "new"


def test_attendance_drop_is_critical(api_client, cell, leader, members):
    for day in PAST_DATES[:4]:
        meet(cell, day, [leader, *members])
    for day in PAST_DATES[4:]:
        meet(cell, day, [leader])

    data = health(api_client, leader, cell)

    assert data["status"] == "critical"
    assert data["attendance_rate"] == 25
    assert data["trend"] == -75
    texts = [r["text"] for r in data["reasons"]]
    assert "Frequência de 25% nos últimos encontros" in texts
    assert "Presença caiu 75%" in texts
    assert "Sem visitantes nos últimos encontros" in texts


def test_unregistered_meetings_and_no_leader(api_client, cell, members, manager):
    for day in PAST_DATES[:5]:
        meet(cell, day, members, visitors_count=1)
    # The last 3 dates have no record, and nobody leads the cell.

    data = health(api_client, manager, cell)

    assert data["status"] == "critical"
    assert {"level": "critical", "text": "Sem líder"} in data["reasons"]
    assert {"level": "critical", "text": "3 encontros sem registro"} in data["reasons"]


def test_one_unregistered_meeting_is_attention(api_client, cell, leader, members):
    for day in PAST_DATES[:-1]:
        meet(cell, day, [leader, *members], visitors_count=1)

    data = health(api_client, leader, cell)

    assert data["status"] == "attention"
    assert data["reasons"] == [{"level": "attention", "text": "1 encontro sem registro"}]


def test_plain_member_gets_no_health(api_client, cell, leader, members):
    meet(cell, PAST_DATES[-1], [leader, *members])
    assert health(api_client, members[0], cell) is None


# --- Pessoas para cuidar --------------------------------------------------------


def test_care_lists_absent_members_and_visitors_who_did_not_return(api_client, cell, leader, members):
    stayed, stopped, _ = members
    returning = CellVisitor.objects.create(cell=cell, name="Voltou")
    gone = CellVisitor.objects.create(cell=cell, name="Sumiu", phone="11977776666")
    for i, day in enumerate(PAST_DATES):
        came = [leader, stayed] + ([stopped] if i < 5 else [])
        visitors = [returning] + ([gone] if i == 5 else [])
        meet(cell, day, came, visitors=visitors)

    api_client.force_authenticate(user=leader)
    data = api_client.get(f"/api/cells/{cell.id}/care/").data

    absent = {row["person"]["name"]: row for row in data["absent_members"]}
    # Membro 2 never came (8 in a row); Membro 1 stopped 3 meetings ago.
    assert set(absent) == {"Membro 1", "Membro 2"}
    assert absent["Membro 1"]["absences"] == 3
    assert absent["Membro 1"]["last_attended"] == PAST_DATES[4]
    assert absent["Membro 1"]["person"]["phone"] == "11900000001"
    assert absent["Membro 2"]["last_attended"] is None
    assert [v["name"] for v in data["visitors_not_returning"]] == ["Sumiu"]
    assert data["visitors_not_returning"][0]["missed"] == 2
    assert data["visitors_not_returning"][0]["phone"] == "11977776666"


def test_care_is_only_for_the_cell_leadership(api_client, cell, members):
    api_client.force_authenticate(user=members[0])
    assert api_client.get(f"/api/cells/{cell.id}/care/").status_code == 403
