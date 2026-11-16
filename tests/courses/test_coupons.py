"""Discount coupons on paid course enrollments (see apps.payments.coupons)."""

import pytest

from apps.churches.choices import LeadershipRole
from apps.courses.choices import EnrollmentPaymentStatus as PS
from apps.courses.models import CampusCoursesLeadership, Enrollment
from apps.payments.models import Coupon
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

COUPONS = "/api/payments/coupons/"


def as_user(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


# --- Course enrollment ----------------------------------------------------------------


def enroll(api_client, course_class, code):
    return api_client.post(
        f"/api/course-classes/{course_class.id}/enroll/", {"items": [], "coupon_code": code}, format="json"
    )


def test_class_coupon_managed_by_course_coordinators(api_client, paid_class, campus):
    coordinator = make_person(email="coord@ilan.church", name="Coord", campus=campus)
    CampusCoursesLeadership.objects.create(campus=campus, person=coordinator, role=LeadershipRole.LEADER)

    response = as_user(api_client, coordinator).post(
        COUPONS,
        {"course_class_id": str(paid_class.id), "code": "ALUNO10", "kind": "fixed", "amount_cents": 1000},
        format="json",
    )

    assert response.status_code == 201, response.data
    assert Coupon.objects.get(code="ALUNO10").course_class == paid_class


def test_coupon_lowers_the_enrollment(api_client, student, paid_class, fake_asaas):
    Coupon.objects.create(course_class=paid_class, code="ALUNO10", kind="fixed", amount_cents=1000)  # R$ 30 → R$ 20

    response = enroll(as_user(api_client, student), paid_class, "aluno10")

    assert response.status_code == 201, response.data
    enrollment = Enrollment.objects.get(person=student)
    assert enrollment.gross_cents == 3000
    assert enrollment.coupon_discount_cents == 1000
    assert enrollment.amount_cents == 2000
    assert response.data["coupon_code"] == "ALUNO10"
    assert fake_asaas["charges"][0]["amount_cents"] == 2000


def test_full_class_coupon_waives_the_enrollment(api_client, student, paid_class, fake_asaas):
    Coupon.objects.create(course_class=paid_class, code="BOLSA", kind="percent", percent=100)

    response = enroll(as_user(api_client, student), paid_class, "BOLSA")

    assert response.status_code == 201, response.data
    enrollment = Enrollment.objects.get(person=student)
    assert enrollment.payment_status == PS.WAIVED
    assert fake_asaas["charges"] == []


def test_scholarship_applies_on_top_of_the_coupon(api_client, student, paid_class, fake_asaas):
    from apps.courses import billing

    Coupon.objects.create(course_class=paid_class, code="ALUNO10", kind="fixed", amount_cents=1000)
    enroll(as_user(api_client, student), paid_class, "ALUNO10")
    enrollment = Enrollment.objects.get(person=student)

    billing.grant_scholarship(enrollment, 2000, "Ajuda", by=None)  # R$ 20 left → full scholarship

    enrollment.refresh_from_db()
    assert enrollment.amount_cents == 0
    assert enrollment.payment_status == PS.WAIVED
