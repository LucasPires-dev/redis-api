"""Check-in at the door: ticket codes, who may check people in, and the rules."""

import pytest

from apps.payments.models import EventRegistration, PaymentStatus
from apps.production.choices import RosterStatus
from apps.production.models import EventRosterEntry
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

URL = "/api/payments/registrations"


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def confirmed(event, regular_person):
    return EventRegistration.objects.create(
        event=event, person=regular_person, payment_status=PaymentStatus.CONFIRMED
    )


def checkin(client, event, **body):
    return client.post(f"{URL}/checkin/", {"event_id": str(event.id), **body}, format="json")


def test_every_registration_gets_a_unique_ticket_code(event, confirmed, campus):
    other = EventRegistration.objects.create(event=event, person=make_person(email="b@ilan.church", campus=campus))
    assert len(confirmed.ticket_code) == 10
    assert confirmed.ticket_code != other.ticket_code


def test_the_person_sees_their_ticket_code(api_client, confirmed, regular_person):
    res = auth(api_client, regular_person).get(f"{URL}/?mine=1")
    (row,) = res.data["results"]
    assert row["ticket_code"] == confirmed.ticket_code
    assert row["checked_in_at"] is None


def test_organizer_checks_in_by_code_once(api_client, event, confirmed, jovens_leader, regular_person):
    client = auth(api_client, jovens_leader)
    typed = f" {confirmed.ticket_code[:5].lower()}-{confirmed.ticket_code[5:]} "  # as typed at the door
    res = checkin(client, event, code=typed)
    assert res.status_code == 200
    assert res.data["already_checked_in"] is False
    assert res.data["registration"]["person"]["name"] == regular_person.name
    assert "ticket_code" not in res.data["registration"]

    confirmed.refresh_from_db()
    assert confirmed.checked_in_at is not None and confirmed.checked_in_by == jovens_leader

    again = checkin(client, event, code=confirmed.ticket_code)
    assert again.status_code == 200 and again.data["already_checked_in"] is True

    stats = client.get(f"{URL}/stats/?event={event.id}").data
    assert stats["confirmed"] == 1 and stats["checked_in"] == 1


def test_found_by_name_and_undone(api_client, event, confirmed, jovens_leader):
    client = auth(api_client, jovens_leader)
    door = client.get(f"{URL}/door-list/?event={event.id}").data
    assert door["confirmed"] == 1 and door["checked_in"] == 0
    (row,) = door["registrations"]

    assert checkin(client, event, registration_id=row["id"]).status_code == 200
    assert client.get(f"{URL}/door-list/?event={event.id}").data["checked_in"] == 1

    res = client.post(f"{URL}/undo-checkin/", {"event_id": str(event.id), "registration_id": row["id"]}, format="json")
    assert res.status_code == 200 and res.data["registration"]["checked_in_at"] is None


def test_pending_or_cancelled_tickets_are_refused(api_client, event, pending_registration, jovens_leader):
    client = auth(api_client, jovens_leader)
    res = checkin(client, event, code=pending_registration.ticket_code)
    assert res.status_code == 400 and res.data["code"] == "pending"

    pending_registration.payment_status = PaymentStatus.CANCELLED
    pending_registration.save()
    res = checkin(client, event, code=pending_registration.ticket_code)
    assert res.status_code == 400 and res.data["code"] == "cancelled"


def test_a_ticket_of_another_event_is_not_found(api_client, event, confirmed, jovens_leader, campus):
    from apps.production.models import Event

    other = Event.objects.create(title="Outro", campus=campus, ministry_kind="jovens", starts_at=event.starts_at)
    res = checkin(auth(api_client, jovens_leader), other, code=confirmed.ticket_code)
    assert res.status_code == 404
    assert checkin(auth(api_client, jovens_leader), event, code="NAOEXISTE").status_code == 404


def test_confirmed_roster_volunteers_check_in_but_others_dont(
    api_client, event, confirmed, campus, jovens_ministry, jovens_leader
):
    volunteer = make_person(email="recepcao@ilan.church", name="Recepção", campus=campus)
    entry = EventRosterEntry.objects.create(
        event=event, ministry=jovens_ministry, person=volunteer, invited_by=jovens_leader
    )
    # Only invited (not confirmed yet): not on the team.
    assert checkin(auth(api_client, volunteer), event, code=confirmed.ticket_code).status_code == 403

    entry.status = RosterStatus.CONFIRMED
    entry.save()
    assert checkin(auth(api_client, volunteer), event, code=confirmed.ticket_code).status_code == 200
    assert auth(api_client, volunteer).get(f"{URL}/door-list/?event={event.id}").status_code == 200

    # The attendee can't check themselves in.
    assert checkin(auth(api_client, confirmed.person), event, code=confirmed.ticket_code).status_code == 403
