"""Serving roster: notices, "my roster", time conflicts, swaps and the demand notices."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.notifications import reminders
from apps.notifications.models import Notification
from apps.production.choices import RosterStatus, SwapStatus
from apps.production.models import Demand, Event, EventRosterEntry, RosterSwapRequest
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

ROSTER = "/api/production-roster-entries"
SWAPS = "/api/production-roster-swaps"


def as_(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def kinds(person):
    """What the person was told (leaving out the fixtures' own appointments)."""
    return list(
        Notification.objects.filter(recipient=person).exclude(kind="appointment").values_list("kind", flat=True)
    )


@pytest.fixture
def event(campus):
    return Event.objects.create(
        title="Culto de Domingo", campus=campus, starts_at=timezone.now() + timedelta(days=5)
    )


@pytest.fixture
def other_member(campus, media_ministry):
    person = make_person(email="outro-midia@ilan.church", name="Outro de Mídia", campus=campus)
    media_ministry.members.add(person)
    return person


def invite(api_client, leader, event, ministry, person, function="Câmera 1"):
    return as_(api_client, leader).post(
        f"{ROSTER}/",
        {"event_id": str(event.id), "ministry_id": str(ministry.id), "person_id": str(person.id), "function": function},
        format="json",
    )


def test_invite_notifies_and_shows_in_my_roster(api_client, event, media_ministry, media_leader, media_member):
    res = invite(api_client, media_leader, event, media_ministry, media_member)
    assert res.status_code == 201
    (note,) = Notification.objects.filter(recipient=media_member)
    assert note.kind == "roster_invite" and event.title in note.title
    assert "Mídia · Câmera 1" in note.body and note.link == "/app/eventos/escalas"

    mine = as_(api_client, media_member).get(f"{ROSTER}/mine/").data
    (row,) = mine
    assert row["event"]["title"] == event.title and row["status"] == "invited"
    assert row["conflicts"] == [] and row["pending_swap"] is None
    # The invitee can open the event even if it isn't their ministry's.
    assert as_(api_client, media_member).get(f"/api/production-events/{event.id}/").status_code == 200


def test_decline_tells_whoever_invited(api_client, event, media_ministry, media_leader, media_member):
    entry_id = invite(api_client, media_leader, event, media_ministry, media_member).data["id"]
    assert as_(api_client, media_member).post(f"{ROSTER}/{entry_id}/decline/").status_code == 200
    assert kinds(media_leader) == ["roster_declined"]
    note = Notification.objects.get(recipient=media_leader, kind="roster_declined")
    assert media_member.name in note.title and note.link == f"/app/eventos/{event.id}/producao"


def test_conflict_with_another_event_at_the_same_time(
    api_client, campus, event, media_ministry, media_leader, media_member
):
    other = Event.objects.create(title="Conferência", campus=campus, starts_at=event.starts_at + timedelta(hours=1))
    EventRosterEntry.objects.create(event=other, ministry=media_ministry, person=media_member)
    far = Event.objects.create(title="Outro dia", campus=campus, starts_at=event.starts_at + timedelta(days=1))
    EventRosterEntry.objects.create(event=far, ministry=media_ministry, person=media_member)

    client = as_(api_client, media_leader)
    res = client.get(
        f"{ROSTER}/conflicts/?event={event.id}&ministry={media_ministry.id}&person={media_member.id}"
    )
    assert [c["title"] for c in res.data] == ["Conferência"]

    entry = invite(api_client, media_leader, event, media_ministry, media_member).data
    assert [c["title"] for c in entry["conflicts"]] == ["Conferência"]

    # Only the ministry's leadership asks.
    res = as_(api_client, media_member).get(
        f"{ROSTER}/conflicts/?event={event.id}&ministry={media_ministry.id}&person={media_member.id}"
    )
    assert res.status_code == 403


def test_swap_accepted_passes_the_place(
    api_client, event, media_ministry, media_leader, media_member, other_member
):
    entry_id = invite(api_client, media_leader, event, media_ministry, media_member).data["id"]
    client = as_(api_client, media_member)
    candidates = client.get(f"{ROSTER}/{entry_id}/swap-candidates/").data
    names = {c["name"] for c in candidates}
    assert other_member.name in names and media_leader.name in names and media_member.name not in names

    res = client.post(
        f"{ROSTER}/{entry_id}/swap/", {"substitute_id": str(other_member.id), "message": "Vou viajar"}, format="json"
    )
    assert res.status_code == 201
    swap_id = res.data["id"]
    note = Notification.objects.get(recipient=other_member)
    assert note.kind == "roster_swap" and "Vou viajar" in note.body
    # A second request while one is pending is refused.
    again = client.post(f"{ROSTER}/{entry_id}/swap/", {"substitute_id": str(media_leader.id)}, format="json")
    assert again.status_code == 400
    assert client.get(f"{ROSTER}/mine/").data[0]["pending_swap"]["substitute"]["name"] == other_member.name

    # Only the substitute answers.
    assert client.post(f"{SWAPS}/{swap_id}/accept/").status_code == 403
    sub = as_(api_client, other_member)
    assert [s["id"] for s in sub.get(f"{SWAPS}/").data["results"]] == [swap_id]
    assert sub.post(f"{SWAPS}/{swap_id}/accept/").status_code == 200

    entry = EventRosterEntry.objects.get(pk=entry_id)
    assert entry.person == other_member and entry.status == RosterStatus.CONFIRMED
    assert "roster_swap_answered" in kinds(media_member)
    assert "roster_swap_answered" in kinds(media_leader)  # whoever invited learns of the change
    assert sub.post(f"{SWAPS}/{swap_id}/accept/").status_code in (400, 404)


def test_swap_declined_or_cancelled(api_client, event, media_ministry, media_leader, media_member, other_member):
    entry_id = invite(api_client, media_leader, event, media_ministry, media_member).data["id"]
    me = as_(api_client, media_member)
    swap_id = me.post(f"{ROSTER}/{entry_id}/swap/", {"substitute_id": str(other_member.id)}, format="json").data["id"]
    assert as_(api_client, other_member).post(f"{SWAPS}/{swap_id}/decline/").status_code == 200
    assert EventRosterEntry.objects.get(pk=entry_id).person == media_member
    assert "roster_swap_answered" in kinds(media_member)

    me = as_(api_client, media_member)
    swap_id = me.post(f"{ROSTER}/{entry_id}/swap/", {"substitute_id": str(other_member.id)}, format="json").data["id"]
    assert me.post(f"{SWAPS}/{swap_id}/cancel/").status_code == 200
    assert RosterSwapRequest.objects.get(pk=swap_id).status == SwapStatus.CANCELLED


def test_swap_only_to_someone_of_the_ministry(api_client, campus, event, media_ministry, media_leader, media_member):
    entry_id = invite(api_client, media_leader, event, media_ministry, media_member).data["id"]
    stranger = make_person(email="fora@ilan.church", campus=campus)
    res = as_(api_client, media_member).post(f"{ROSTER}/{entry_id}/swap/", {"substitute_id": str(stranger.id)})
    assert res.status_code == 400
    # Nor by someone who isn't on the entry.
    res = as_(api_client, media_leader).post(f"{ROSTER}/{entry_id}/swap/", {"substitute_id": str(media_leader.id)})
    assert res.status_code == 403


def test_reminders_the_day_before_and_unanswered(campus, media_ministry, media_member, other_member):
    today = timezone.localdate()
    tomorrow = Event.objects.create(
        title="Amanhã", campus=campus, starts_at=timezone.now() + timedelta(days=1)
    )
    soon = Event.objects.create(title="Em breve", campus=campus, starts_at=timezone.now() + timedelta(days=2))
    EventRosterEntry.objects.create(
        event=tomorrow, ministry=media_ministry, person=media_member, status=RosterStatus.CONFIRMED
    )
    EventRosterEntry.objects.create(event=soon, ministry=media_ministry, person=other_member)

    assert reminders.roster_tomorrow(today) == 1
    assert reminders.roster_unanswered(today) == 1
    assert reminders.roster_tomorrow(today) == 0 and reminders.roster_unanswered(today) == 0  # never twice
    assert kinds(media_member) == ["roster_tomorrow"]
    assert kinds(other_member) == ["roster_unanswered"]


def test_demand_received_and_answered(api_client, campus, jovens_leader, media_leader, media_member):
    client = as_(api_client, jovens_leader)
    res = client.post(
        "/api/production-demands/",
        {
            "from_ministry_kind": "jovens",
            "from_campus_id": str(campus.id),
            "to_ministry_kind": "media",
            "to_campus_id": str(campus.id),
            "demand_type": "slide",
            "priority": "urgent",
            "description": "Slides do louvor",
        },
        format="json",
    )
    assert res.status_code == 201, res.data
    note = Notification.objects.get(recipient=media_leader, kind="demand_received")
    assert note.kind == "demand_received" and note.title.startswith("Urgente")
    assert not Notification.objects.filter(recipient=media_member).exists()  # leaders, not every member

    demand = Demand.objects.get(pk=res.data["id"])
    leader = as_(api_client, media_leader)
    assert leader.post(f"/api/production-demands/{demand.id}/accept/").status_code == 200
    assert leader.post(f"/api/production-demands/{demand.id}/complete/").status_code == 200
    titles = list(Notification.objects.filter(recipient=jovens_leader).values_list("title", flat=True))
    assert any(t.startswith("Demanda aceita") for t in titles)
    assert any(t.startswith("Demanda concluída") for t in titles)
