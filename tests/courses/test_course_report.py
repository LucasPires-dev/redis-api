"""Coordination report of courses and the students a class should care for."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import LeadershipRole
from apps.courses.choices import EnrollmentPaymentStatus, EnrollmentStatus, PaymentMethod
from apps.courses.models import (
    CampusCoursesLeadership,
    Course,
    CourseClass,
    CourseGeneralLeadership,
    Enrollment,
    EnrollmentPayment,
    Lesson,
    LessonAttendance,
)
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def students(campus, count, prefix="aluno"):
    return [make_person(email=f"{prefix}{i}@ilan.church", name=f"Aluno {i}", campus=campus) for i in range(count)]


def attend(course_class, pattern_by_person):
    """pattern: "PPA-" → present, present, absent, not taken — lesson by lesson."""
    length = max(len(p) for p in pattern_by_person.values())
    lessons = [
        Lesson.objects.get_or_create(
            course_class=course_class, order=i + 1, defaults={"date": TODAY - timedelta(days=7 * (length - i))}
        )[0]
        for i in range(length)
    ]
    for person, pattern in pattern_by_person.items():
        for lesson, mark in zip(lessons, pattern):
            if mark != "-":
                LessonAttendance.objects.create(lesson=lesson, person=person, present=mark == "P")


@pytest.fixture
def ongoing_class(course, campus):
    return CourseClass.objects.create(
        course=course, campus=campus, name="Turma Atual",
        start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=30),
    )


# ── Students to care for ─────────────────────────────────────────────────────


def test_care_flags_absences_in_a_row_and_below_minimum(api_client, courses_leader, ongoing_class, campus):
    fine, missing, low, recovering, done = students(campus, 5)
    for person in (fine, missing, low, recovering, done):
        Enrollment.objects.create(course_class=ongoing_class, person=person)
    Enrollment.objects.filter(person=done).update(status=EnrollmentStatus.COMPLETED)
    attend(ongoing_class, {
        fine: "PPPP",
        missing: "PPAA",     # two in a row (and 50%)
        low: "AAPP",         # back, but 50% < 75%
        recovering: "APPP",  # 75%: fine
        done: "AAAA",        # completed: not listed
    })

    response = auth(api_client, courses_leader).get(f"/api/course-classes/{ongoing_class.id}/care/")

    assert response.status_code == 200
    listed = {row["person"]["id"]: row for row in response.data}
    assert set(listed) == {str(missing.id), str(low.id)}
    assert response.data[0]["person"]["id"] == str(missing.id)  # the most urgent first
    assert listed[str(missing.id)]["reasons"] == ["absences_in_a_row", "below_minimum"]
    assert listed[str(missing.id)]["absences_in_a_row"] == 2
    assert listed[str(low.id)]["reasons"] == ["below_minimum"]
    assert listed[str(low.id)]["attendance"]["percent"] == 50
    assert listed[str(low.id)]["last_present_on"] == TODAY - timedelta(days=7)


def test_below_minimum_waits_for_three_lessons(api_client, courses_leader, ongoing_class, campus):
    (person,) = students(campus, 1)
    Enrollment.objects.create(course_class=ongoing_class, person=person)
    attend(ongoing_class, {person: "AP"})

    response = auth(api_client, courses_leader).get(f"/api/course-classes/{ongoing_class.id}/care/")

    assert response.data == []


def test_care_is_for_who_manages_the_class(api_client, ongoing_class, campus):
    (member,) = students(campus, 1, prefix="membro")

    response = auth(api_client, member).get(f"/api/course-classes/{ongoing_class.id}/care/")

    assert response.status_code in (403, 404)


# ── Coordination report ──────────────────────────────────────────────────────


@pytest.fixture
def report_data(course, campus, ongoing_class):
    """Ongoing class: 3 active, 1 completed, 1 dropped, 1 cancelled, 1 expired (not a dropout)."""
    people = students(campus, 7)
    statuses = [
        (EnrollmentStatus.ACTIVE, EnrollmentPaymentStatus.FREE),
        (EnrollmentStatus.ACTIVE, EnrollmentPaymentStatus.FREE),
        (EnrollmentStatus.ACTIVE, EnrollmentPaymentStatus.FREE),
        (EnrollmentStatus.COMPLETED, EnrollmentPaymentStatus.FREE),
        (EnrollmentStatus.DROPPED, EnrollmentPaymentStatus.FREE),
        (EnrollmentStatus.ACTIVE, EnrollmentPaymentStatus.CANCELLED),
        (EnrollmentStatus.ACTIVE, EnrollmentPaymentStatus.EXPIRED),
    ]
    for person, (status, payment) in zip(people, statuses):
        Enrollment.objects.create(course_class=ongoing_class, person=person, status=status, payment_status=payment)
    # 6 of 8 attendances: 75%. The second student missed the last two → to care.
    attend(ongoing_class, {people[0]: "PP", people[1]: "PP", people[2]: "AA", people[3]: "PP"})

    finished = CourseClass.objects.create(
        course=course, campus=campus, name="Turma Antiga",
        start_date=TODAY - timedelta(days=120), end_date=TODAY - timedelta(days=10),
    )
    payer = make_person(email="pagante@ilan.church", name="Pagante", campus=campus)
    enrollment = Enrollment.objects.create(
        course_class=finished, person=payer, payment_status=EnrollmentPaymentStatus.PARTIAL,
        gross_cents=3000, amount_cents=3000,
    )
    EnrollmentPayment.objects.create(
        enrollment=enrollment, method=PaymentMethod.MANUAL, amount_cents=1000, paid_at=timezone.now()
    )
    return {"ongoing": ongoing_class, "finished": finished}


def rows_by_name(response):
    return {row["name"]: row for row in response.data["classes"]}


def test_report_counts_enrollment_completion_dropouts_and_attendance(api_client, courses_leader, report_data):
    response = auth(api_client, courses_leader).get("/api/course-classes/report/")

    assert response.status_code == 200, response.data
    row = rows_by_name(response)["Turma Atual"]
    assert row["state"] == "ongoing"
    assert (row["enrolled"], row["active"], row["completed"], row["dropouts"]) == (4, 3, 1, 2)
    assert row["completion_rate"] == 17  # 1 of 6 who started
    assert row["dropout_rate"] == 33  # 2 of 6
    assert row["attendance_rate"] == 75
    assert (row["lessons_held"], row["lesson_count"]) == (2, 2)
    assert row["to_care"] == 1
    assert row["is_paid"] is False


def test_report_money_per_class_and_totals(api_client, courses_leader, report_data):
    response = auth(api_client, courses_leader).get("/api/course-classes/report/")

    row = rows_by_name(response)["Turma Antiga"]
    assert row["state"] == "finished"
    assert row["is_paid"] is True
    assert (row["expected_cents"], row["received_cents"], row["outstanding_cents"]) == (3000, 1000, 2000)
    totals = response.data["totals"]
    assert totals["classes"] == 2
    assert totals["ongoing"] == 1
    assert totals["enrolled"] == 5
    assert totals["outstanding_cents"] == 2000


def test_report_filters_by_state_and_campus(api_client, courses_leader, report_data, campus):
    client = auth(api_client, courses_leader)

    assert list(rows_by_name(client.get("/api/course-classes/report/?state=finished"))) == ["Turma Antiga"]
    assert client.get("/api/course-classes/report/?state=whatever").status_code == 400
    other = client.get("/api/course-classes/report/?campus=00000000-0000-0000-0000-000000000000")
    assert other.data["classes"] == []


def test_report_scope_follows_coordination(api_client, church, campus, other_campus, report_data):
    elsewhere_course = Course.objects.create(church=church, campus=other_campus, name="Curso de Fora")
    CourseClass.objects.create(course=elsewhere_course, campus=other_campus, name="Turma de Fora")
    local = make_person(email="local.cursos@ilan.church", name="Local", campus=other_campus)
    CampusCoursesLeadership.objects.create(campus=other_campus, person=local, role=LeadershipRole.LEADER)
    general = make_person(email="geral.cursos@ilan.church", name="Geral", campus=campus)
    CourseGeneralLeadership.objects.create(church=church).leaders.add(general)

    local_view = auth(api_client, local).get("/api/course-classes/report/")
    assert list(rows_by_name(local_view)) == ["Turma de Fora"]
    assert [c["name"] for c in local_view.data["campuses"]] == ["Outro Campus"]

    general_view = auth(api_client, general).get("/api/course-classes/report/")
    assert set(rows_by_name(general_view)) == {"Turma Atual", "Turma Antiga", "Turma de Fora"}


def test_members_and_teachers_do_not_see_the_report(api_client, campus):
    (member,) = students(campus, 1, prefix="membro")

    assert auth(api_client, member).get("/api/course-classes/report/").status_code == 403
