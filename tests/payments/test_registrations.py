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


def test_own_registrations_list_carries_the_event(api_client, regular_person, event, free_config):
    """The "Minhas inscrições" tab lists them without fetching every event."""
    client = auth(api_client, regular_person)
    client.post(URL, {"event_id": str(event.id)}, format="json")

    rows = client.get(URL).data["results"]

    assert len(rows) == 1
    assert rows[0]["event"]["id"] == str(event.id)
    assert rows[0]["event"]["title"] == event.title


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
    first = make_person(campus=event.campus, email="primeiro@ilan.church", name="Primeiro", cpf="11144477735")
    second = make_person(campus=event.campus, email="segundo@ilan.church", name="Segundo", cpf="11144477735")

    assert auth(api_client, first).post(URL, {"event_id": str(event.id)}, format="json").status_code == 201
    response = auth(api_client, second).post(URL, {"event_id": str(event.id)}, format="json")

    assert response.status_code == 400


# --- Cancelling -----------------------------------------------------------------------


def test_person_cancels_an_unpaid_registration(api_client, regular_person, pending_registration, fake_asaas):
    response = auth(api_client, regular_person).post(f"{URL}{pending_registration.id}/cancel/")

    assert response.status_code == 200
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == PaymentStatus.CANCELLED
    # The payment link is killed too, so the old charge can't be paid anymore.
    assert fake_asaas["cancelled"] == ["pay_123"]


def test_cancel_fails_cleanly_when_asaas_is_down(api_client, regular_person, pending_registration, fake_asaas):
    # Cancelling locally while the charge stays payable would let money arrive for nothing.
    fake_asaas["cancel_fails"] = True

    response = auth(api_client, regular_person).post(f"{URL}{pending_registration.id}/cancel/")

    assert response.status_code == 503
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == PaymentStatus.PENDING


def test_person_can_register_again_after_cancelling(api_client, regular_person, event, pending_registration, fake_asaas):
    client = auth(api_client, regular_person)
    client.post(f"{URL}{pending_registration.id}/cancel/")

    response = client.post(URL, {"event_id": str(event.id)}, format="json")

    assert response.status_code == 201, response.data
    # The cancelled one stays as history.
    statuses = set(EventRegistration.objects.filter(event=event, person=regular_person).values_list("payment_status", flat=True))
    assert statuses == {PaymentStatus.CANCELLED, PaymentStatus.PENDING}


def test_cannot_hold_two_active_registrations(api_client, regular_person, event, pending_registration, fake_asaas):
    response = auth(api_client, regular_person).post(URL, {"event_id": str(event.id)}, format="json")

    assert response.status_code == 400


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


def test_revenue_uses_the_amount_each_person_paid(api_client, jovens_leader, event, paid_config, fake_asaas):
    # Two people pay R$150, then the price goes up: revenue must stay R$300.
    for i in range(2):
        person = make_person(
            campus=event.campus, email=f"pagante{i}@ilan.church", name=f"Pagante {i}", cpf="11144477735"
        )
        auth(api_client, person).post(URL, {"event_id": str(event.id)}, format="json")
    EventRegistration.objects.filter(event=event).update(payment_status=PaymentStatus.CONFIRMED)
    paid_config.price_cents = 20000
    paid_config.save()

    response = auth(api_client, jovens_leader).get(f"{URL}stats/?event={event.id}")

    assert response.data["revenue_cents"] == 30000


def test_regular_person_cannot_see_stats(api_client, regular_person, event, pending_registration):
    # Registration counts and revenue are for the event's organizers only.
    response = auth(api_client, regular_person).get(f"{URL}stats/?event={event.id}")

    assert response.status_code == 403


# --- Listing --------------------------------------------------------------------------


def test_mine_returns_only_the_managers_own_registration(api_client, jovens_leader, event, pending_registration):
    # The event page shows "Sua inscrição" from this list; a manager must not see
    # someone else's registration there.
    response = auth(api_client, jovens_leader).get(f"{URL}?event={event.id}&mine=1")

    assert response.status_code == 200
    assert response.data["results"] == []


def test_manager_lists_every_registration_without_mine(api_client, jovens_leader, event, pending_registration):
    response = auth(api_client, jovens_leader).get(f"{URL}?event={event.id}")

    assert [r["id"] for r in response.data["results"]] == [str(pending_registration.id)]


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


# --- CPF (Asaas only charges customers with one) ----------------------------------------


def test_paid_event_asks_for_the_cpf_when_missing(api_client, event, paid_config, fake_asaas):
    person = make_person(campus=event.campus, email="semcpf@ilan.church", name="Sem CPF")
    response = auth(api_client, person).post(URL, {"event_id": str(event.id)}, format="json")

    assert response.status_code == 400
    assert "cpf" in response.data
    assert not EventRegistration.objects.filter(person=person).exists()
    assert fake_asaas["charges"] == []


def test_cpf_typed_at_registration_is_kept_and_sent_to_asaas(api_client, event, paid_config, fake_asaas):
    person = make_person(campus=event.campus, email="semcpf@ilan.church", name="Sem CPF")
    response = auth(api_client, person).post(
        URL, {"event_id": str(event.id), "cpf": "111.444.777-35"}, format="json"
    )

    assert response.status_code == 201, response.data
    assert "cpf" not in response.data  # personal data: never returned
    person.refresh_from_db()
    assert person.cpf == "11144477735"
    assert fake_asaas["customers"][-1] == {"email": "semcpf@ilan.church", "cpf": "11144477735"}


def test_invalid_cpf_is_rejected(api_client, event, paid_config, fake_asaas):
    person = make_person(campus=event.campus, email="semcpf@ilan.church", name="Sem CPF")
    response = auth(api_client, person).post(
        URL, {"event_id": str(event.id), "cpf": "111.444.777-00"}, format="json"
    )

    assert response.status_code == 400
    assert response.data["cpf"] == ["CPF inválido."]


def test_free_event_needs_no_cpf(api_client, event, free_config):
    person = make_person(campus=event.campus, email="semcpf@ilan.church", name="Sem CPF")
    response = auth(api_client, person).post(URL, {"event_id": str(event.id)}, format="json")

    assert response.status_code == 201, response.data
