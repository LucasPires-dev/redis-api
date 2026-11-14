"""Creating an event together with its ticket (price, capacity, deadline) in one
request, and the ticket business rules shared with the ticket config endpoint."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.payments.models import EventRegistration, EventTicketConfig
from apps.production.models import Event

pytestmark = pytest.mark.django_db

URL = "/api/production-events/"


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def event_payload(campus, **extra):
    starts = timezone.now() + timedelta(days=30)
    return {
        "title": "Retiro de Jovens",
        "event_type": "ministry_event",
        "ministry_kind": "jovens",
        "campus_id": str(campus.id),
        "starts_at": starts.isoformat(),
        **extra,
    }


# --- Creating with a ticket -----------------------------------------------------------


def test_ministry_leader_creates_a_paid_event_in_one_step(api_client, jovens_leader, campus, jovens_ministry):
    deadline = (timezone.now() + timedelta(days=20)).isoformat()
    payload = event_payload(
        campus,
        ticket={
            "price_cents": 15000,
            "max_capacity": 80,
            "registration_deadline": deadline,
            "description": "Hospedagem e alimentação",
        },
    )

    response = auth(api_client, jovens_leader).post(URL, payload, format="json")

    assert response.status_code == 201, response.data
    config = EventTicketConfig.objects.get(event_id=response.data["id"])
    assert (config.price_cents, config.max_capacity, config.description) == (
        15000,
        80,
        "Hospedagem e alimentação",
    )
    # The response already carries the ticket summary, so the UI needn't refetch.
    assert response.data["ticket_config"]["price_cents"] == 15000
    assert "ticket" not in response.data  # write-only


def test_free_event_with_registration(api_client, jovens_leader, campus, jovens_ministry):
    response = auth(api_client, jovens_leader).post(
        URL, event_payload(campus, ticket={"price_cents": 0}), format="json"
    )

    assert response.status_code == 201, response.data
    assert EventTicketConfig.objects.get(event_id=response.data["id"]).price_cents == 0


def test_event_without_ticket_has_no_registration(api_client, jovens_leader, campus, jovens_ministry):
    response = auth(api_client, jovens_leader).post(URL, event_payload(campus), format="json")

    assert response.status_code == 201, response.data
    assert response.data["ticket_config"] is None
    assert not EventTicketConfig.objects.exists()


# --- Validation (nothing is created when the ticket is invalid) -------------------------


@pytest.mark.parametrize(
    "ticket, field",
    [
        ({"price_cents": 300}, "price_cents"),  # below Asaas's R$ 5,00 minimum
        ({"price_cents": -1}, "price_cents"),
        ({"price_cents": 1000, "max_capacity": 0}, "max_capacity"),
    ],
    ids=["below-minimum", "negative", "zero-capacity"],
)
def test_invalid_ticket_rejects_the_whole_event(api_client, jovens_leader, campus, jovens_ministry, ticket, field):
    response = auth(api_client, jovens_leader).post(URL, event_payload(campus, ticket=ticket), format="json")

    assert response.status_code == 400
    assert field in response.data["ticket"]
    # All or nothing: no half-created event without its ticket.
    assert not Event.objects.exists()


def test_deadline_after_the_event_starts_is_rejected(api_client, jovens_leader, campus, jovens_ministry):
    payload = event_payload(campus)
    late = (timezone.now() + timedelta(days=31)).isoformat()
    payload["ticket"] = {"price_cents": 0, "registration_deadline": late}

    response = auth(api_client, jovens_leader).post(URL, payload, format="json")

    assert response.status_code == 400
    assert "registration_deadline" in response.data["ticket"]


def test_ticket_cannot_be_changed_through_the_event(api_client, jovens_leader, campus, jovens_ministry):
    created = auth(api_client, jovens_leader).post(URL, event_payload(campus), format="json").data

    response = api_client.patch(f"{URL}{created['id']}/", {"ticket": {"price_cents": 1000}}, format="json")

    assert response.status_code == 400
    assert not EventTicketConfig.objects.exists()


def test_permission_still_applies(api_client, regular_person, campus, jovens_ministry):
    # A ticket doesn't widen who may create events.
    response = auth(api_client, regular_person).post(
        URL, event_payload(campus, ticket={"price_cents": 0}), format="json"
    )

    assert response.status_code == 403
    assert not Event.objects.exists()


# --- Same rules on the ticket config endpoint ------------------------------------------


def test_config_endpoint_rejects_price_below_minimum(api_client, jovens_leader, paid_config):
    response = auth(api_client, jovens_leader).patch(
        f"/api/payments/ticket-configs/{paid_config.id}/", {"price_cents": 100}, format="json"
    )

    assert response.status_code == 400
    assert "price_cents" in response.data


def test_capacity_cannot_drop_below_active_registrations(api_client, jovens_leader, event, paid_config):
    from tests.conftest import make_person

    for i in range(3):
        EventRegistration.objects.create(
            event=event, person=make_person(email=f"insc{i}@ilan.church", name=f"Insc {i}")
        )

    response = auth(api_client, jovens_leader).patch(
        f"/api/payments/ticket-configs/{paid_config.id}/", {"max_capacity": 2}, format="json"
    )

    assert response.status_code == 400
    assert "max_capacity" in response.data
