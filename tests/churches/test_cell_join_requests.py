"""\"Quero participar\": anyone asks to join a public cell; its leadership
accepts (the person becomes a named visitor and gets the address and the
leaders) or declines."""

from datetime import time

import pytest

from apps.churches.choices import CellRole, VisitorStatus
from apps.churches.models import CellLeadership, CellVisitor
from apps.notifications.models import Notification
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

URL = "/api/cell-join-requests/"


def as_(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def cell(campus):
    cell = make_cell(
        campus=campus, name="GT Casa", address="Rua das Flores, 10", visibility="public",
        meeting_day=5, meeting_time=time(20, 0),
    )
    leader = make_person(email="lider.casa@ilan.church", name="Líder Casa", phone="11988887777", campus=campus)
    CellLeadership.objects.create(cell=cell, person=leader, role=CellRole.LEADER)
    cell.leader = leader
    return cell


@pytest.fixture
def outsider(db):
    return make_person(email="visitante@gmail.com", name="Bruna Visitante", phone="11911112222", campus=None)


def ask(api_client, person, cell, message="Moro perto"):
    return as_(api_client, person).post(URL, {"cell_id": str(cell.id), "message": message}, format="json")


def test_ask_accept_and_get_the_address(api_client, cell, outsider):
    res = ask(api_client, outsider, cell)
    assert res.status_code == 201, res.data
    request_id = res.data["id"]
    assert ask(api_client, outsider, cell).status_code == 400  # one pending at a time

    # Before the answer: no address.
    mine = as_(api_client, outsider).get(f"{URL}mine/", {"cell": str(cell.id)}).data
    assert mine[0]["status"] == "pending" and "address" not in mine[0]["cell"]

    note = Notification.objects.get(recipient=cell.leader, kind="cell_join_request")
    assert "Bruna Visitante" in note.body and note.link == f"/app/celulas/{cell.id}"

    pending = as_(api_client, cell.leader).get(URL, {"cell": str(cell.id)}).data
    assert pending[0]["person"]["phone"] == "11911112222" and pending[0]["message"] == "Moro perto"

    res = as_(api_client, cell.leader).post(f"{URL}{request_id}/accept/")
    assert res.status_code == 200, res.data
    visitor = CellVisitor.objects.get(cell=cell)
    assert visitor.name == "Bruna Visitante" and visitor.status == VisitorStatus.VISITING
    assert visitor.join_request.person == outsider

    mine = as_(api_client, outsider).get(f"{URL}mine/", {"cell": str(cell.id)}).data
    assert mine[0]["status"] == "approved"
    assert mine[0]["cell"]["address"] == "Rua das Flores, 10"
    assert mine[0]["cell"]["leaders"] == [{"name": "Líder Casa", "phone": "11988887777"}]
    answer = Notification.objects.get(recipient=outsider, kind="cell_join_answered")
    assert answer.link == f"/celula/{cell.id}"
    assert as_(api_client, cell.leader).post(f"{URL}{request_id}/decline/").status_code == 400


def test_decline(api_client, cell, outsider):
    request_id = ask(api_client, outsider, cell).data["id"]
    assert as_(api_client, cell.leader).post(f"{URL}{request_id}/decline/").status_code == 200
    assert not CellVisitor.objects.filter(cell=cell).exists()
    mine = as_(api_client, outsider).get(f"{URL}mine/").data
    assert mine[0]["status"] == "rejected" and "address" not in mine[0]["cell"]
    # They may ask again later.
    assert ask(api_client, outsider, cell).status_code == 201


def test_only_the_leadership_answers(api_client, cell, outsider, regular_person):
    request_id = ask(api_client, outsider, cell).data["id"]
    assert as_(api_client, outsider).post(f"{URL}{request_id}/accept/").status_code == 403
    assert as_(api_client, regular_person).post(f"{URL}{request_id}/accept/").status_code == 403
    assert as_(api_client, regular_person).get(URL).data == []


def test_members_only_cells_take_no_requests(api_client, campus, outsider):
    closed = make_cell(campus=campus, name="GT Fechado")
    res = ask(api_client, outsider, closed)
    assert res.status_code == 400 and "não está aberta" in str(res.data)


def test_cancel_and_already_in(api_client, cell, outsider):
    request_id = ask(api_client, outsider, cell).data["id"]
    assert as_(api_client, outsider).post(f"{URL}{request_id}/cancel/").data["status"] == "cancelled"
    cell.members.add(outsider)
    res = ask(api_client, outsider, cell)
    assert res.status_code == 400 and "já participa" in str(res.data)


def test_an_accepted_visitor_becomes_a_member_with_their_account(api_client, cell, outsider):
    request_id = ask(api_client, outsider, cell).data["id"]
    as_(api_client, cell.leader).post(f"{URL}{request_id}/accept/")
    visitor = CellVisitor.objects.get(cell=cell)
    res = as_(api_client, cell.leader).post(f"/api/cell-visitors/{visitor.id}/make-member/", {}, format="json")
    assert res.status_code == 200, res.data
    assert cell.members.filter(pk=outsider.pk).exists()
