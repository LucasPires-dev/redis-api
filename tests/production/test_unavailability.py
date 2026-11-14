"""Days without serving: marked by the person, seen by the leadership when
building the roster (a warning, never a block). Also the notices that closed
the plan's gaps: an event registration still waiting for its payment, a
supervision visit (to the cell's leaders) and a visitor forwarded to
Reconciliação (to its leaders)."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import CellRole, LeadershipRole, MinistryKind
from apps.churches.models import CellLeadership, CellVisitor, Ministry, MinistryLeadership, Season
from apps.notifications import reminders
from apps.notifications.models import Notification
from apps.payments.models import EventRegistration, EventTicketConfig, PaymentStatus
from apps.production.models import Event, EventRosterEntry, ServeUnavailability
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

URL = "/api/production-unavailability/"
ROSTER = "/api/production-roster-entries"


def as_(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def event(campus):
    return Event.objects.create(title="Culto", campus=campus, starts_at=timezone.now() + timedelta(days=10))


# ── Unavailability ───────────────────────────────────────────────────────────


def test_people_manage_only_their_own_days(api_client, media_member, regular_person, event):
    day = timezone.localdate(event.starts_at)
    client = as_(api_client, media_member)
    res = client.post(URL, {"starts_on": str(day), "ends_on": str(day + timedelta(days=2)), "reason": "Viagem"})
    assert res.status_code == 201, res.data
    assert client.post(URL, {"starts_on": str(day), "ends_on": str(day - timedelta(days=1))}).status_code == 400
    assert client.post(URL, {"starts_on": str(day), "ends_on": str(day + timedelta(days=400))}).status_code == 400
    # Past periods leave the list.
    ServeUnavailability.objects.create(
        person=media_member, starts_on=day - timedelta(days=30), ends_on=day - timedelta(days=20)
    )
    assert [u["reason"] for u in client.get(URL).data] == ["Viagem"]

    other = as_(api_client, regular_person)
    assert other.get(URL).data == []
    assert other.delete(f"{URL}{res.data['id']}/").status_code == 404
    assert as_(api_client, media_member).delete(f"{URL}{res.data['id']}/").status_code == 204


def test_leadership_sees_it_when_inviting(api_client, media_leader, media_member, media_ministry, event):
    day = timezone.localdate(event.starts_at)
    ServeUnavailability.objects.create(person=media_member, starts_on=day, ends_on=day, reason="Plantão")
    client = as_(api_client, media_leader)
    rows = client.get(f"{ROSTER}/availability/", {"event": str(event.id), "ministry": str(media_ministry.id)}).data
    marked = {r["name"]: r["unavailable"] for r in rows}
    assert marked[media_member.name]["reason"] == "Plantão"
    assert marked[media_leader.name] is None

    # Still invitable: the leadership decides.
    body = {"event_id": str(event.id), "ministry_id": str(media_ministry.id), "person_id": str(media_member.id)}
    res = client.post(f"{ROSTER}/", body, format="json")
    assert res.status_code == 201 and res.data["unavailable"]["reason"] == "Plantão"
    mine = as_(api_client, media_member).get(f"{ROSTER}/mine/").data
    assert mine[0]["unavailable"]["reason"] == "Plantão"


def test_other_days_dont_count(api_client, media_leader, media_member, media_ministry, event):
    day = timezone.localdate(event.starts_at)
    ServeUnavailability.objects.create(
        person=media_member, starts_on=day + timedelta(days=1), ends_on=day + timedelta(days=3)
    )
    rows = as_(api_client, media_leader).get(
        f"{ROSTER}/availability/", {"event": str(event.id), "ministry": str(media_ministry.id)}
    ).data
    assert all(r["unavailable"] is None for r in rows)


def test_availability_is_for_the_ministry_leadership(api_client, media_member, media_ministry, event):
    res = as_(api_client, media_member).get(
        f"{ROSTER}/availability/", {"event": str(event.id), "ministry": str(media_ministry.id)}
    )
    assert res.status_code == 403


def test_swap_candidates_show_who_is_away(api_client, media_member, media_ministry, campus, event):
    other = make_person(email="outra.midia@ilan.church", name="Outra", campus=campus)
    media_ministry.members.add(other)
    day = timezone.localdate(event.starts_at)
    ServeUnavailability.objects.create(person=other, starts_on=day - timedelta(days=1), ends_on=day)
    entry = EventRosterEntry.objects.create(event=event, ministry=media_ministry, person=media_member)
    rows = as_(api_client, media_member).get(f"{ROSTER}/{entry.id}/swap-candidates/").data
    assert {r["name"]: r["unavailable"] for r in rows}["Outra"] is True


# ── Unpaid event registration ───────────────────────────────────────────────


def test_unpaid_registration_gets_one_reminder(regular_person, event):
    EventTicketConfig.objects.create(event=event, price_cents=5000)
    reg = EventRegistration.objects.create(
        event=event, person=regular_person, amount_cents=5000,
        payment_status=PaymentStatus.PENDING, payment_url="https://asaas.test/i/1",
    )
    assert reminders.event_payments_pending() == 0  # just registered
    EventRegistration.objects.filter(pk=reg.pk).update(created_at=timezone.now() - timedelta(days=2))
    assert reminders.event_payments_pending() == 1
    assert reminders.event_payments_pending() == 0  # once
    note = Notification.objects.get(recipient=regular_person, kind="event_payment_pending")
    assert "R$ 50,00" in note.body and note.link == "/app/eventos/inscricoes"


def test_no_reminder_after_the_event_or_once_paid(regular_person, campus, event):
    past = Event.objects.create(title="Passado", campus=campus, starts_at=timezone.now() - timedelta(days=1))
    for ev, status in ((past, PaymentStatus.PENDING), (event, PaymentStatus.CONFIRMED)):
        reg = EventRegistration.objects.create(
            event=ev, person=regular_person, amount_cents=5000, payment_status=status,
            payment_url="https://asaas.test/i/x",
        )
        EventRegistration.objects.filter(pk=reg.pk).update(created_at=timezone.now() - timedelta(days=3))
    assert reminders.event_payments_pending() == 0


# ── Supervision visit and forwarded visitor ─────────────────────────────────


@pytest.fixture
def cell(campus, church):
    today = timezone.localdate()
    season = Season.objects.create(
        church=church, name="Atual", start_date=today - timedelta(weeks=4), end_date=today + timedelta(weeks=8)
    )
    cell = make_cell(campus=campus, season=season, name="GT Vida")
    cell.leader = make_person(email="lider.gt@ilan.church", name="Líder GT", campus=campus)
    CellLeadership.objects.create(cell=cell, person=cell.leader, role=CellRole.LEADER)
    return cell


def test_a_visit_tells_the_cell_leaders_without_the_notes(api_client, campus, cell):
    supervisor = make_person(email="supervisor@ilan.church", name="Supervisora", campus=campus)
    cell.supervisor = supervisor
    cell.save()
    res = as_(api_client, supervisor).post(
        "/api/cell-supervision-visits/",
        {"cell_id": str(cell.id), "date": timezone.localdate().isoformat(), "kind": "meeting",
         "notes": "Feedback reservado"},
    )
    assert res.status_code == 201, res.data
    note = Notification.objects.get(recipient=cell.leader, kind="supervision_visit")
    assert "GT Vida" in note.title and "Supervisora" in note.body
    assert "Feedback" not in note.body and note.link == f"/app/celulas/{cell.id}"
    assert not Notification.objects.filter(recipient=supervisor, kind="supervision_visit").exists()


def test_forward_tells_the_reconciliation_leaders(api_client, campus, cell):
    ministry = Ministry.objects.create(campus=campus, kind=MinistryKind.RECONCILIACAO, name="Reconciliação")
    head = make_person(email="rec.lider@ilan.church", name="Líder Rec", campus=campus)
    MinistryLeadership.objects.create(ministry=ministry, person=head, role=LeadershipRole.LEADER)
    visitor = CellVisitor.objects.create(cell=cell, name="Ana Visitante", phone="11999990000", created_by=cell.leader)

    assert as_(api_client, cell.leader).post(f"/api/cell-visitors/{visitor.id}/forward/").status_code == 200
    note = Notification.objects.get(recipient=head, kind="visitor_forwarded")
    assert "Ana Visitante" in note.title and "GT Vida" in note.body
    assert note.link == f"/app/ministerios/reconciliacao/{campus.id}"


def test_forward_without_reconciliation_leaders_tells_the_campus(api_client, campus_leader, cell):
    visitor = CellVisitor.objects.create(cell=cell, name="Bia", created_by=cell.leader)
    as_(api_client, cell.leader).post(f"/api/cell-visitors/{visitor.id}/forward/")
    assert Notification.objects.filter(recipient=campus_leader, kind="visitor_forwarded").exists()
