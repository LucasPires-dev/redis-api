"""Event reports: refunds, expenses, the report, CSV exports and comparison."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from apps.notifications.models import Notification
from apps.payments.asaas import AsaasError
from apps.payments.models import (
    EventExpense,
    EventRegistration,
    EventRegistrationGuest,
    EventTicketConfig,
    PaymentStatus,
)
from apps.production.models import Event
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

URL = "/api/payments"


def as_(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def paid(event, person, amount=15000, **kw):
    return EventRegistration.objects.create(
        event=event, person=person, payment_status=PaymentStatus.CONFIRMED, amount_cents=amount,
        asaas_payment_id=kw.pop("asaas_payment_id", f"pay_{person.pk.hex[:6]}"), paid_at=timezone.now(), **kw,
    )


@pytest.fixture
def refunds(monkeypatch):
    state = {"refunded": [], "fail": None}

    def refund_payment(payment_id):
        if state["fail"]:
            raise AsaasError(state["fail"], "nope")
        state["refunded"].append(payment_id)

    monkeypatch.setattr("apps.payments.views.refund_payment", refund_payment)
    return state


# ── Refunds ──────────────────────────────────────────────────────────────────


def test_organizers_refund_through_asaas(api_client, jovens_leader, regular_person, event, paid_config, refunds):
    reg = paid(event, regular_person)
    res = as_(api_client, jovens_leader).post(f"{URL}/registrations/{reg.id}/refund/", {"note": "Doente"})
    assert res.status_code == 200, res.data
    assert res.data["payment_status"] == "refunded" and res.data["refund_note"] == "Doente"
    assert refunds["refunded"] == [reg.asaas_payment_id]
    reg.refresh_from_db()
    assert reg.refunded_by == jovens_leader and reg.refunded_at
    note = Notification.objects.get(recipient=regular_person, title__startswith="Inscrição reembolsada")
    assert "150,00" in note.body


def test_only_organizers_refund(api_client, regular_person, campus, event, paid_config, refunds):
    reg = paid(event, regular_person)
    assert as_(api_client, regular_person).post(f"{URL}/registrations/{reg.id}/refund/").status_code == 403
    other = make_person(email="x@ilan.church", campus=campus)
    assert as_(api_client, other).post(f"{URL}/registrations/{reg.id}/refund/").status_code == 403
    assert refunds["refunded"] == []


def test_boleto_needs_a_manual_refund(api_client, jovens_leader, regular_person, event, paid_config, refunds):
    reg = paid(event, regular_person)
    refunds["fail"] = 400
    client = as_(api_client, jovens_leader)
    res = client.post(f"{URL}/registrations/{reg.id}/refund/", {})
    assert res.status_code == 400 and res.data["code"] == "manual_refund_required"
    res = client.post(f"{URL}/registrations/{reg.id}/refund/", {"manual": True, "note": "Pix"}, format="json")
    assert res.status_code == 200 and res.data["refund_note"] == "Devolvido por fora — Pix"


def test_asaas_down_keeps_the_registration(api_client, jovens_leader, regular_person, event, paid_config, refunds):
    reg = paid(event, regular_person)
    refunds["fail"] = 502
    res = as_(api_client, jovens_leader).post(f"{URL}/registrations/{reg.id}/refund/")
    assert res.status_code == 503
    reg.refresh_from_db()
    assert reg.payment_status == PaymentStatus.CONFIRMED


def test_unpaid_is_cancelled_not_refunded(api_client, jovens_leader, regular_person, event, free_config, refunds):
    reg = EventRegistration.objects.create(event=event, person=regular_person, payment_status="confirmed")
    client = as_(api_client, jovens_leader)
    assert client.post(f"{URL}/registrations/{reg.id}/refund/").status_code == 400
    # The organizers cancel it, and the person hears about it.
    res = client.post(f"{URL}/registrations/{reg.id}/cancel/")
    assert res.status_code == 200 and res.data["payment_status"] == "cancelled"
    assert Notification.objects.filter(recipient=regular_person, title__startswith="Inscrição cancelada").exists()


def test_organizers_cannot_cancel_a_paid_one(api_client, jovens_leader, regular_person, event, paid_config):
    reg = paid(event, regular_person)
    res = as_(api_client, jovens_leader).post(f"{URL}/registrations/{reg.id}/cancel/")
    assert res.status_code == 400 and "Reembolsar" in str(res.data)


# ── Expenses ─────────────────────────────────────────────────────────────────


def test_expenses_are_for_the_organizers(api_client, jovens_leader, regular_person, event):
    client = as_(api_client, jovens_leader)
    body = {"event_id": str(event.id), "description": "Lanche", "category": "food", "amount_cents": 32000,
            "spent_on": "2026-11-10"}
    res = client.post(f"{URL}/expenses/", body, format="json")
    assert res.status_code == 201, res.data
    assert res.data["category_label"] == "Alimentação" and res.data["created_by"]["name"] == jovens_leader.name
    assert len(client.get(f"{URL}/expenses/", {"event": str(event.id)}).data) == 1
    assert client.post(f"{URL}/expenses/", {**body, "amount_cents": 0}, format="json").status_code == 400

    outsider = as_(api_client, regular_person)
    assert outsider.post(f"{URL}/expenses/", body, format="json").status_code == 403
    assert outsider.get(f"{URL}/expenses/", {"event": str(event.id)}).status_code == 403
    assert outsider.delete(f"{URL}/expenses/{res.data['id']}/").status_code in (403, 404)


# ── The report ───────────────────────────────────────────────────────────────


@pytest.fixture
def happened(event, paid_config, regular_person, campus):
    """A past event: 3 paid (one with a companion), one refunded, check-ins, an expense."""
    event.starts_at = timezone.now() - timedelta(days=1)
    event.save()
    ana = make_person(email="ana@ilan.church", name="Ana", campus=campus)
    bia = make_person(email="bia@ilan.church", name="Bia", campus=campus)
    caio = make_person(email="caio@ilan.church", name="Caio", campus=campus)
    r1 = paid(event, regular_person, amount=30000, seats=2, checked_in_at=timezone.now())
    EventRegistrationGuest.objects.create(registration=r1, name="Duda", checked_in_at=timezone.now())
    paid(event, ana)
    EventRegistration.objects.filter(pk=paid(event, bia).pk).update(payment_status=PaymentStatus.REFUNDED)
    EventRegistration.objects.create(event=event, person=caio, payment_status=PaymentStatus.PENDING, amount_cents=15000)
    EventExpense.objects.create(event=event, description="Som", category="audiovisual", amount_cents=10000,
                                spent_on=date.today())
    return event


def test_report(api_client, jovens_leader, happened):
    res = as_(api_client, jovens_leader).get(f"{URL}/reports/{happened.id}/")
    assert res.status_code == 200, res.data
    att, fin = res.data["attendance"], res.data["finance"]
    assert att["registrations"] == 2 and att["confirmed_seats"] == 3 and att["pending"] == 1
    assert att["checked_in"] == 2 and att["present"] == 2 and att["no_show"] == 1 and att["attendance_rate"] == 67
    assert fin["gross_cents"] == 60000 and fin["refunded_cents"] == 15000 and fin["net_cents"] == 45000
    assert fin["expenses_cents"] == 10000 and fin["balance_cents"] == 35000
    assert fin["expenses_by_category"] == [
        {"category": "audiovisual", "label": "Som, luz e imagem", "amount_cents": 10000}
    ]
    assert res.data["ticket_types"] == [
        {"id": None, "name": "Ingresso", "registrations": 2, "seats": 3, "revenue_cents": 45000}
    ]
    assert sum(d["count"] for d in res.data["registrations_by_day"]) == 4


def test_report_is_for_the_organizers(api_client, regular_person, happened):
    assert as_(api_client, regular_person).get(f"{URL}/reports/{happened.id}/").status_code == 403


def test_headcount_without_registration(api_client, manager, campus):
    culto = Event.objects.create(title="Culto", campus=campus, starts_at=timezone.now() - timedelta(days=2))
    client = as_(api_client, manager)
    assert client.patch(f"/api/production-events/{culto.id}/", {"headcount": 180}, format="json").status_code == 200
    att = client.get(f"{URL}/reports/{culto.id}/").data["attendance"]
    assert att["present"] == 180 and att["has_registration"] is False and att["attendance_rate"] is None


def test_csv_exports(api_client, jovens_leader, happened):
    client = as_(api_client, jovens_leader)
    res = client.get(f"{URL}/reports/{happened.id}/csv/")
    assert res.status_code == 200 and res["Content-Type"].startswith("text/csv")
    assert "inscritos.csv" in res["Content-Disposition"]
    text = res.content.decode("utf-8")
    assert text.startswith("﻿Nome;E-mail")
    assert "Duda;;;João" in text and "300,00" in text

    text = client.get(f"{URL}/reports/{happened.id}/csv/", {"kind": "finance"}).content.decode("utf-8")
    assert "Reembolso;Inscrição;Bia;-150,00" in text
    assert "Despesa;Som, luz e imagem;Som;-100,00" in text
    assert ";Total;;Saldo;350,00" in text
    assert client.get(f"{URL}/reports/{happened.id}/csv/", {"kind": "x"}).status_code == 400


# ── Comparison ───────────────────────────────────────────────────────────────


def test_compare_lists_only_organized_events(api_client, jovens_leader, manager, campus, happened):
    other = Event.objects.create(title="Culto", campus=campus, starts_at=timezone.now() - timedelta(days=8),
                                 headcount=120)
    future = Event.objects.create(title="Futuro", campus=campus, starts_at=timezone.now() + timedelta(days=8))

    rows = as_(api_client, manager).get(f"{URL}/reports/").data["results"]
    titles = [r["event"]["title"] for r in rows]
    assert titles[:2] == [happened.title, "Culto"] and "Futuro" not in titles
    assert rows[0]["balance_cents"] == 35000 and rows[1]["present"] == 120

    # The jovens leader organizes only the jovens event.
    rows = as_(api_client, jovens_leader).get(f"{URL}/reports/").data["results"]
    assert [r["event"]["title"] for r in rows] == [happened.title]

    rows = as_(api_client, manager).get(f"{URL}/reports/", {"events": f"{other.id},{future.id}"}).data["results"]
    assert {r["event"]["title"] for r in rows} == {"Culto", "Futuro"}
    assert as_(api_client, manager).get(f"{URL}/reports/", {"events": "nope"}).status_code == 400


def test_compare_a_series(api_client, manager, campus):
    from apps.production.models import EventSeries

    series = EventSeries.objects.create(title="Culto", campus=campus, weekday=6, start_time="19:00",
                                        starts_on=date.today() - timedelta(days=30))
    for weeks in (1, 2):
        Event.objects.create(title="Culto", campus=campus, series=series,
                             starts_at=timezone.now() - timedelta(weeks=weeks))
    Event.objects.create(title="Avulso", campus=campus, starts_at=timezone.now() - timedelta(days=1))
    rows = as_(api_client, manager).get(f"{URL}/reports/", {"series": str(series.id)}).data["results"]
    assert len(rows) == 2 and all(r["event"]["series"] == str(series.id) for r in rows)


def test_no_ticket_config_still_reports(api_client, manager, campus):
    culto = Event.objects.create(title="Culto", campus=campus, starts_at=timezone.now() - timedelta(days=2))
    att = as_(api_client, manager).get(f"{URL}/reports/{culto.id}/").data["attendance"]
    assert att["present"] is None and att["registrations"] == 0
    assert not EventTicketConfig.objects.filter(event=culto).exists()
