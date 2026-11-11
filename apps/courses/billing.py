"""Paid enrollments: pricing, Asaas charges, payments, scholarships, refunds.

Money flow of an enrollment:

- enroll(): the person picks the class's items (required ones always come);
  gross = sum of their prices, frozen on the enrollment. Free classes are
  done here. Paid ones get an Asaas charge (single or in installments) and, for
  a self-enrollment, a seat reservation that expires after the class's
  `reservation_days` if nothing is paid.
- Every amount received is a row in EnrollmentPayment (one per paid Asaas
  charge — each installment — or per payment recorded by hand). The status
  follows from the ledger: nothing → pending, part → partial, all → paid.
- A scholarship lowers what is owed (all of it → "waived").
- Cancelling refunds what was paid (Asaas refund, or "returned in person" for
  hand-recorded payments) when the class's refund rule allows it.

Asaas charges are referenced as "enrollment:<uuid>" so the shared webhook can
tell them from event registrations (see apps.payments.webhooks).
"""

import logging
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from rest_framework import serializers

from apps.payments.asaas import (
    AsaasError,
    cancel_charge,
    cancel_installment,
    create_charge,
    find_or_create_customer,
    refund_payment,
)

from .choices import (
    CLOSED_PAYMENT_STATUSES,
    SEAT_HOLDING_PAYMENT_STATUSES,
    EnrollmentPaymentStatus as PS,
    EnrollmentStatus,
    PaymentMethod,
)
from .models import Enrollment, EnrollmentItem, EnrollmentPayment

logger = logging.getLogger(__name__)

REFERENCE_PREFIX = "enrollment:"
# Asaas doesn't accept charges (or installments) below R$ 5,00.
MIN_CHARGE_CENTS = 500


class ProviderUnavailable(Exception):
    """Asaas couldn't be reached or refused the operation; nothing was changed."""

    def __init__(self, message):
        self.message = message
        super().__init__(message)


# ── Seats ────────────────────────────────────────────────────────────────────


def active_enrollments(course_class):
    """Enrollments holding a seat: not dropped and not financially closed."""
    return course_class.enrollments.filter(payment_status__in=SEAT_HOLDING_PAYMENT_STATUSES).exclude(
        status=EnrollmentStatus.DROPPED
    )


def expire_overdue(queryset=None):
    """Releases the seats of self-enrollments whose reservation ran out with
    nothing paid. Called before seats are counted or listed, so no scheduler is
    needed. Returns how many expired."""
    now = timezone.now()
    queryset = queryset if queryset is not None else Enrollment.objects.all()
    overdue = queryset.filter(payment_status=PS.PENDING, expires_at__lt=now, payments__isnull=True)
    expired = 0
    for enrollment in overdue:
        try:
            _cancel_open_charge(enrollment)
        except AsaasError:
            # The link may still work for a while; the webhook would then record
            # the payment on an expired enrollment (logged for a manual refund).
            logger.warning("Couldn't cancel the charge of expired enrollment %s", enrollment.pk)
        enrollment.payment_status = PS.EXPIRED
        enrollment.status = EnrollmentStatus.DROPPED  # off the roster too
        enrollment.save(update_fields=["payment_status", "status", "asaas_payment_id",
                                       "asaas_installment_id", "payment_url", "updated_at"])
        expired += 1
    return expired


def seats_left(course_class, exclude=None):
    if course_class.max_capacity is None:
        return None
    expire_overdue(course_class.enrollments.all())
    taken = active_enrollments(course_class)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    return max(course_class.max_capacity - taken.count(), 0)


# ── Pricing ──────────────────────────────────────────────────────────────────


def resolve_items(course_class, choices):
    """The items an enrollment pays for: every required item plus the chosen
    optional ones, each with its picked option. `choices` is a list of
    {"fee_item_id", "option"}. Returns [(ClassFeeItem, option)]."""
    items = {str(item.id): item for item in course_class.fee_items.all()}
    picked = {}
    for choice in choices:
        item = items.get(str(choice.get("fee_item_id")))
        if item is None:
            raise serializers.ValidationError({"items": "Item inválido para esta turma."})
        if item.id in picked:
            raise serializers.ValidationError({"items": f"O item {item.name} foi escolhido duas vezes."})
        picked[item.id] = (item, (choice.get("option") or "").strip())

    for item in items.values():
        if item.required and item.id not in picked:
            picked[item.id] = (item, "")

    selection = sorted(picked.values(), key=lambda pair: (pair[0].order, pair[0].name))
    for item, option in selection:
        if item.options and option not in item.options:
            raise serializers.ValidationError(
                {"items": f"Escolha uma opção para {item.name}: {', '.join(item.options)}."}
            )
        if not item.options and option:
            raise serializers.ValidationError({"items": f"{item.name} não tem opções."})
    return selection


def check_installments(course_class, amount_cents, installments):
    if installments < 1 or installments > course_class.max_installments:
        raise serializers.ValidationError(
            {"installments": f"Esta turma permite pagar em até {course_class.max_installments}x."}
        )
    if installments > 1 and amount_cents // installments < MIN_CHARGE_CENTS:
        raise serializers.ValidationError(
            {"installments": "Cada parcela precisa ser de pelo menos R$ 5,00."}
        )


def max_installments_for(course_class, amount_cents):
    """The most installments allowed for this amount (each at least R$ 5,00)."""
    return max(1, min(course_class.max_installments, amount_cents // MIN_CHARGE_CENTS))


# ── Charges ──────────────────────────────────────────────────────────────────


def _cancel_open_charge(enrollment):
    """Kills the enrollment's open Asaas charge, if any (raises AsaasError).
    Clears the fields in memory; the caller saves."""
    if enrollment.asaas_installment_id:
        cancel_installment(enrollment.asaas_installment_id)
    elif enrollment.asaas_payment_id:
        cancel_charge(enrollment.asaas_payment_id)
    enrollment.asaas_payment_id = ""
    enrollment.asaas_installment_id = ""
    enrollment.payment_url = ""


def _open_charge(enrollment, installments):
    """Creates an Asaas charge for what is still owed (raises AsaasError)."""
    balance = enrollment.amount_cents - enrollment.paid_cents
    course_class = enrollment.course_class
    customer_id = find_or_create_customer(email=enrollment.person.email, name=enrollment.person.name)
    charge = create_charge(
        customer_id=customer_id,
        amount_cents=balance,
        description=f"Matrícula: {course_class.course.name} — {course_class.name}",
        external_reference=f"{REFERENCE_PREFIX}{enrollment.pk}",
        due_days=course_class.reservation_days,
        installments=installments,
    )
    enrollment.asaas_payment_id = charge["id"]
    enrollment.asaas_installment_id = charge.get("installment") or ""
    enrollment.payment_url = charge.get("invoiceUrl", "")
    enrollment.installments = installments
    enrollment.has_overdue_installment = False


def refresh_status(enrollment, save=True):
    """Sets the payment status from what is owed and the ledger (only while the
    enrollment is open: closed ones stay closed)."""
    if enrollment.payment_status in CLOSED_PAYMENT_STATUSES:
        return
    paid = enrollment.paid_cents
    if enrollment.amount_cents == 0:
        enrollment.payment_status = PS.WAIVED if enrollment.discount_cents else PS.FREE
    elif paid >= enrollment.amount_cents:
        enrollment.payment_status = PS.PAID
        enrollment.paid_at = enrollment.paid_at or timezone.now()
        enrollment.has_overdue_installment = False
    elif paid > 0:
        enrollment.payment_status = PS.PARTIAL
    else:
        enrollment.payment_status = PS.PENDING
    if enrollment.payment_status != PS.PENDING:
        enrollment.expires_at = None
    if save:
        enrollment.save()


# ── Enrolling ────────────────────────────────────────────────────────────────


def enroll(course_class, person, choices, installments=1, *, self_service, send_charge=True):
    """Enrolls a person (by themselves or by a coordinator). Raises
    ValidationError for a business rule, ProviderUnavailable when the charge
    couldn't be created (then nothing is kept)."""
    already = serializers.ValidationError({"detail": f"{person.name} já está matriculado(a) nesta turma."})
    if active_enrollments(course_class).filter(person=person).exists():
        raise already

    selection = resolve_items(course_class, choices)
    gross = sum(item.price_cents for item, _ in selection)
    if 0 < gross < MIN_CHARGE_CENTS:
        raise serializers.ValidationError({"items": "O total precisa ser de pelo menos R$ 5,00."})
    if gross:
        check_installments(course_class, gross, installments)

    left = seats_left(course_class)
    if left is not None and left <= 0:
        raise serializers.ValidationError({"detail": "Vagas esgotadas."})

    try:
        with transaction.atomic():
            enrollment = Enrollment.objects.create(
                course_class=course_class,
                person=person,
                status=EnrollmentStatus.ACTIVE,
                gross_cents=gross,
                amount_cents=gross,
                installments=installments if gross else 1,
                payment_status=PS.PENDING if gross else PS.FREE,
                expires_at=(
                    timezone.now() + timedelta(days=course_class.reservation_days)
                    if gross and self_service
                    else None
                ),
            )
            EnrollmentItem.objects.bulk_create(
                EnrollmentItem(
                    enrollment=enrollment,
                    fee_item=item,
                    name=item.name,
                    price_cents=item.price_cents,
                    option=option,
                )
                for item, option in selection
            )
    except IntegrityError:
        raise already  # double click: the DB constraint wins

    if gross and send_charge:
        try:
            _open_charge(enrollment, installments)
            enrollment.save()
        except AsaasError:
            logger.exception("Couldn't create the charge for enrollment %s", enrollment.pk)
            enrollment.delete()  # a seat nobody can pay for would only block the person
            raise ProviderUnavailable("Não foi possível gerar a cobrança agora. Tente novamente em alguns minutos.")
    return enrollment


def generate_charge(enrollment, installments=None):
    """(Re)creates the payment link for what is still owed."""
    if enrollment.payment_status not in (PS.PENDING, PS.PARTIAL):
        raise serializers.ValidationError({"detail": "Esta matrícula não tem valor em aberto."})
    balance = enrollment.amount_cents - enrollment.paid_cents
    installments = installments or (enrollment.installments if enrollment.paid_cents == 0 else 1)
    check_installments(enrollment.course_class, balance, installments)
    try:
        _cancel_open_charge(enrollment)
        _open_charge(enrollment, installments)
    except AsaasError:
        raise ProviderUnavailable("Não foi possível gerar a cobrança agora. Tente novamente em alguns minutos.")
    enrollment.save()
    return enrollment


# ── Payments ─────────────────────────────────────────────────────────────────


def record_payment(enrollment, amount_cents, method, *, asaas_payment_id="", note="", by=None):
    """Adds money received to the ledger and updates the status. Returns None
    when that Asaas payment was already recorded (repeated webhook)."""
    try:
        with transaction.atomic():
            payment = EnrollmentPayment.objects.create(
                enrollment=enrollment,
                method=method,
                amount_cents=amount_cents,
                asaas_payment_id=asaas_payment_id,
                note=note,
                recorded_by=by,
                paid_at=timezone.now(),
            )
    except IntegrityError:
        return None
    _forget_payments(enrollment)
    refresh_status(enrollment)
    return payment


def _forget_payments(enrollment):
    """Drops a prefetched `payments` list, so totals see the ledger as it is now."""
    getattr(enrollment, "_prefetched_objects_cache", {}).pop("payments", None)


def record_manual_payment(enrollment, amount_cents, note, by):
    """A payment received in person (cash, Pix to the church). The open Asaas
    link is cancelled first, so the person can't pay the same amount twice; a
    new one can be generated for any remaining balance."""
    if enrollment.payment_status not in (PS.PENDING, PS.PARTIAL):
        raise serializers.ValidationError({"detail": "Esta matrícula não tem valor em aberto."})
    balance = enrollment.amount_cents - enrollment.paid_cents
    if amount_cents < 1 or amount_cents > balance:
        raise serializers.ValidationError(
            {"amount_cents": f"Informe um valor entre R$ 0,01 e R$ {balance / 100:.2f}.".replace(".", ",")}
        )
    try:
        _cancel_open_charge(enrollment)
    except AsaasError:
        raise ProviderUnavailable(
            "Não foi possível cancelar o link de pagamento online agora. Tente novamente em alguns minutos."
        )
    enrollment.save()
    return record_payment(enrollment, amount_cents, PaymentMethod.MANUAL, note=note, by=by)


def grant_scholarship(enrollment, discount_cents, reason, by):
    """Lowers what the person owes (all of it: full scholarship). Only before
    any payment, so the charge can simply be replaced."""
    if enrollment.payment_status not in (PS.PENDING, PS.WAIVED, PS.FREE) or enrollment.paid_cents:
        raise serializers.ValidationError(
            {"detail": "A bolsa só pode ser concedida antes de qualquer pagamento."}
        )
    if not enrollment.gross_cents:
        raise serializers.ValidationError({"detail": "Esta matrícula é gratuita."})
    if discount_cents > enrollment.gross_cents:
        raise serializers.ValidationError({"discount_cents": "A bolsa não pode passar do valor da matrícula."})
    reason = (reason or "").strip()
    if discount_cents and not reason:
        raise serializers.ValidationError({"reason": "Informe o motivo da bolsa."})
    if 0 < enrollment.gross_cents - discount_cents < MIN_CHARGE_CENTS:
        raise serializers.ValidationError(
            {"discount_cents": "O valor restante precisa ser de pelo menos R$ 5,00 (ou bolsa integral)."}
        )

    had_charge = bool(enrollment.asaas_payment_id)
    try:
        _cancel_open_charge(enrollment)
    except AsaasError:
        raise ProviderUnavailable("Não foi possível atualizar a cobrança agora. Tente novamente em alguns minutos.")

    enrollment.discount_cents = discount_cents
    enrollment.amount_cents = enrollment.gross_cents - discount_cents
    enrollment.scholarship_reason = reason if discount_cents else ""
    enrollment.scholarship_granted_by = by if discount_cents else None
    enrollment.payment_status = PS.PENDING  # recomputed below
    refresh_status(enrollment, save=False)
    if enrollment.payment_status == PS.PENDING:
        enrollment.expires_at = None  # a coordinator handled it: no rush
        if had_charge:
            installments = min(enrollment.installments, max_installments_for(
                enrollment.course_class, enrollment.amount_cents))
            try:
                _open_charge(enrollment, installments)
            except AsaasError:
                # The old link is gone; a new one can be generated later.
                logger.exception("Couldn't recreate the charge of enrollment %s", enrollment.pk)
    enrollment.save()
    return enrollment


# ── Cancelling ───────────────────────────────────────────────────────────────


def refund_allowed(enrollment):
    """The class's rule: full refund only before the class starts."""
    course_class = enrollment.course_class
    if not course_class.refund_before_start:
        return False
    return course_class.start_date is None or timezone.localdate() < course_class.start_date


def cancel(enrollment, *, by_coordinator, force=False, note=""):
    """Cancels the enrollment and frees the seat. Money already paid is
    refunded (only by a coordinator, and after the class started only with
    `force`). Online payments are refunded in Asaas; hand-recorded ones are
    marked as returned in person."""
    if enrollment.payment_status in CLOSED_PAYMENT_STATUSES:
        return enrollment
    paid = enrollment.payments.filter(refunded_at__isnull=True)
    if paid.exists():
        if not by_coordinator:
            raise serializers.ValidationError(
                {"detail": "Matrícula já paga. Procure a coordenação do curso para cancelar e pedir o reembolso."}
            )
        if not refund_allowed(enrollment) and not force:
            raise serializers.ValidationError(
                {"detail": "Pela regra da turma, não há reembolso depois do início das aulas.", "code": "refund_rule"}
            )

    try:
        _cancel_open_charge(enrollment)
    except AsaasError:
        raise ProviderUnavailable("Não foi possível cancelar a cobrança agora. Tente novamente em alguns minutos.")

    now = timezone.now()
    for payment in paid:
        if payment.method == PaymentMethod.ASAAS:
            try:
                refund_payment(payment.asaas_payment_id)
            except AsaasError:
                # Payments refunded so far stay marked; retrying continues from here.
                enrollment.save()
                raise ProviderUnavailable("Não foi possível fazer o reembolso agora. Tente novamente em alguns minutos.")
        payment.refunded_at = now
        payment.refund_note = note
        payment.save(update_fields=["refunded_at", "refund_note"])

    refunded = enrollment.payments.filter(refunded_at__isnull=False).aggregate(t=Sum("amount_cents"))["t"] or 0
    enrollment.refunded_cents = refunded
    enrollment.payment_status = PS.REFUNDED if refunded else PS.CANCELLED
    enrollment.status = EnrollmentStatus.DROPPED
    enrollment.expires_at = None
    enrollment.save()
    _forget_payments(enrollment)
    return enrollment


# ── Asaas webhook ────────────────────────────────────────────────────────────


def find_for_asaas_payment(external_reference, payment):
    """The enrollment an Asaas payment belongs to, or None (unknown, or a payment
    that isn't this enrollment's current charge/installment plan/ledger)."""
    enrollment_id = external_reference[len(REFERENCE_PREFIX):]
    try:
        enrollment = Enrollment.objects.select_related("course_class").get(pk=enrollment_id)
    except (Enrollment.DoesNotExist, ValueError, DjangoValidationError):  # invalid UUID too
        return None
    payment_id = payment.get("id", "")
    installment_id = payment.get("installment") or ""
    belongs = (
        (payment_id and payment_id == enrollment.asaas_payment_id)
        or (installment_id and installment_id == enrollment.asaas_installment_id)
        or (payment_id and enrollment.payments.filter(asaas_payment_id=payment_id).exists())
    )
    return enrollment if belongs else None


def apply_asaas_paid(enrollment, payment):
    value = payment.get("value") or 0
    amount_cents = int(round(float(value) * 100))
    if enrollment.payment_status in CLOSED_PAYMENT_STATUSES:
        logger.error(
            "Asaas payment %s for closed enrollment %s (%s) — needs a manual refund",
            payment.get("id"),
            enrollment.pk,
            enrollment.payment_status,
        )
    record_payment(enrollment, amount_cents, PaymentMethod.ASAAS, asaas_payment_id=payment.get("id", ""))


def apply_asaas_overdue(enrollment, payment):
    if enrollment.payment_status == PS.PENDING and not enrollment.payments.exists():
        enrollment.payment_status = PS.EXPIRED
        enrollment.status = EnrollmentStatus.DROPPED
        enrollment.asaas_payment_id = enrollment.asaas_installment_id = enrollment.payment_url = ""
        enrollment.save()
    elif enrollment.payment_status == PS.PARTIAL:
        enrollment.has_overdue_installment = True
        enrollment.save(update_fields=["has_overdue_installment", "updated_at"])


def apply_asaas_refunded(enrollment, payment):
    updated = enrollment.payments.filter(
        asaas_payment_id=payment.get("id", ""), refunded_at__isnull=True
    ).update(refunded_at=timezone.now())
    if updated and not enrollment.payments.filter(refunded_at__isnull=True).exists():
        enrollment.refunded_cents = enrollment.payments.aggregate(t=Sum("amount_cents"))["t"] or 0
        enrollment.payment_status = PS.REFUNDED
        enrollment.status = EnrollmentStatus.DROPPED
        enrollment.save()


# ── Finance ──────────────────────────────────────────────────────────────────


def finance_summary(course_class):
    expire_overdue(course_class.enrollments.all())
    enrollments = list(
        course_class.enrollments.select_related("person").prefetch_related("payments", "items")
    )
    holding = [
        e for e in enrollments
        if e.payment_status in SEAT_HOLDING_PAYMENT_STATUSES and e.status != EnrollmentStatus.DROPPED
    ]
    payments = EnrollmentPayment.objects.filter(enrollment__course_class=course_class)
    received = payments.aggregate(t=Sum("amount_cents"))["t"] or 0
    refunded = payments.filter(refunded_at__isnull=False).aggregate(t=Sum("amount_cents"))["t"] or 0
    online = payments.filter(method=PaymentMethod.ASAAS, refunded_at__isnull=True).aggregate(
        t=Sum("amount_cents"))["t"] or 0
    expenses = course_class.expenses.aggregate(t=Sum("amount_cents"))["t"] or 0
    net_received = received - refunded

    by_status = {}
    for e in enrollments:
        by_status[e.payment_status] = by_status.get(e.payment_status, 0) + 1

    # How many of each item (and option) the active enrollments took — what to buy.
    items = {}
    for e in holding:
        for item in e.items.all():
            entry = items.setdefault(item.name, {"name": item.name, "total": 0, "options": {}})
            entry["total"] += 1
            if item.option:
                entry["options"][item.option] = entry["options"].get(item.option, 0) + 1

    return {
        "expected_cents": sum(e.amount_cents for e in holding),
        "received_cents": net_received,
        "received_online_cents": online,
        "received_manual_cents": net_received - online,
        "outstanding_cents": sum(e.balance_cents for e in holding),
        "scholarship_cents": sum(e.discount_cents for e in holding),
        "refunded_cents": refunded,
        "expenses_cents": expenses,
        "balance_cents": net_received - expenses,
        "enrolled": len(holding),
        "capacity": course_class.max_capacity,
        "by_status": by_status,
        "overdue_installments": sum(1 for e in holding if e.has_overdue_installment),
        "materials_delivered": sum(1 for e in holding if e.material_delivered_at),
        "items": sorted(items.values(), key=lambda i: i["name"]),
    }


def open_classes_filter(today=None):
    """Classes a member can enroll in by themselves right now."""
    today = today or timezone.localdate()
    return (
        Q(self_enrollment=True)
        & (Q(enrollment_deadline__isnull=True) | Q(enrollment_deadline__gte=today))
        & (Q(end_date__isnull=True) | Q(end_date__gte=today))
    )
