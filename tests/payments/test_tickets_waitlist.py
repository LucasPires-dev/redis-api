"""Complete registrations: ticket types and batches, companions, the waitlist
of sold-out events and the public link."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.notifications.models import Notification
from apps.notifications.reminders import event_offers_expiring, refresh_event_waitlists
from apps.payments.models import (
    EventRegistration,
    EventTicketConfig,
    EventTicketType,
    EventWaitlistEntry,
    PaymentStatus,
)
from apps.production.models import Event
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

URL = "/api/payments"


def as_(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def register(client, event, **body):
    return client.post(f"{URL}/registrations/", {"event_id": str(event.id), **body}, format="json")


@pytest.fixture
def free_with_types(event):
    config = EventTicketConfig.objects.create(event=event, price_cents=0, max_capacity=3, max_companions=2)
    now = timezone.now()
    inteira = EventTicketType.objects.create(config=config, name="Inteira", price_cents=0, order=1)
    lote = EventTicketType.objects.create(
        config=config, name="1º lote", price_cents=0, sales_end=now - timedelta(days=1), order=0
    )
    return config, inteira, lote


# ── Ticket types and batches ─────────────────────────────────────────────────


def test_type_is_required_and_must_be_on_sale(api_client, regular_person, event, free_with_types):
    _, inteira, lote = free_with_types
    client = as_(api_client, regular_person)
    assert register(client, event).data["ticket_type_id"]
    res = register(client, event, ticket_type_id=str(lote.id))
    assert res.status_code == 400 and "não está à venda" in str(res.data)
    res = register(client, event, ticket_type_id=str(inteira.id))
    assert res.status_code == 201 and res.data["ticket_type"]["name"] == "Inteira"


def test_paid_type_charges_its_price_times_the_seats(api_client, regular_person, event, fake_asaas):
    config = EventTicketConfig.objects.create(event=event, price_cents=0, max_companions=1)
    meia = EventTicketType.objects.create(config=config, name="Meia", price_cents=2500)
    res = register(as_(api_client, regular_person), event, ticket_type_id=str(meia.id), guest_names=["Ana"])
    assert res.status_code == 201, res.data
    assert res.data["amount_cents"] == 5000 and res.data["seats"] == 2
    assert fake_asaas["charges"][0]["amount_cents"] == 5000


def test_type_capacity(api_client, regular_person, campus, event, free_with_types):
    _, inteira, _ = free_with_types
    inteira.capacity = 1
    inteira.save()
    assert register(as_(api_client, regular_person), event, ticket_type_id=str(inteira.id)).status_code == 201
    other = make_person(email="outra@ilan.church", campus=campus)
    res = register(as_(api_client, other), event, ticket_type_id=str(inteira.id))
    assert res.status_code == 400 and "esgotou" in str(res.data)


def test_organizers_manage_types_and_a_sold_type_is_not_deleted(
    api_client, jovens_leader, regular_person, event, free_with_types
):
    config, inteira, _ = free_with_types
    leader = as_(api_client, jovens_leader)
    res = leader.post(f"{URL}/ticket-types/", {"config_id": str(config.id), "name": "Meia", "price_cents": 300})
    assert res.status_code == 400  # under the R$ 5 minimum
    res = leader.post(f"{URL}/ticket-types/", {"config_id": str(config.id), "name": "Meia", "price_cents": 0})
    assert res.status_code == 201

    register(as_(api_client, regular_person), event, ticket_type_id=str(inteira.id))
    assert as_(api_client, jovens_leader).delete(f"{URL}/ticket-types/{inteira.id}/").status_code == 400
    assert as_(api_client, regular_person).post(
        f"{URL}/ticket-types/", {"config_id": str(config.id), "name": "X", "price_cents": 0}
    ).status_code == 403


# ── Companions ───────────────────────────────────────────────────────────────


def test_companions_get_their_own_tickets_and_check_in(
    api_client, regular_person, jovens_leader, event, free_with_types
):
    _, inteira, _ = free_with_types
    client = as_(api_client, regular_person)
    res = register(client, event, ticket_type_id=str(inteira.id), guest_names=["Ana", "Bia", "Caio"])
    assert res.status_code == 400  # at most 2

    res = register(client, event, ticket_type_id=str(inteira.id), guest_names=["Ana", " Bia "])
    assert res.status_code == 201 and res.data["seats"] == 3
    guests = res.data["guests"]
    assert [g["name"] for g in guests] == ["Ana", "Bia"]
    codes = {res.data["ticket_code"], *(g["ticket_code"] for g in guests)}
    assert len(codes) == 3

    door = as_(api_client, jovens_leader).get(f"{URL}/registrations/door-list/?event={event.id}").data
    assert door["confirmed"] == 3
    assert [r["guest_of"] for r in door["registrations"]].count(regular_person.name) == 2

    res = as_(api_client, jovens_leader).post(
        f"{URL}/registrations/checkin/", {"event_id": str(event.id), "code": guests[0]["ticket_code"]}, format="json"
    )
    assert res.status_code == 200 and res.data["registration"]["person"]["name"] == "Ana"
    assert res.data["registration"]["guest_of"] == regular_person.name


# ── Waitlist ─────────────────────────────────────────────────────────────────


def test_sold_out_waitlist_and_offer(api_client, regular_person, campus, event, free_with_types):
    _, inteira, _ = free_with_types  # 3 places
    first = make_person(email="primeiro@ilan.church", campus=campus)
    res = register(as_(api_client, first), event, ticket_type_id=str(inteira.id), guest_names=["A", "B"])
    assert res.status_code == 201
    registration_id = res.data["id"]

    client = as_(api_client, regular_person)
    res = register(client, event, ticket_type_id=str(inteira.id))
    assert res.status_code == 400 and res.data["code"] == "full"

    res = client.post(f"{URL}/waitlist/", {"event_id": str(event.id)}, format="json")
    assert res.status_code == 201 and res.data["position"] == 1 and res.data["offer_expires_at"] is None
    assert client.post(f"{URL}/waitlist/", {"event_id": str(event.id)}, format="json").status_code == 400

    # Someone else arrives later and waits too.
    late = make_person(email="tarde@ilan.church", campus=campus)
    as_(api_client, late).post(f"{URL}/waitlist/", {"event_id": str(event.id)}, format="json")

    # The first one cancels: the places go to the first in line, held.
    assert as_(api_client, first).post(f"{URL}/registrations/{registration_id}/cancel/").status_code == 200
    mine = EventWaitlistEntry.objects.get(person=regular_person)
    assert mine.offer_expires_at is not None
    assert Notification.objects.filter(recipient=regular_person, kind="event_waitlist_offer").exists()
    # The second in line also fits (3 places free, 1 held): offered too.
    assert EventWaitlistEntry.objects.get(person=late).offer_expires_at is not None

    # A newcomer can't take held places, but there's one left.
    newcomer = make_person(email="novo@ilan.church", campus=campus)
    assert register(as_(api_client, newcomer), event, ticket_type_id=str(inteira.id)).status_code == 201
    stranger = make_person(email="estranho@ilan.church", campus=campus)
    assert register(as_(api_client, stranger), event, ticket_type_id=str(inteira.id)).data["code"] == "full"

    # The person with the offer registers, and leaves the line.
    assert register(as_(api_client, regular_person), event, ticket_type_id=str(inteira.id)).status_code == 201
    assert not EventWaitlistEntry.objects.filter(person=regular_person).exists()


def test_expired_offer_passes_to_the_next(api_client, regular_person, campus, event, free_with_types):
    config, _, _ = free_with_types
    config.max_capacity = 1
    config.save()
    holder = make_person(email="dono@ilan.church", campus=campus)
    EventRegistration.objects.create(event=event, person=holder, payment_status=PaymentStatus.CONFIRMED)
    first = EventWaitlistEntry.objects.create(event=event, person=regular_person)
    second_person = make_person(email="segundo@ilan.church", campus=campus)
    second = EventWaitlistEntry.objects.create(event=event, person=second_person)

    # More places: the hourly run hands them out.
    config.max_capacity = 2
    config.save()
    refresh_event_waitlists()
    first.refresh_from_db()
    assert first.offer_expires_at is not None

    # The reminder before it runs out, once.
    EventWaitlistEntry.objects.filter(pk=first.pk).update(offer_expires_at=timezone.now() + timedelta(hours=3))
    assert event_offers_expiring() == 1 and event_offers_expiring() == 0

    # It runs out: the next one gets it.
    EventWaitlistEntry.objects.filter(pk=first.pk).update(offer_expires_at=timezone.now() - timedelta(minutes=1))
    refresh_event_waitlists()
    assert not EventWaitlistEntry.objects.filter(pk=first.pk).exists()
    second.refresh_from_db()
    assert second.offer_expires_at is not None


def test_cannot_wait_while_there_are_places(api_client, regular_person, event, free_with_types):
    res = as_(api_client, regular_person).post(f"{URL}/waitlist/", {"event_id": str(event.id)}, format="json")
    assert res.status_code == 400 and res.data["code"] == "has_seats"


# ── Public link ──────────────────────────────────────────────────────────────


def test_public_link_only_when_opened(api_client, other_campus, free_with_types, event):
    config, _, _ = free_with_types
    api_client.force_authenticate(user=None)
    assert api_client.get(f"{URL}/public-events/{event.id}/").status_code == 404

    config.is_public = True
    config.save()
    res = api_client.get(f"{URL}/public-events/{event.id}/")
    assert res.status_code == 200
    assert res.data["title"] == event.title
    assert [t["name"] for t in res.data["ticket"]["types"]] == ["Inteira"]  # the closed batch is hidden
    assert res.data["registration_open"] is True and res.data["sold_out"] is False

    # Someone from another campus (or no campus) can see and register.
    outsider = make_person(email="visitante@gmail.com", campus=None)
    client = as_(api_client, outsider)
    assert client.get(f"/api/production-events/{event.id}/").status_code == 200
    inteira = config.types.get(name="Inteira")
    assert register(client, event, ticket_type_id=str(inteira.id)).status_code == 201


def test_private_event_stays_hidden_from_other_campuses(api_client, other_campus, event, free_with_types):
    outsider = make_person(email="fora@ilan.church", campus=other_campus)
    res = register(as_(api_client, outsider), event, ticket_type_id=str(free_with_types[1].id))
    assert res.status_code == 400 and "Evento não encontrado" in str(res.data)


def test_event_created_with_a_public_ticket(api_client, jovens_leader, campus):
    res = as_(api_client, jovens_leader).post(
        "/api/production-events/",
        {
            "title": "Conferência",
            "ministry_kind": "jovens",
            "campus_id": str(campus.id),
            "event_type": "ministry_event",
            "starts_at": (timezone.now() + timedelta(days=10)).isoformat(),
            "ticket": {"price_cents": 0, "max_companions": 2, "is_public": True},
        },
        format="json",
    )
    assert res.status_code == 201, res.data
    config = Event.objects.get(pk=res.data["id"]).ticket_config
    assert config.is_public and config.max_companions == 2
