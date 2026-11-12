"""Cell visitors by name: registering them, marking them in meetings (with the
meeting's topic and decisions), forwarding them to Reconciliação as a lead and
turning them into members. Contact data stays with whoever manages the cell."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import LeadershipRole, VisitorStatus
from apps.churches.models import CellLeadership, CellMeeting, CellVisitor, Season
from apps.reconciliation.choices import LeadStatus
from apps.reconciliation.models import ReconciliationLead
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()
URL = "/api/cell-visitors/"
MEETINGS = "/api/cell-meetings/"


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) else data


@pytest.fixture
def season(church):
    return Season.objects.create(
        church=church, name="Atual", start_date=TODAY - timedelta(days=60), end_date=TODAY + timedelta(days=60)
    )


@pytest.fixture
def cell(campus, season):
    return make_cell(campus=campus, season=season, name="GT Vida")


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


@pytest.fixture
def visitor(cell, cell_leader):
    return CellVisitor.objects.create(cell=cell, name="Ana Visitante", phone="11999990000", created_by=cell_leader)


def as_user(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


# --- Registering and seeing visitors ---------------------------------------------


def test_leader_registers_a_visitor(api_client, cell, cell_leader):
    response = as_user(api_client, cell_leader).post(
        URL, {"cell_id": str(cell.id), "name": "  Bruno  ", "phone": "11988887777"}, format="json"
    )

    assert response.status_code == 201, response.data
    assert response.data["name"] == "Bruno"
    assert response.data["status"] == VisitorStatus.VISITING
    assert response.data["visit_count"] == 0


def test_plain_member_neither_sees_nor_registers_visitors(api_client, cell, member, visitor):
    client = as_user(api_client, member)

    assert rows(client.get(URL, {"cell": str(cell.id)})) == []
    response = client.post(URL, {"cell_id": str(cell.id), "name": "Intruso"}, format="json")
    assert response.status_code == 403


def test_leader_of_another_cell_cannot_see_visitors(api_client, campus, season, visitor):
    other = make_cell(campus=campus, season=season, name="Outra")
    other_leader = make_person(email="lider.outra@ilan.church", campus=campus)
    CellLeadership.objects.create(cell=other, person=other_leader, role=LeadershipRole.LEADER)

    assert rows(as_user(api_client, other_leader).get(URL)) == []


# --- Meetings with named visitors ----------------------------------------------


def test_meeting_records_named_visitors_topic_and_decisions(api_client, cell, cell_leader, visitor):
    response = as_user(api_client, cell_leader).post(
        MEETINGS,
        {
            "cell_id": str(cell.id),
            "date": str(TODAY - timedelta(days=1)),
            "topic": "Parábola do semeador",
            "visitor_ids": [str(visitor.id)],
            "visitors_count": 2,
            "decisions_count": 1,
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["topic"] == "Parábola do semeador"
    assert response.data["decisions_count"] == 1
    assert [v["name"] for v in response.data["visitors"]] == ["Ana Visitante"]
    # 1 named + 2 counted by number.
    assert response.data["attendance_count"] == 3

    listed = rows(api_client.get(URL, {"cell": str(cell.id)}))[0]
    assert listed["visit_count"] == 1
    assert listed["first_visit"] == listed["last_visit"] == str(TODAY - timedelta(days=1))


def test_meeting_rejects_visitor_of_another_cell(api_client, campus, season, cell, cell_leader):
    other = make_cell(campus=campus, season=season, name="Outra")
    stranger = CellVisitor.objects.create(cell=other, name="De Fora")

    response = as_user(api_client, cell_leader).post(
        MEETINGS,
        {"cell_id": str(cell.id), "date": str(TODAY - timedelta(days=1)), "visitor_ids": [str(stranger.id)]},
        format="json",
    )

    assert response.status_code == 400
    assert "visitor_ids" in response.data


def test_meeting_not_held_clears_visitors_and_decisions(api_client, cell, cell_leader, visitor):
    response = as_user(api_client, cell_leader).post(
        MEETINGS,
        {
            "cell_id": str(cell.id),
            "date": str(TODAY - timedelta(days=1)),
            "held": False,
            "cancel_reason": "Feriado",
            "visitor_ids": [str(visitor.id)],
            "decisions_count": 3,
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    meeting = CellMeeting.objects.get(pk=response.data["id"])
    assert meeting.visitors.count() == 0
    assert meeting.decisions_count == 0


# --- Forwarding to Reconciliação -------------------------------------------------


def test_forward_creates_a_pending_lead_for_the_campus(api_client, campus, cell, cell_leader, visitor):
    response = as_user(api_client, cell_leader).post(f"{URL}{visitor.id}/forward/")

    assert response.status_code == 200, response.data
    assert response.data["status"] == VisitorStatus.FORWARDED
    assert response.data["forwarded"] is True
    lead = ReconciliationLead.objects.get(cell_visitor=visitor)
    assert lead.name == "Ana Visitante"
    assert lead.phone == "11999990000"
    assert lead.campus == campus
    assert lead.status == LeadStatus.PENDING
    assert "GT Vida" in lead.raw_campus_label

    again = api_client.post(f"{URL}{visitor.id}/forward/")
    assert again.status_code == 400
    assert ReconciliationLead.objects.count() == 1


def test_member_cannot_forward(api_client, member, visitor):
    # Not even visible to a plain member.
    assert as_user(api_client, member).post(f"{URL}{visitor.id}/forward/").status_code == 404


# --- Becoming a member -----------------------------------------------------------


def test_make_member_registers_a_new_person(api_client, campus, cell, cell_leader, visitor):
    response = as_user(api_client, cell_leader).post(
        f"{URL}{visitor.id}/make-member/", {"email": "Ana@Exemplo.com"}, format="json"
    )

    assert response.status_code == 200, response.data
    assert response.data["status"] == VisitorStatus.MEMBER
    person = cell.members.get(email="ana@exemplo.com")
    assert person.name == "Ana Visitante"
    assert person.phone == "11999990000"
    assert person.campus == campus
    assert not person.has_usable_password()
    assert response.data["person"]["id"] == str(person.id)


def test_make_member_with_an_existing_person(api_client, cell, cell_leader, visitor, campus):
    existing = make_person(email="ana.cadastrada@ilan.church", name="Ana Cadastrada", campus=campus)

    response = as_user(api_client, cell_leader).post(
        f"{URL}{visitor.id}/make-member/", {"person_id": str(existing.id)}, format="json"
    )

    assert response.status_code == 200, response.data
    assert cell.members.filter(pk=existing.pk).exists()


def test_make_member_refuses_an_email_already_in_use(api_client, cell, cell_leader, visitor, campus):
    make_person(email="ja.existe@ilan.church", campus=campus)

    response = as_user(api_client, cell_leader).post(
        f"{URL}{visitor.id}/make-member/", {"email": "JA.EXISTE@ilan.church"}, format="json"
    )

    assert response.status_code == 400
    assert "email" in response.data
    visitor.refresh_from_db()
    assert visitor.status == VisitorStatus.VISITING


def test_make_member_needs_a_person_or_email(api_client, cell_leader, visitor):
    response = as_user(api_client, cell_leader).post(f"{URL}{visitor.id}/make-member/", {}, format="json")
    assert response.status_code == 400
