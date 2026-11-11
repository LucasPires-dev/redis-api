from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import LeadershipRole
from apps.courses.choices import TeacherRole
from apps.courses.models import (
    CampusCoursesLeadership,
    ClassFeeItem,
    Course,
    CourseClass,
    CourseClassTeacher,
)
from tests.conftest import make_person

WEBHOOK_TOKEN = "test-webhook-token"
TODAY = timezone.localdate()


@pytest.fixture(autouse=True)
def asaas_webhook_token(settings):
    settings.ASAAS_WEBHOOK_TOKEN = WEBHOOK_TOKEN


@pytest.fixture
def courses_leader(campus):
    person = make_person(email="cursos.arena@ilan.church", name="Cursos Arena", campus=campus)
    CampusCoursesLeadership.objects.create(campus=campus, person=person, role=LeadershipRole.LEADER)
    return person


@pytest.fixture
def course(church, campus):
    return Course.objects.create(church=church, campus=campus, name="Escola de Líderes")


@pytest.fixture
def paid_class(course, campus):
    """Starts in 10 days; R$ 30 handout (required) + optional R$ 35 t-shirt in sizes."""
    course_class = CourseClass.objects.create(
        course=course,
        campus=campus,
        name="Turma A",
        start_date=TODAY + timedelta(days=10),
        end_date=TODAY + timedelta(days=90),
        self_enrollment=True,
        max_capacity=20,
        reservation_days=3,
        max_installments=3,
    )
    ClassFeeItem.objects.create(course_class=course_class, name="Apostila", price_cents=3000, required=True, order=0)
    ClassFeeItem.objects.create(
        course_class=course_class, name="Camiseta", price_cents=3500, required=False, options=["P", "M", "G"], order=1
    )
    return course_class


@pytest.fixture
def free_class(course, campus):
    return CourseClass.objects.create(
        course=course, campus=campus, name="Turma Livre", self_enrollment=True, end_date=TODAY + timedelta(days=30)
    )


@pytest.fixture
def teacher(paid_class, campus):
    person = make_person(email="prof@ilan.church", name="Professor", campus=campus)
    CourseClassTeacher.objects.create(course_class=paid_class, person=person, role=TeacherRole.TEACHER)
    return person


@pytest.fixture
def student(campus):
    return make_person(email="aluno@ilan.church", name="Aluno", campus=campus)


def item(course_class, name):
    return course_class.fee_items.get(name=name)


@pytest.fixture
def fake_asaas(monkeypatch):
    """Replaces every Asaas call made by courses billing and the webhook."""
    from apps.payments.asaas import AsaasError

    state = {
        "status": "RECEIVED",
        "charge_fails": False,
        "cancel_fails": False,
        "refund_fails": False,
        "charges": [],
        "cancelled": [],
        "cancelled_installments": [],
        "refunded": [],
    }

    def find_or_create_customer(email, name):
        return "cus_1"

    def create_charge(customer_id, amount_cents, description, external_reference, *, due_days=3, installments=1):
        if state["charge_fails"]:
            raise AsaasError(500, "boom")
        number = len(state["charges"]) + 1
        state["charges"].append(
            {
                "amount_cents": amount_cents,
                "external_reference": external_reference,
                "due_days": due_days,
                "installments": installments,
            }
        )
        charge = {"id": f"pay_{number}", "invoiceUrl": f"https://asaas.test/i/pay_{number}"}
        if installments > 1:
            charge["installment"] = f"ins_{number}"
        return charge

    def cancel_charge(payment_id):
        if state["cancel_fails"]:
            raise AsaasError(0, "timeout")
        state["cancelled"].append(payment_id)

    def cancel_installment(installment_id):
        if state["cancel_fails"]:
            raise AsaasError(0, "timeout")
        state["cancelled_installments"].append(installment_id)

    def refund_payment(payment_id):
        if state["refund_fails"]:
            raise AsaasError(400, "no")
        state["refunded"].append(payment_id)

    def get_payment_status(payment_id):
        return state["status"]

    for name, fn in {
        "find_or_create_customer": find_or_create_customer,
        "create_charge": create_charge,
        "cancel_charge": cancel_charge,
        "cancel_installment": cancel_installment,
        "refund_payment": refund_payment,
    }.items():
        monkeypatch.setattr(f"apps.courses.billing.{name}", fn)
    monkeypatch.setattr("apps.payments.webhooks.get_payment_status", get_payment_status)
    return state
