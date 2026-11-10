"""The Asaas webhook is public, so it must only trust calls that really come from
Asaas and only confirm payments Asaas reports as paid. Otherwise anyone could
POST "PAYMENT_CONFIRMED" for their own registration and get a free ticket.
"""

import pytest

from apps.payments.models import PaymentStatus
from tests.payments.conftest import WEBHOOK_TOKEN

pytestmark = pytest.mark.django_db

URL = "/api/payments/webhooks/asaas/"


def post_webhook(api_client, registration, event="PAYMENT_CONFIRMED", token=WEBHOOK_TOKEN, payment_id="pay_123"):
    headers = {"HTTP_ASAAS_ACCESS_TOKEN": token} if token is not None else {}
    body = {"event": event, "payment": {"id": payment_id, "externalReference": str(registration.id)}}
    return api_client.post(URL, body, format="json", **headers)


# --- Authenticity ---------------------------------------------------------------------


def test_valid_token_confirms_the_payment(api_client, pending_registration, fake_asaas):
    response = post_webhook(api_client, pending_registration)

    assert response.status_code == 200
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == PaymentStatus.CONFIRMED
    assert pending_registration.paid_at is not None


@pytest.mark.parametrize("token", [None, "", "wrong-token"], ids=["missing", "empty", "wrong"])
def test_forged_call_is_rejected(api_client, pending_registration, fake_asaas, token):
    # The attack: a user posts a fake confirmation for their own registration id.
    response = post_webhook(api_client, pending_registration, token=token)

    assert response.status_code == 401
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == PaymentStatus.PENDING


def test_webhook_fails_closed_without_configured_token(
    api_client, settings, pending_registration, fake_asaas
):
    # A deploy that forgot ASAAS_WEBHOOK_TOKEN must not accept anything.
    settings.ASAAS_WEBHOOK_TOKEN = ""

    response = post_webhook(api_client, pending_registration, token="")

    assert response.status_code == 401
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == PaymentStatus.PENDING


# --- Consistency with Asaas -------------------------------------------------------------


def test_confirmation_is_ignored_when_asaas_says_not_paid(api_client, pending_registration, fake_asaas):
    # Even with a valid token (e.g. a replayed or misrouted event), Asaas is the source of truth.
    fake_asaas["status"] = "PENDING"

    response = post_webhook(api_client, pending_registration)

    assert response.status_code == 200
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == PaymentStatus.PENDING


def test_payment_of_another_registration_is_ignored(api_client, pending_registration, fake_asaas):
    response = post_webhook(api_client, pending_registration, payment_id="pay_someone_else")

    assert response.status_code == 200
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == PaymentStatus.PENDING


def test_asaas_unreachable_asks_for_a_retry(api_client, pending_registration, fake_asaas):
    # Non-2xx makes Asaas resend the event later instead of losing the confirmation.
    fake_asaas["status_fails"] = True

    response = post_webhook(api_client, pending_registration)

    assert response.status_code == 502
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == PaymentStatus.PENDING


def test_unknown_registration_is_acknowledged(api_client, pending_registration, fake_asaas):
    # 200 so Asaas doesn't pause the webhook queue over an unrelated payment.
    body = {"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_x", "externalReference": "not-a-uuid"}}

    response = api_client.post(URL, body, format="json", HTTP_ASAAS_ACCESS_TOKEN=WEBHOOK_TOKEN)

    assert response.status_code == 200


# --- Other status changes ---------------------------------------------------------------


@pytest.mark.parametrize(
    "asaas_event, expected",
    [
        ("PAYMENT_REFUNDED", PaymentStatus.REFUNDED),
        ("PAYMENT_DELETED", PaymentStatus.CANCELLED),
        ("PAYMENT_OVERDUE", PaymentStatus.CANCELLED),
    ],
)
def test_status_changes_are_applied(api_client, pending_registration, fake_asaas, asaas_event, expected):
    response = post_webhook(api_client, pending_registration, event=asaas_event)

    assert response.status_code == 200
    pending_registration.refresh_from_db()
    assert pending_registration.payment_status == expected
