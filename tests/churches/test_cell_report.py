"""Coordination report (/api/cells/report/): weekly series per cell, totals,
per-campus comparison and coverage (% of the campus's people in a cell)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.cell_report import week_start
from apps.churches.choices import LeadershipRole, VisitorStatus
from apps.churches.models import CellLeadership, CellMeeting, CellVisitor, Season
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()
# The season starts on a Monday 3 weeks ago: 3 past weeks plus this one.
START = week_start(TODAY) - timedelta(weeks=3)


@pytest.fixture
def season(church):
    return Season.objects.create(church=church, name="Atual", start_date=START, end_date=TODAY + timedelta(weeks=8))


@pytest.fixture
def cell(campus, season):
    """Leader + 2 members; meetings 2 and 1 weeks ago; the first one (3 weeks
    ago) was never registered."""
    cell = make_cell(campus=campus, season=season, name="GT Relatório", meeting_day=TODAY.isoweekday())
    leader = make_person(email="lider.rel@ilan.church", name="Líder Rel", campus=campus)
    CellLeadership.objects.create(cell=cell, person=leader, role=LeadershipRole.LEADER)
    m1 = make_person(email="m1.rel@ilan.church", name="M1", campus=campus)
    m2 = make_person(email="m2.rel@ilan.church", name="M2", campus=campus)
    cell.members.add(m1, m2)
    # Someone of the campus in no cell.
    make_person(email="fora.rel@ilan.church", name="Fora", campus=campus)

    first = CellMeeting.objects.create(
        cell=cell, date=TODAY - timedelta(weeks=2), visitors_count=2, decisions_count=1
    )
    first.attendees.set([leader, m1])
    joined = CellVisitor.objects.create(cell=cell, name="Entrou", status=VisitorStatus.MEMBER)
    second = CellMeeting.objects.create(cell=cell, date=TODAY - timedelta(weeks=1))
    second.attendees.set([leader, m1, m2])
    second.visitors.set([joined])
    cell.leader = leader
    return cell


@pytest.fixture
def other_cell(other_campus, season):
    cell = make_cell(campus=other_campus, season=season, name="GT Outro", meeting_day=TODAY.isoweekday())
    person = make_person(email="outro.rel@ilan.church", name="Outro", campus=other_campus)
    cell.members.add(person)
    meeting = CellMeeting.objects.create(cell=cell, date=TODAY - timedelta(weeks=1))
    meeting.attendees.set([person])
    return cell


def get_report(api_client, person, **params):
    api_client.force_authenticate(user=person)
    return api_client.get("/api/cells/report/", {"season": "current", **params})


def test_campus_leader_gets_the_weekly_report_of_their_campus(api_client, campus_leader, cell, other_cell):
    response = get_report(api_client, campus_leader)

    assert response.status_code == 200
    data = response.data
    assert data["weeks"] == [START + timedelta(weeks=w) for w in range(4)]
    # Only the cells of the campus they lead.
    assert [row["name"] for row in data["cells"]] == ["GT Relatório"]

    row = data["cells"][0]
    assert row["participants"] == 3
    assert row["leaders"] == ["Líder Rel"]
    assert row["meetings_held"] == 2
    assert row["meetings_pending"] == 1
    assert row["attendance"] == 5
    assert row["avg_attendance"] == 2.5
    assert row["attendance_rate"] == 83  # (2/3 + 3/3) / 2
    assert row["visitors"] == 3  # 2 unnamed + 1 named
    assert row["decisions"] == 1
    assert row["new_members"] == 1
    assert row["series"] == [
        {"held": 0, "attendance": 0, "visitors": 0, "decisions": 0},
        {"held": 1, "attendance": 2, "visitors": 2, "decisions": 1},
        {"held": 1, "attendance": 3, "visitors": 1, "decisions": 0},
        {"held": 0, "attendance": 0, "visitors": 0, "decisions": 0},
    ]

    # Campus: leader, 2 members, the campus leader and someone in no cell.
    [arena] = data["campuses"]
    assert arena["people"] == 5
    assert arena["people_in_cells"] == 3
    assert arena["coverage"] == 60
    assert data["totals"]["cells"] == 1
    assert data["totals"]["coverage"] == 60


def test_manager_compares_every_campus_and_can_filter_one(api_client, manager, campus, cell, other_cell):
    data = get_report(api_client, manager).data
    assert {c["name"] for c in data["campuses"]} == {"Arena ILAN", "Outro Campus"}
    assert data["totals"]["cells"] == 2
    assert data["totals"]["meetings_held"] == 3
    assert data["totals"]["attendance"] == 6

    only_arena = get_report(api_client, manager, campus=str(campus.id)).data
    assert [c["name"] for c in only_arena["cells"]] == ["GT Relatório"]


def test_someone_in_a_cell_of_another_campus_counts_as_in_a_cell(api_client, campus_leader, cell, other_cell):
    # The person without a cell joins the cell of the other campus.
    other_cell.members.add(cell.campus.people.get(name="Fora"))
    [arena] = get_report(api_client, campus_leader).data["campuses"]
    assert arena["people_in_cells"] == 4
    assert arena["coverage"] == 80


def test_a_cell_leader_who_does_not_coordinate_gets_403(api_client, cell):
    assert get_report(api_client, cell.leader).status_code == 403
