"""Completion certificates: the code given on completion, the PDF for the
student and the class's managers, and the public verification page."""

import pytest

from apps.courses import certificates
from apps.courses.choices import EnrollmentStatus
from apps.courses.models import Enrollment, Lesson, LessonAttendance
from tests.conftest import make_person

pytestmark = pytest.mark.django_db


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def graduate(free_class, campus):
    person = make_person(email="formando@ilan.church", name="Formando da Silva", campus=campus)
    Enrollment.objects.create(course_class=free_class, person=person)
    lesson = Lesson.objects.create(course_class=free_class, order=1, title="Única")
    LessonAttendance.objects.create(lesson=lesson, person=person, present=True)
    return person


def complete(api_client, manager, course_class, person):
    return auth(api_client, manager).post(
        f"/api/course-classes/{course_class.id}/complete/", {"person_ids": [str(person.id)]}, format="json"
    )


def enrollment_of(person):
    return Enrollment.objects.get(person=person)


def test_completing_gives_a_verification_code(api_client, courses_leader, free_class, graduate):
    response = complete(api_client, courses_leader, free_class, graduate)

    code = enrollment_of(graduate).certificate_code
    assert len(code) == 12 and set(code) <= set(certificates.CODE_ALPHABET)
    row = next(r for r in response.data if r["person"]["id"] == str(graduate.id))
    assert row["certificate_code"] == certificates.display_code(code)


def test_status_change_to_completed_also_gives_a_code(api_client, courses_leader, free_class, graduate):
    auth(api_client, courses_leader).post(
        f"/api/course-classes/{free_class.id}/enrollments/",
        {"person_id": str(graduate.id), "status": "completed"},
        format="json",
    )

    assert enrollment_of(graduate).certificate_code


def test_student_downloads_their_certificate(api_client, courses_leader, free_class, graduate):
    complete(api_client, courses_leader, free_class, graduate)
    enrollment = enrollment_of(graduate)

    response = auth(api_client, graduate).get(
        f"/api/course-enrollments/{enrollment.id}/certificate/", HTTP_ORIGIN="https://app.igreja.test"
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert 'filename="certificado-escola-de-lideres-formando-da-silva.pdf"' in response["Content-Disposition"]


def test_class_manager_downloads_but_another_member_cannot(
    api_client, courses_leader, free_class, graduate, campus
):
    complete(api_client, courses_leader, free_class, graduate)
    url = f"/api/course-enrollments/{enrollment_of(graduate).id}/certificate/"
    stranger = make_person(email="curioso@ilan.church", name="Curioso", campus=campus)

    assert auth(api_client, courses_leader).get(url).status_code == 200
    assert auth(api_client, stranger).get(url).status_code in (403, 404)


def test_no_certificate_before_completion(api_client, free_class, graduate):
    response = auth(api_client, graduate).get(f"/api/course-enrollments/{enrollment_of(graduate).id}/certificate/")

    assert response.status_code == 400


def test_public_page_confirms_a_certificate_without_login(api_client, courses_leader, free_class, graduate):
    complete(api_client, courses_leader, free_class, graduate)
    code = certificates.display_code(enrollment_of(graduate).certificate_code)
    api_client.force_authenticate(user=None)

    # Typed in lower case and without dashes works too.
    response = api_client.get(f"/api/certificates/{code.replace('-', '').lower()}/")

    assert response.status_code == 200
    assert response.data["person_name"] == "Formando da Silva"
    assert response.data["course_name"] == "Escola de Líderes"
    assert response.data["code"] == code
    assert response.data["lesson_count"] == 1
    assert response.data["attendance_percent"] == 100
    assert "email" not in str(response.data)


def test_unknown_or_reopened_certificate_is_not_found(api_client, courses_leader, free_class, graduate):
    complete(api_client, courses_leader, free_class, graduate)
    enrollment = enrollment_of(graduate)
    url = f"/api/certificates/{enrollment.certificate_code}/"

    assert api_client.get("/api/certificates/AAAA-BBBB-CCCC/").status_code == 404
    enrollment.status = EnrollmentStatus.ACTIVE
    enrollment.save()
    assert api_client.get(url).status_code == 404


def test_pdf_lists_the_verification_address(free_class, graduate, settings):
    enrollment = enrollment_of(graduate)
    enrollment.status = EnrollmentStatus.COMPLETED
    enrollment.save()
    certificates.ensure_code(enrollment)
    settings.FRONTEND_URL = "https://redis.igreja.test"

    url = certificates.verification_url(enrollment.certificate_code)

    assert url == f"https://redis.igreja.test/certificado/{certificates.display_code(enrollment.certificate_code)}"
    assert certificates.build_pdf(enrollment).startswith(b"%PDF")
