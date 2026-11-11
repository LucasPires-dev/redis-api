"""Paid course enrollments: class fees, self-enrollment, Asaas charges and
webhook, scholarships, payments in person, cancellation/refunds, finances."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.courses.choices import EnrollmentPaymentStatus as PS
from apps.courses.choices import EnrollmentStatus, PaymentMethod
from apps.courses.models import (
    ClassExpense,
    ClassFeeItem,
    CourseClass,
    Enrollment,
    EnrollmentPayment,
    Lesson,
)
from tests.courses.conftest import TODAY, WEBHOOK_TOKEN, item

pytestmark = pytest.mark.django_db


def as_user(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def self_enroll(api_client, course_class, items=(), installments=1):
    return api_client.post(
        f"/api/course-classes/{course_class.id}/enroll/",
        {"items": list(items), "installments": installments},
        format="json",
    )


def tshirt(course_class, size="M"):
    return {"fee_item_id": str(item(course_class, "Camiseta").id), "option": size}


def webhook(api_client, event, enrollment, payment_id, value, installment=None):
    api_client.force_authenticate(user=None)
    payment = {"id": payment_id, "externalReference": f"enrollment:{enrollment.id}", "value": value}
    if installment:
        payment["installment"] = installment
    return api_client.post(
        "/api/payments/webhooks/asaas/",
        {"event": event, "payment": payment},
        format="json",
        HTTP_ASAAS_ACCESS_TOKEN=WEBHOOK_TOKEN,
    )


# ── Class fee configuration ─────────────────────────────────────────────────


def test_coordinator_configures_fees_on_create(api_client, courses_leader, course, campus):
    response = as_user(api_client, courses_leader).post(
        "/api/course-classes/",
        {
            "course_id": str(course.id),
            "campus_id": str(campus.id),
            "name": "Turma Nova",
            "self_enrollment": True,
            "max_installments": 2,
            "fee_items": [
                {"name": "Apostila", "price_cents": 3000, "required": True},
                {"name": "Camiseta", "price_cents": 3500, "required": False, "options": ["P", " M ", ""]},
            ],
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["base_price_cents"] == 3000
    assert response.data["can_manage_finances"] is True
    shirt = next(i for i in response.data["fee_items"] if i["name"] == "Camiseta")
    assert shirt["options"] == ["P", "M"]


def test_required_items_below_asaas_minimum_are_rejected(api_client, courses_leader, course, campus):
    response = as_user(api_client, courses_leader).post(
        "/api/course-classes/",
        {
            "course_id": str(course.id),
            "campus_id": str(campus.id),
            "name": "Barata",
            "fee_items": [{"name": "Adesivo", "price_cents": 300, "required": True}],
        },
        format="json",
    )
    assert response.status_code == 400
    assert "fee_items" in response.data


def test_editing_items_keeps_existing_enrollment_prices(api_client, courses_leader, student, paid_class, fake_asaas):
    as_user(api_client, student)
    assert self_enroll(api_client, paid_class).status_code == 201

    handout = item(paid_class, "Apostila")
    response = as_user(api_client, courses_leader).patch(
        f"/api/course-classes/{paid_class.id}/",
        {"fee_items": [{"id": str(handout.id), "name": "Apostila", "price_cents": 4000, "required": True}]},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert [i["name"] for i in response.data["fee_items"]] == ["Apostila"]  # t-shirt removed
    enrollment = Enrollment.objects.get(person=student)
    assert enrollment.amount_cents == 3000
    assert [(i.name, i.price_cents) for i in enrollment.items.all()] == [("Apostila", 3000)]


def test_capacity_cannot_go_below_active_enrollments(api_client, courses_leader, student, paid_class, fake_asaas):
    as_user(api_client, student)
    self_enroll(api_client, paid_class)
    response = as_user(api_client, courses_leader).patch(
        f"/api/course-classes/{paid_class.id}/", {"max_capacity": 0}, format="json"
    )
    assert response.status_code == 400


def test_teacher_has_no_access_to_finances(api_client, teacher, paid_class):
    as_user(api_client, teacher)
    data = api_client.get(f"/api/course-classes/{paid_class.id}/").data
    assert data["can_manage"] is True
    assert data["can_manage_finances"] is False
    assert api_client.get(f"/api/course-classes/{paid_class.id}/finance/").status_code == 403
    assert api_client.get(f"/api/course-classes/{paid_class.id}/finance-export/").status_code == 403


# ── Open classes and self-enrollment ────────────────────────────────────────


def test_member_sees_only_open_classes(api_client, student, paid_class, course, campus):
    CourseClass.objects.create(course=course, campus=campus, name="Fechada", self_enrollment=False)
    CourseClass.objects.create(
        course=course, campus=campus, name="Prazo vencido", self_enrollment=True,
        enrollment_deadline=TODAY - timedelta(days=1),
    )
    data = as_user(api_client, student).get("/api/course-classes/open/").data
    assert [c["name"] for c in data] == ["Turma A"]
    assert data[0]["base_price_cents"] == 3000
    assert data[0]["seats_left"] == 20
    assert data[0]["my_enrollment"] is None


def test_self_enrollment_with_optional_item_creates_a_charge(api_client, student, paid_class, fake_asaas):
    response = self_enroll(as_user(api_client, student), paid_class, [tshirt(paid_class, "M")])
    assert response.status_code == 201, response.data
    assert response.data["payment_status"] == PS.PENDING
    assert response.data["amount_cents"] == 6500
    assert response.data["payment_url"] == "https://asaas.test/i/pay_1"
    assert {(i["name"], i["option"]) for i in response.data["items"]} == {("Apostila", ""), ("Camiseta", "M")}

    enrollment = Enrollment.objects.get(pk=response.data["id"])
    assert fake_asaas["charges"] == [
        {"amount_cents": 6500, "external_reference": f"enrollment:{enrollment.id}", "due_days": 3, "installments": 1}
    ]
    assert enrollment.expires_at is not None
    mine = api_client.get("/api/course-enrollments/?mine=1").data["results"]
    assert [e["id"] for e in mine] == [str(enrollment.id)]
    assert mine[0]["course_class"]["course_name"] == "Escola de Líderes"


@pytest.mark.parametrize("choice", [{"option": ""}, {"option": "XG"}])
def test_item_with_options_needs_a_valid_option(api_client, student, paid_class, fake_asaas, choice):
    response = self_enroll(
        as_user(api_client, student), paid_class, [{"fee_item_id": str(item(paid_class, "Camiseta").id), **choice}]
    )
    assert response.status_code == 400
    assert "items" in response.data


def test_installments_follow_the_class_limit(api_client, student, paid_class, fake_asaas):
    as_user(api_client, student)
    assert self_enroll(api_client, paid_class, installments=4).status_code == 400
    response = self_enroll(api_client, paid_class, [tshirt(paid_class)], installments=3)
    assert response.status_code == 201, response.data
    assert fake_asaas["charges"][0]["installments"] == 3
    assert Enrollment.objects.get(pk=response.data["id"]).asaas_installment_id == "ins_1"


def test_each_installment_must_be_at_least_five_reais(api_client, student, paid_class, fake_asaas):
    # R$ 30,00 in 3x is fine (R$ 10); a R$ 12,00 class in 3x isn't.
    handout = item(paid_class, "Apostila")
    handout.price_cents = 1200
    handout.save()
    response = self_enroll(as_user(api_client, student), paid_class, installments=3)
    assert response.status_code == 400
    assert "installments" in response.data


def test_free_class_needs_no_payment(api_client, student, free_class, fake_asaas):
    response = self_enroll(as_user(api_client, student), free_class)
    assert response.status_code == 201
    assert response.data["payment_status"] == PS.FREE
    assert fake_asaas["charges"] == []


def test_cannot_enroll_twice(api_client, student, paid_class, fake_asaas):
    as_user(api_client, student)
    assert self_enroll(api_client, paid_class).status_code == 201
    assert self_enroll(api_client, paid_class).status_code == 400


def test_full_class_rejects_and_expired_reservation_frees_the_seat(
    api_client, student, regular_person, paid_class, fake_asaas
):
    paid_class.max_capacity = 1
    paid_class.save()
    self_enroll(as_user(api_client, student), paid_class)
    assert self_enroll(as_user(api_client, regular_person), paid_class).status_code == 400

    Enrollment.objects.filter(person=student).update(expires_at=timezone.now() - timedelta(minutes=1))
    response = self_enroll(as_user(api_client, regular_person), paid_class)
    assert response.status_code == 201, response.data
    expired = Enrollment.objects.get(person=student)
    assert expired.payment_status == PS.EXPIRED
    assert expired.status == EnrollmentStatus.DROPPED
    assert "pay_1" in fake_asaas["cancelled"]


def test_charge_failure_leaves_no_enrollment(api_client, student, paid_class, fake_asaas):
    fake_asaas["charge_fails"] = True
    assert self_enroll(as_user(api_client, student), paid_class).status_code == 503
    assert not Enrollment.objects.exists()


def test_cannot_self_enroll_in_a_closed_class(api_client, student, paid_class, fake_asaas):
    paid_class.self_enrollment = False
    paid_class.save()
    assert self_enroll(as_user(api_client, student), paid_class).status_code == 404


# ── Asaas webhook ─────────────────────────────────────────────────────────────


@pytest.fixture
def pending(api_client, student, paid_class, fake_asaas):
    response = self_enroll(as_user(api_client, student), paid_class)
    return Enrollment.objects.get(pk=response.data["id"])


def test_payment_confirms_and_repeated_webhook_counts_once(api_client, pending, fake_asaas):
    assert webhook(api_client, "PAYMENT_CONFIRMED", pending, "pay_1", 30.0).status_code == 200
    assert webhook(api_client, "PAYMENT_RECEIVED", pending, "pay_1", 30.0).status_code == 200
    pending.refresh_from_db()
    assert pending.payment_status == PS.PAID
    assert pending.paid_cents == 3000
    assert pending.expires_at is None
    assert EnrollmentPayment.objects.count() == 1


def test_webhook_needs_asaas_to_confirm(api_client, pending, fake_asaas):
    fake_asaas["status"] = "PENDING"
    webhook(api_client, "PAYMENT_CONFIRMED", pending, "pay_1", 30.0)
    pending.refresh_from_db()
    assert pending.payment_status == PS.PENDING


def test_webhook_ignores_payments_of_other_charges(api_client, pending, fake_asaas):
    webhook(api_client, "PAYMENT_CONFIRMED", pending, "pay_999", 30.0)
    pending.refresh_from_db()
    assert pending.payment_status == PS.PENDING


def test_installments_go_partial_then_paid(api_client, student, paid_class, fake_asaas):
    response = self_enroll(as_user(api_client, student), paid_class, [tshirt(paid_class)], installments=2)
    enrollment = Enrollment.objects.get(pk=response.data["id"])
    webhook(api_client, "PAYMENT_RECEIVED", enrollment, "pay_1", 32.5, installment="ins_1")
    enrollment.refresh_from_db()
    assert enrollment.payment_status == PS.PARTIAL
    assert enrollment.balance_cents == 3250
    # The second installment is another Asaas payment of the same installment plan.
    webhook(api_client, "PAYMENT_RECEIVED", enrollment, "pay_1b", 32.5, installment="ins_1")
    enrollment.refresh_from_db()
    assert enrollment.payment_status == PS.PAID


def test_overdue_without_payment_expires_the_reservation(api_client, pending, fake_asaas):
    webhook(api_client, "PAYMENT_OVERDUE", pending, "pay_1", 30.0)
    pending.refresh_from_db()
    assert pending.payment_status == PS.EXPIRED
    assert pending.status == EnrollmentStatus.DROPPED


def test_overdue_installment_keeps_the_seat(api_client, student, paid_class, fake_asaas):
    response = self_enroll(as_user(api_client, student), paid_class, [tshirt(paid_class)], installments=2)
    enrollment = Enrollment.objects.get(pk=response.data["id"])
    webhook(api_client, "PAYMENT_RECEIVED", enrollment, "pay_1", 32.5, installment="ins_1")
    webhook(api_client, "PAYMENT_OVERDUE", enrollment, "pay_1b", 32.5, installment="ins_1")
    enrollment.refresh_from_db()
    assert enrollment.payment_status == PS.PARTIAL
    assert enrollment.has_overdue_installment is True


# ── Coordinator: enrolling, scholarship, payment in person ──────────────────


def test_coordinator_enrolls_without_link_for_payment_in_person(
    api_client, courses_leader, student, paid_class, fake_asaas
):
    response = as_user(api_client, courses_leader).post(
        f"/api/course-classes/{paid_class.id}/enrollments/", {"person_id": str(student.id)}, format="json"
    )
    assert response.status_code == 201, response.data
    enrollment = Enrollment.objects.get(person=student)
    assert enrollment.payment_status == PS.PENDING
    assert enrollment.payment_url == ""
    assert enrollment.expires_at is None  # no reservation clock for coordinator enrollments
    assert fake_asaas["charges"] == []


def test_teacher_cannot_enroll_in_a_paid_class(api_client, teacher, student, paid_class):
    response = as_user(api_client, teacher).post(
        f"/api/course-classes/{paid_class.id}/enrollments/", {"person_id": str(student.id)}, format="json"
    )
    assert response.status_code == 403


def test_teacher_can_still_mark_completed(api_client, teacher, pending):
    response = as_user(api_client, teacher).post(
        f"/api/course-classes/{pending.course_class_id}/enrollments/",
        {"person_id": str(pending.person_id), "status": "completed"},
        format="json",
    )
    assert response.status_code == 200, response.data
    pending.refresh_from_db()
    assert pending.status == EnrollmentStatus.COMPLETED
    assert pending.completed_at is not None


def test_partial_scholarship_replaces_the_charge(api_client, courses_leader, pending, fake_asaas):
    response = as_user(api_client, courses_leader).post(
        f"/api/course-enrollments/{pending.id}/scholarship/",
        {"discount_cents": 1500, "reason": "Família em dificuldade"},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert response.data["amount_cents"] == 1500
    assert response.data["scholarship_reason"] == "Família em dificuldade"
    assert response.data["scholarship_granted_by"]["id"] == str(courses_leader.id)
    assert "pay_1" in fake_asaas["cancelled"]
    assert fake_asaas["charges"][-1]["amount_cents"] == 1500


def test_full_scholarship_waives(api_client, courses_leader, pending, fake_asaas):
    response = as_user(api_client, courses_leader).post(
        f"/api/course-enrollments/{pending.id}/scholarship/",
        {"discount_cents": 3000, "reason": "Bolsa integral"},
        format="json",
    )
    assert response.data["payment_status"] == PS.WAIVED
    assert response.data["payment_url"] == ""
    assert len(fake_asaas["charges"]) == 1  # no new charge


@pytest.mark.parametrize(
    "payload", [{"discount_cents": 1000}, {"discount_cents": 2800, "reason": "x"}, {"discount_cents": 9000, "reason": "x"}]
)
def test_scholarship_rules(api_client, courses_leader, pending, fake_asaas, payload):
    # Needs a reason; can't leave less than R$ 5,00 to pay; can't exceed the price.
    response = as_user(api_client, courses_leader).post(
        f"/api/course-enrollments/{pending.id}/scholarship/", payload, format="json"
    )
    assert response.status_code == 400


def test_no_scholarship_after_a_payment(api_client, courses_leader, pending, fake_asaas):
    webhook(api_client, "PAYMENT_RECEIVED", pending, "pay_1", 30.0)
    response = as_user(api_client, courses_leader).post(
        f"/api/course-enrollments/{pending.id}/scholarship/", {"discount_cents": 1000, "reason": "x"}, format="json"
    )
    assert response.status_code == 400


def test_student_cannot_grant_themselves_a_scholarship(api_client, student, pending, fake_asaas):
    response = as_user(api_client, student).post(
        f"/api/course-enrollments/{pending.id}/scholarship/", {"discount_cents": 3000, "reason": "x"}, format="json"
    )
    assert response.status_code == 403


def test_manual_payments(api_client, courses_leader, pending, fake_asaas):
    as_user(api_client, courses_leader)
    url = f"/api/course-enrollments/{pending.id}/manual-payment/"
    assert api_client.post(url, {"amount_cents": 5000}, format="json").status_code == 400  # more than owed

    response = api_client.post(url, {"amount_cents": 1000, "note": "Dinheiro na secretaria"}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["payment_status"] == PS.PARTIAL
    assert response.data["payment_url"] == ""  # the online link was cancelled
    assert "pay_1" in fake_asaas["cancelled"]

    response = api_client.post(url, {"amount_cents": 2000}, format="json")
    assert response.data["payment_status"] == PS.PAID
    assert [p["method"] for p in response.data["payments"]] == [PaymentMethod.MANUAL] * 2
    assert response.data["payments"][0]["recorded_by"]["id"] == str(courses_leader.id)


def test_new_payment_link_for_the_balance(api_client, courses_leader, student, pending, fake_asaas):
    as_user(api_client, courses_leader).post(
        f"/api/course-enrollments/{pending.id}/manual-payment/", {"amount_cents": 1000}, format="json"
    )
    response = as_user(api_client, student).post(f"/api/course-enrollments/{pending.id}/pay/", {}, format="json")
    assert response.status_code == 200, response.data
    assert fake_asaas["charges"][-1]["amount_cents"] == 2000
    assert response.data["payment_url"].endswith("pay_2")


def test_others_cannot_see_or_pay_an_enrollment(api_client, regular_person, pending, fake_asaas):
    as_user(api_client, regular_person)
    assert api_client.get(f"/api/course-enrollments/{pending.id}/").status_code == 404
    assert api_client.post(f"/api/course-enrollments/{pending.id}/pay/", {}, format="json").status_code == 404
    assert api_client.get(f"/api/course-enrollments/?course_class={pending.course_class_id}").status_code == 403


# ── Cancelling and refunds ──────────────────────────────────────────────────


def test_student_cancels_unpaid_and_enrolls_again(api_client, student, pending, paid_class, fake_asaas):
    as_user(api_client, student)
    response = api_client.post(f"/api/course-enrollments/{pending.id}/cancel/", {}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["payment_status"] == PS.CANCELLED
    assert "pay_1" in fake_asaas["cancelled"]
    assert self_enroll(api_client, paid_class).status_code == 201
    assert Enrollment.objects.filter(person=student).count() == 2  # history kept


def test_student_cannot_cancel_after_paying(api_client, student, pending, fake_asaas):
    webhook(api_client, "PAYMENT_RECEIVED", pending, "pay_1", 30.0)
    response = as_user(api_client, student).post(f"/api/course-enrollments/{pending.id}/cancel/", {}, format="json")
    assert response.status_code == 400


def test_coordinator_refunds_before_the_class_starts(api_client, courses_leader, pending, fake_asaas):
    webhook(api_client, "PAYMENT_RECEIVED", pending, "pay_1", 20.0)  # partial online
    as_user(api_client, courses_leader).post(
        f"/api/course-enrollments/{pending.id}/manual-payment/", {"amount_cents": 1000}, format="json"
    )
    response = api_client.post(
        f"/api/course-enrollments/{pending.id}/cancel/", {"note": "Devolvido em dinheiro"}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["payment_status"] == PS.REFUNDED
    assert response.data["refunded_cents"] == 3000
    assert response.data["status"] == EnrollmentStatus.DROPPED
    assert fake_asaas["refunded"] == ["pay_1"]
    assert all(p["refunded_at"] for p in response.data["payments"])


def test_refund_after_start_needs_override(api_client, courses_leader, pending, paid_class, fake_asaas):
    webhook(api_client, "PAYMENT_RECEIVED", pending, "pay_1", 30.0)
    paid_class.start_date = TODAY - timedelta(days=1)
    paid_class.save()
    as_user(api_client, courses_leader)
    url = f"/api/course-enrollments/{pending.id}/cancel/"
    assert api_client.post(url, {}, format="json").status_code == 400
    response = api_client.post(url, {"force": True}, format="json")
    assert response.data["payment_status"] == PS.REFUNDED


def test_failed_refund_changes_nothing(api_client, courses_leader, pending, fake_asaas):
    webhook(api_client, "PAYMENT_RECEIVED", pending, "pay_1", 30.0)
    fake_asaas["refund_fails"] = True
    response = as_user(api_client, courses_leader).post(
        f"/api/course-enrollments/{pending.id}/cancel/", {}, format="json"
    )
    assert response.status_code == 503
    pending.refresh_from_db()
    assert pending.payment_status == PS.PAID


def test_enrollment_with_payments_cannot_be_deleted(api_client, courses_leader, pending, fake_asaas):
    webhook(api_client, "PAYMENT_RECEIVED", pending, "pay_1", 30.0)
    response = as_user(api_client, courses_leader).delete(
        f"/api/course-classes/{pending.course_class_id}/enrollments/{pending.person_id}/"
    )
    assert response.status_code == 400
    assert Enrollment.objects.filter(pk=pending.pk).exists()


# ── Material, finances, expenses ────────────────────────────────────────────


def test_teacher_marks_material_delivered(api_client, teacher, pending):
    response = as_user(api_client, teacher).post(
        f"/api/course-enrollments/{pending.id}/material/", {"delivered": True}, format="json"
    )
    assert response.status_code == 200
    assert response.data["material_delivered_at"]


def test_finance_summary_and_export(api_client, courses_leader, student, regular_person, paid_class, fake_asaas):
    self_enroll(as_user(api_client, student), paid_class, [tshirt(paid_class, "G")])
    self_enroll(as_user(api_client, regular_person), paid_class, [tshirt(paid_class, "M")])
    paid = Enrollment.objects.get(person=student)
    webhook(api_client, "PAYMENT_RECEIVED", paid, "pay_1", 65.0)
    ClassExpense.objects.create(course_class=paid_class, description="Apostilas", amount_cents=4000, date=TODAY)

    data = as_user(api_client, courses_leader).get(f"/api/course-classes/{paid_class.id}/finance/").data
    summary = data["summary"]
    assert summary["expected_cents"] == 13000
    assert summary["received_cents"] == 6500
    assert summary["received_online_cents"] == 6500
    assert summary["outstanding_cents"] == 6500
    assert summary["expenses_cents"] == 4000
    assert summary["balance_cents"] == 2500
    assert summary["by_status"] == {PS.PAID: 1, PS.PENDING: 1}
    shirts = next(i for i in summary["items"] if i["name"] == "Camiseta")
    assert shirts == {"name": "Camiseta", "total": 2, "options": {"G": 1, "M": 1}}
    assert len(data["enrollments"]) == 2

    response = api_client.get(f"/api/course-classes/{paid_class.id}/finance-export/")
    assert response.status_code == 200
    content = response.content.decode("utf-8-sig")
    assert "Aluno;E-mail;Situação" in content
    assert "Camiseta (G)" in content
    assert "Apostilas" in content


def test_expenses_are_for_coordinators(api_client, courses_leader, teacher, paid_class):
    payload = {"course_class_id": str(paid_class.id), "description": "Coffee break", "amount_cents": 2500,
               "date": str(TODAY)}
    assert as_user(api_client, teacher).post("/api/class-expenses/", payload, format="json").status_code == 403
    response = as_user(api_client, courses_leader).post("/api/class-expenses/", payload, format="json")
    assert response.status_code == 201, response.data
    listed = api_client.get(f"/api/class-expenses/?course_class={paid_class.id}").data["results"]
    assert [e["description"] for e in listed] == ["Coffee break"]
    assert api_client.delete(f"/api/class-expenses/{response.data['id']}/").status_code == 204


# ── Lessons are scoped (security fix) ───────────────────────────────────────


def test_outsider_cannot_see_or_create_lessons(api_client, regular_person, paid_class):
    Lesson.objects.create(course_class=paid_class, order=1, title="Aula 1")
    as_user(api_client, regular_person)
    assert api_client.get(f"/api/lessons/?course_class_id={paid_class.id}").data["count"] == 0
    response = api_client.post(
        "/api/lessons/", {"course_class_id": str(paid_class.id), "order": 2}, format="json"
    )
    assert response.status_code == 403


def test_teacher_creates_lessons(api_client, teacher, paid_class):
    response = as_user(api_client, teacher).post(
        "/api/lessons/", {"course_class_id": str(paid_class.id), "order": 1, "title": "Abertura"}, format="json"
    )
    assert response.status_code == 201, response.data


def test_fee_item_options_are_shown_to_members(api_client, student, paid_class):
    data = as_user(api_client, student).get("/api/course-classes/open/").data
    shirt = next(i for i in data[0]["fee_items"] if i["name"] == "Camiseta")
    assert shirt["options"] == ["P", "M", "G"]
    assert ClassFeeItem.objects.count() == 2
