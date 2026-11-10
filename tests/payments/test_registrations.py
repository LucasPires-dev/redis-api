"""Registering for events with tickets: free and paid flows, failures talking to
Asaas, cancelling, and who may see an event's registration stats."""

import pytest

from apps.payments.models import EventRegistration, PaymentStatus
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

URL = "/api/payments/registrations/"


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


# --- Registering ----------------------------------------------------------------------


def test_free_event_registration_is_confirmed_right_away(api_client, regular_person, event, free_config):
    response = auth(api_client, regular_person).post(URL, {"event_id": str(event.id)}, format="json")

    assert response.status_code == 201, response.data
    registration = EventRegistration.objects.get(event=event, person=regular_person)
    assert registration.payment_status == PaymentStatus.CONFIRMED


def test_paid_event_creates_an_asaas_charge(api_client, regular_person, event, paid_config, fake_asaas):
    response = auth(api_client, regular_person).post(URL, {"event_id": str(event.id)}, format="json")

    assert response.status_code == 201, response.data
    registration = EventRegistration.objects.get(event=event, person=regular_person)
    assert registration.payment_status == PaymentStatus.PENDING
    assert registration.asaas_payment_id == "pay_123"
    assert registration.payment_url == "https://asaas.test/i/pay_123"
    # The charge points back to the registration, so the webhook can find it.
    assert fake_asaas["charges"] == [{"amount_cents": 15000, "external_reference": str(registration.id)}]


def test_failed_charge_leaves_no_registration_behind(api_client, regular_person, event, paid_config, fake_asaas):
    # Otherwise the person would be stuck: "already registered", but nothing to pay.
    fake_asaas["charge_fails"] = True

    response = auth(api_client, regular_person).post(URL, {"event_id": str(event.id)}, format="json")

    assert response.status_code == 503
    assert not EventRegistration.objects.filter(event=event, person=regular_person).exists()


def test_person_can_retry_after_a_failed_charge(api_client, regular_person, event, paid_config, fake_asaas):
    client = auth(api_client, regular_person)
    fake_asaas["charge_fails"] = True
    client.post(URL, {"event_id": str(event.id)}, format="json")

    fake_asaas["charge_fails"] = False
    response = client.post(URL, {"event_id": str(event.id)}, format="json")

    assert response.status_code == 201, response.data


def test_full_event_rejects_new_registrations(api_client, event, paid_config, fake_asaas):
    paid_config.max_capacity = 1
    paid_config.save()
    first = make_person(email="primeiro@ilan.church", name="Primeiro")
    second = make_person(email="segundo@ilan.church", name="Segundo")

    assert auth(api_client, first).post(URL, {"event_id": str(event.id)}, format="json").status_code == 201
    response = auth(api_client, second).post(URL, {"event_id": str(event.id)}, format="json")

    assert response.status_code == 400


# --- Cancelling -----------------------------------------------------------------------


def test_person_cancels_an_unpaid_registration(api_client, regular_person, pending_registration):
    response = auth(api_client, regular_person).post(f"{URL}{pending_registration.id}/cancel/")

    assert response.status_code == 200
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == PaymentStatus.CANCELLED


def test_paid_registration_cannot_be_cancelled_by_the_person(api_client, regular_person, pending_registration):
    # Cancelling here wouldn't refund anything in Asaas — the money would just be kept.
    pending_registration.payment_status = PaymentStatus.CONFIRMED
    pending_registration.save()

    response = auth(api_client, regular_person).post(f"{URL}{pending_registration.id}/cancel/")

    assert response.status_code == 400
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == PaymentStatus.CONFIRMED


# --- Stats ----------------------------------------------------------------------------


def test_event_manager_sees_stats(api_client, jovens_leader, event, pending_registration):
    response = auth(api_client, jovens_leader).get(f"{URL}stats/?event={event.id}")

    assert response.status_code == 200
    assert response.data["pending"] == 1


def test_regular_person_cannot_see_stats(api_client, regular_person, event, pending_registration):
    # Registration counts and revenue are for the event's organizers only.
    response = auth(api_client, regular_person).get(f"{URL}stats/?event={event.id}")

    assert response.status_code == 403


# --- Ticket config --------------------------------------------------------------------


def test_ticket_config_cannot_be_moved_to_another_event(api_client, jovens_leader, campus, paid_config):
    from django.utils import timezone

    from apps.production.models import Event

    # An event the leader doesn't manage (a general culto with no ministry).
    other = Event.objects.create(title="Outro", campus=campus, starts_at=timezone.now())

    response = auth(api_client, jovens_leader).patch(
        f"/api/payments/ticket-configs/{paid_config.id}/", {"event_id": str(other.id)}, format="json"
    )

    assert response.status_code == 400
    paid_config.refresh_from_db()
    assert paid_config.event_id != other.id
