"""Discount coupons: who manages them, checking a code, and the discount on
paid event registrations and course enrollments."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.payments.models import Coupon, EventRegistration, PaymentStatus
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

COUPONS = "/api/payments/coupons/"
REGISTER = "/api/payments/registrations/"


def as_user(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def event_coupon(event, code="JOVEM20", **kw):
    data = {"kind": "percent", "percent": 20}
    data.update(kw)
    return Coupon.objects.create(event=event, code=code, **data)


# --- Managing -------------------------------------------------------------------------


def test_event_organizer_creates_and_lists_coupons(api_client, jovens_leader, event, paid_config):
    client = as_user(api_client, jovens_leader)
    response = client.post(
        COUPONS, {"event_id": str(event.id), "code": " jovem20 ", "kind": "percent", "percent": 20}, format="json"
    )

    assert response.status_code == 201, response.data
    assert response.data["code"] == "JOVEM20"
    assert response.data["label"] == "20% de desconto"
    rows = client.get(f"{COUPONS}?event={event.id}").data["results"]
    assert [r["code"] for r in rows] == ["JOVEM20"]


def test_coupon_needs_a_valid_value_and_a_unique_code(api_client, jovens_leader, event, paid_config):
    client = as_user(api_client, jovens_leader)
    base = {"event_id": str(event.id), "code": "X10"}

    assert client.post(COUPONS, {**base, "kind": "percent", "percent": 0}, format="json").status_code == 400
    assert client.post(COUPONS, {**base, "kind": "percent", "percent": 101}, format="json").status_code == 400
    assert client.post(COUPONS, {**base, "kind": "fixed"}, format="json").status_code == 400
    assert client.post(COUPONS, {**base, "code": "com espaço", "kind": "percent", "percent": 5},
                       format="json").status_code == 400
    assert client.post(COUPONS, {**base, "kind": "fixed", "amount_cents": 1000}, format="json").status_code == 201
    again = client.post(COUPONS, {**base, "code": "x10", "kind": "percent", "percent": 5}, format="json")
    assert again.status_code == 400
    assert "code" in again.data


def test_only_organizers_manage_coupons(api_client, regular_person, event, paid_config):
    client = as_user(api_client, regular_person)
    created = client.post(COUPONS, {"event_id": str(event.id), "code": "HACK", "kind": "percent", "percent": 100},
                          format="json")
    assert created.status_code == 403
    assert client.get(f"{COUPONS}?event={event.id}").status_code == 403
    coupon = event_coupon(event)
    assert client.patch(f"{COUPONS}{coupon.id}/", {"percent": 100}, format="json").status_code == 403


def test_used_coupon_cannot_be_deleted_only_deactivated(
    api_client, jovens_leader, regular_person, event, paid_config, fake_asaas
):
    coupon = event_coupon(event)
    as_user(api_client, regular_person).post(
        REGISTER, {"event_id": str(event.id), "coupon_code": "JOVEM20"}, format="json"
    )

    client = as_user(api_client, jovens_leader)
    assert client.delete(f"{COUPONS}{coupon.id}/").status_code == 400
    assert client.patch(f"{COUPONS}{coupon.id}/", {"is_active": False}, format="json").status_code == 200
    unused = event_coupon(event, code="OUTRO")
    assert client.delete(f"{COUPONS}{unused.id}/").status_code == 204


# --- Checking a code ------------------------------------------------------------------


def test_person_checks_a_code_before_registering(api_client, regular_person, event, paid_config):
    event_coupon(event)
    client = as_user(api_client, regular_person)

    ok = client.post(f"{COUPONS}check/", {"code": "jovem20", "event_id": str(event.id)}, format="json")
    assert ok.status_code == 200, ok.data
    assert ok.data["percent"] == 20
    bad = client.post(f"{COUPONS}check/", {"code": "NADA", "event_id": str(event.id)}, format="json")
    assert bad.status_code == 400
    assert bad.data["coupon_code"] == "Cupom inválido."


def test_expired_inactive_and_exhausted_coupons_are_refused(api_client, regular_person, event, paid_config):
    client = as_user(api_client, regular_person)

    def check(code):
        return client.post(f"{COUPONS}check/", {"code": code, "event_id": str(event.id)}, format="json")

    event_coupon(event, code="VELHO", valid_until=timezone.now() - timedelta(hours=1))
    event_coupon(event, code="DESLIGADO", is_active=False)
    used = event_coupon(event, code="UMSO", max_uses=1)
    other = make_person(email="outra@ilan.church", name="Outra", campus=event.campus)
    EventRegistration.objects.create(event=event, person=other, coupon=used, payment_status=PaymentStatus.CONFIRMED)

    assert check("VELHO").data["coupon_code"] == "Este cupom expirou."
    assert check("DESLIGADO").data["coupon_code"] == "Cupom inválido."
    assert check("UMSO").data["coupon_code"] == "Este cupom já atingiu o limite de usos."


# --- Event registration ---------------------------------------------------------------


def test_coupon_lowers_the_event_charge(api_client, regular_person, event, paid_config, fake_asaas):
    event_coupon(event)  # 20% of R$ 150

    response = as_user(api_client, regular_person).post(
        REGISTER, {"event_id": str(event.id), "coupon_code": "jovem20"}, format="json"
    )

    assert response.status_code == 201, response.data
    registration = EventRegistration.objects.get(person=regular_person)
    assert registration.amount_cents == 12000
    assert registration.discount_cents == 3000
    assert registration.coupon.code == "JOVEM20"
    assert fake_asaas["charges"][0]["amount_cents"] == 12000


def test_full_coupon_confirms_without_a_charge(api_client, regular_person, event, paid_config, fake_asaas):
    event_coupon(event, code="CORTESIA", percent=100)

    response = as_user(api_client, regular_person).post(
        REGISTER, {"event_id": str(event.id), "coupon_code": "CORTESIA"}, format="json"
    )

    assert response.status_code == 201, response.data
    registration = EventRegistration.objects.get(person=regular_person)
    assert registration.payment_status == PaymentStatus.CONFIRMED
    assert registration.amount_cents == 0
    assert fake_asaas["charges"] == []


def test_coupon_cannot_leave_less_than_the_minimum_charge(api_client, regular_person, event, paid_config, fake_asaas):
    event_coupon(event, code="QUASE", kind="fixed", percent=None, amount_cents=14800)  # leaves R$ 2

    response = as_user(api_client, regular_person).post(
        REGISTER, {"event_id": str(event.id), "coupon_code": "QUASE"}, format="json"
    )

    assert response.status_code == 400
    assert "R$ 5,00" in response.data["coupon_code"]
    assert not EventRegistration.objects.exists()


def test_cancelled_registration_gives_the_use_back(api_client, regular_person, event, paid_config, fake_asaas):
    coupon = event_coupon(event, max_uses=1)
    client = as_user(api_client, regular_person)
    client.post(REGISTER, {"event_id": str(event.id), "coupon_code": "JOVEM20"}, format="json")
    registration = EventRegistration.objects.get(person=regular_person)
    registration.payment_status = PaymentStatus.CANCELLED
    registration.save()

    again = client.post(REGISTER, {"event_id": str(event.id), "coupon_code": "JOVEM20"}, format="json")

    assert again.status_code == 201, again.data
    assert coupon.event_registrations.count() == 2


def test_another_events_coupon_does_not_work(api_client, regular_person, event, paid_config, fake_asaas):
    from apps.production.models import Event

    other = Event.objects.create(
        title="Outro", campus=event.campus, event_type="ministry_event",
        starts_at=timezone.now() + timedelta(days=5),
    )
    event_coupon(other)

    response = as_user(api_client, regular_person).post(
        REGISTER, {"event_id": str(event.id), "coupon_code": "JOVEM20"}, format="json"
    )

    assert response.status_code == 400
    assert response.data["coupon_code"] == "Cupom inválido."


def test_deleting_the_event_takes_its_used_coupons_along(api_client, regular_person, event, paid_config, fake_asaas):
    from apps.production.models import Event

    coupon = event_coupon(event)
    as_user(api_client, regular_person).post(
        REGISTER, {"event_id": str(event.id), "coupon_code": "JOVEM20"}, format="json"
    )

    event.delete()

    assert not Event.objects.filter(pk=event.pk).exists()
    assert not Coupon.objects.filter(pk=coupon.pk).exists()
