"""Event reports: attendance, money in and out, CSV exports and the numbers
that line events up side by side.

Definitions used everywhere:
- confirmed seats: the person plus companions of each confirmed registration;
- present: check-ins (people and companions) or, for an event without
  registration, the headcount counted in the room;
- gross revenue: what was paid (confirmed + refunded); refunds: what went back;
  net revenue: gross - refunds; balance: net - expenses.
"""

import csv
import io
from collections import Counter

from django.db.models import Count, Sum
from django.utils import timezone

from .models import (
    EventExpense,
    EventRegistration,
    EventRegistrationGuest,
    EventTicketConfig,
    ExpenseCategory,
    PaymentStatus,
)

PAID_STATUSES = [PaymentStatus.CONFIRMED, PaymentStatus.REFUNDED]


def _cents(qs):
    return qs.aggregate(total=Sum("amount_cents"))["total"] or 0


def _rate(part, whole):
    return round(part * 100 / whole) if whole else None


def attendance(event):
    regs = EventRegistration.objects.filter(event=event)
    confirmed = regs.filter(payment_status=PaymentStatus.CONFIRMED)
    seats = confirmed.aggregate(total=Sum("seats"))["total"] or 0
    checked_in = (
        confirmed.filter(checked_in_at__isnull=False).count()
        + EventRegistrationGuest.objects.filter(
            registration__in=confirmed, checked_in_at__isnull=False
        ).count()
    )
    has_registration = EventTicketConfig.objects.filter(event=event).exists()
    # Check-ins count when there are any; otherwise the headcount, if taken.
    if has_registration and checked_in:
        present = checked_in
    elif event.headcount is not None:
        present = event.headcount
    else:
        present = checked_in if has_registration else None
    return {
        "has_registration": has_registration,
        "registrations": confirmed.count(),
        "confirmed_seats": seats,
        "pending": regs.filter(payment_status=PaymentStatus.PENDING).count(),
        "cancelled": regs.filter(payment_status=PaymentStatus.CANCELLED).count(),
        "checked_in": checked_in,
        "no_show": max(seats - checked_in, 0) if has_registration and event.starts_at <= timezone.now() else None,
        "headcount": event.headcount,
        "present": present,
        "attendance_rate": _rate(checked_in, seats) if has_registration else None,
    }


def finance(event):
    regs = EventRegistration.objects.filter(event=event)
    gross = _cents(regs.filter(payment_status__in=PAID_STATUSES))
    refunded = _cents(regs.filter(payment_status=PaymentStatus.REFUNDED))
    expenses = EventExpense.objects.filter(event=event)
    by_category = {
        row["category"]: row["total"]
        for row in expenses.values("category").annotate(total=Sum("amount_cents"))
    }
    expenses_total = sum(by_category.values())
    return {
        "gross_cents": gross,
        "refunded_cents": refunded,
        "refunds": regs.filter(payment_status=PaymentStatus.REFUNDED).count(),
        "net_cents": gross - refunded,
        "expenses_cents": expenses_total,
        "balance_cents": gross - refunded - expenses_total,
        "expenses_by_category": [
            {"category": value, "label": label, "amount_cents": by_category[value]}
            for value, label in ExpenseCategory.choices
            if by_category.get(value)
        ],
    }


def ticket_types(event):
    """Sold seats and revenue per ticket type (or the single ticket)."""
    rows = (
        EventRegistration.objects.filter(event=event, payment_status=PaymentStatus.CONFIRMED)
        .values("ticket_type_id", "ticket_type__name")
        .annotate(registrations=Count("id"), seats=Sum("seats"), revenue_cents=Sum("amount_cents"))
        .order_by("ticket_type__name")
    )
    return [
        {
            "id": row["ticket_type_id"],
            "name": row["ticket_type__name"] or "Ingresso",
            "registrations": row["registrations"],
            "seats": row["seats"] or 0,
            "revenue_cents": row["revenue_cents"] or 0,
        }
        for row in rows
    ]


def registrations_by_day(event):
    """Registrations made per day (not cancelled before paying), oldest first."""
    days = Counter(
        timezone.localdate(created)
        for created in EventRegistration.objects.filter(event=event)
        .exclude(payment_status=PaymentStatus.CANCELLED)
        .values_list("created_at", flat=True)
    )
    return [{"date": day.isoformat(), "count": days[day]} for day in sorted(days)]


def event_info(event):
    return {
        "id": str(event.id),
        "title": event.title,
        "starts_at": event.starts_at.isoformat(),
        "event_type": event.event_type,
        "campus": event.campus.name if event.campus_id else None,
        "series": str(event.series_id) if event.series_id else None,
    }


def summary(event):
    return {
        "event": event_info(event),
        "attendance": attendance(event),
        "finance": finance(event),
        "ticket_types": ticket_types(event),
        "registrations_by_day": registrations_by_day(event),
    }


def compact(event):
    """One line of the comparison: the numbers that matter side by side."""
    att = attendance(event)
    fin = finance(event)
    return {
        "event": event_info(event),
        "registrations": att["registrations"],
        "confirmed_seats": att["confirmed_seats"],
        "present": att["present"],
        "attendance_rate": att["attendance_rate"],
        "gross_cents": fin["gross_cents"],
        "refunded_cents": fin["refunded_cents"],
        "net_cents": fin["net_cents"],
        "expenses_cents": fin["expenses_cents"],
        "balance_cents": fin["balance_cents"],
    }


# ── CSV ──────────────────────────────────────────────────────────────────────
# ";" and decimal comma, with a BOM: what Excel in Portuguese opens right.


def _money(cents):
    return f"{cents / 100:.2f}".replace(".", ",")


def _when(dt):
    return timezone.localtime(dt).strftime("%d/%m/%Y %H:%M") if dt else ""


def _csv(header, rows):
    out = io.StringIO()
    out.write("﻿")
    writer = csv.writer(out, delimiter=";")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def attendees_csv(event):
    header = [
        "Nome", "E-mail", "Telefone", "Acompanhante de", "Ingresso", "Situação", "Valor (R$)",
        "Pago em", "Código", "Check-in", "Reembolsado em", "Observação do reembolso", "Inscrito em",
    ]
    rows = []
    registrations = (
        EventRegistration.objects.filter(event=event)
        .select_related("person", "ticket_type")
        .prefetch_related("guests")
        .order_by("person__name", "created_at")
    )
    for r in registrations:
        status = r.get_payment_status_display()
        ticket = r.ticket_type.name if r.ticket_type_id else ""
        rows.append([
            r.person.name, r.person.email, r.person.phone, "", ticket, status,
            _money(r.amount_cents), _when(r.paid_at), r.ticket_code, _when(r.checked_in_at),
            _when(r.refunded_at), r.refund_note, _when(r.created_at),
        ])
        for g in r.guests.all():
            rows.append([
                g.name, "", "", r.person.name, ticket, status, "", "", g.ticket_code, _when(g.checked_in_at),
                "", "", _when(r.created_at),
            ])
    return _csv(header, rows)


def finance_csv(event):
    header = ["Data", "Tipo", "Categoria", "Descrição", "Valor (R$)"]
    rows = []
    paid = (
        EventRegistration.objects.filter(event=event, payment_status__in=PAID_STATUSES, amount_cents__gt=0)
        .select_related("person")
        .order_by("paid_at", "created_at")
    )
    for r in paid:
        rows.append([_when(r.paid_at or r.created_at), "Receita", "Inscrição", r.person.name, _money(r.amount_cents)])
        if r.payment_status == PaymentStatus.REFUNDED:
            note = f" — {r.refund_note}" if r.refund_note else ""
            rows.append([
                _when(r.refunded_at or r.updated_at), "Reembolso", "Inscrição", f"{r.person.name}{note}",
                _money(-r.amount_cents),
            ])
    for e in EventExpense.objects.filter(event=event).order_by("spent_on", "created_at"):
        rows.append([
            e.spent_on.strftime("%d/%m/%Y"), "Despesa", e.get_category_display(), e.description,
            _money(-e.amount_cents),
        ])
    fin = finance(event)
    rows.append([])
    for label, key in (
        ("Receita bruta", "gross_cents"), ("Reembolsos", "refunded_cents"), ("Receita líquida", "net_cents"),
        ("Despesas", "expenses_cents"), ("Saldo", "balance_cents"),
    ):
        rows.append(["", "Total", "", label, _money(fin[key])])
    return _csv(header, rows)


def filename(event, kind):
    slug = "".join(c if c.isalnum() else "-" for c in event.title.lower()).strip("-")
    slug = "-".join(part for part in slug.split("-") if part)[:60] or "evento"
    return f"{slug}-{timezone.localtime(event.starts_at):%Y-%m-%d}-{kind}.csv"
