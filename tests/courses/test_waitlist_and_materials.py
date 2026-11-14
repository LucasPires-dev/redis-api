"""Waitlist of full classes and lesson materials."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.courses.choices import TeacherRole
from apps.courses.models import (
    Course,
    CourseClass,
    CourseClassTeacher,
    Enrollment,
    Lesson,
    WaitlistEntry,
)
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def full_class(church, campus):
    course = Course.objects.create(church=church, name="Fundamentos")
    return CourseClass.objects.create(
        course=course, campus=campus, name="Turma cheia", self_enrollment=True, max_capacity=1,
        reservation_days=2, end_date=TODAY + timedelta(days=30),
    )


@pytest.fixture
def people(campus):
    return [
        make_person(email=f"aluno{i}@ilan.church", name=f"Aluno {i}", campus=campus) for i in range(1, 4)
    ]


def enroll(api_client, person, course_class):
    return auth(api_client, person).post(f"/api/course-classes/{course_class.id}/enroll/", {}, format="json")


def open_row(api_client, person, course_class):
    rows = auth(api_client, person).get("/api/course-classes/open/").data
    return next(r for r in rows if r["id"] == str(course_class.id))


# ── Waitlist ─────────────────────────────────────────────────────────────────


def test_join_only_when_full(api_client, full_class, people):
    first, second, _ = people
    early = auth(api_client, first).post(f"/api/course-classes/{full_class.id}/waitlist/")
    assert early.status_code == 400  # there's a seat: enroll instead

    assert enroll(api_client, first, full_class).status_code == 201
    joined = auth(api_client, second).post(f"/api/course-classes/{full_class.id}/waitlist/")

    assert joined.status_code == 201, joined.data
    assert joined.data["position"] == 1
    row = open_row(api_client, second, full_class)
    assert row["seats_left"] == 0
    assert row["my_waitlist"]["position"] == 1


def test_freed_seat_is_held_for_the_first_in_line(api_client, full_class, people):
    first, second, third = people
    enroll(api_client, first, full_class)
    auth(api_client, second).post(f"/api/course-classes/{full_class.id}/waitlist/")
    auth(api_client, third).post(f"/api/course-classes/{full_class.id}/waitlist/")
    assert open_row(api_client, third, full_class)["my_waitlist"]["position"] == 2

    enrollment = Enrollment.objects.get(person=first)
    auth(api_client, first).post(f"/api/course-enrollments/{enrollment.id}/cancel/", {}, format="json")

    # The seat is the second person's: the third still sees the class full.
    offered = open_row(api_client, second, full_class)
    assert offered["seats_left"] == 1
    assert offered["my_waitlist"]["offer_expires_at"] is not None
    third_row = open_row(api_client, third, full_class)
    assert third_row["seats_left"] == 0
    assert third_row["my_waitlist"]["position"] == 1
    assert enroll(api_client, third, full_class).status_code == 400

    assert enroll(api_client, second, full_class).status_code == 201
    assert not WaitlistEntry.objects.filter(person=second).exists()
    # Still full: nobody else is offered a seat.
    assert open_row(api_client, third, full_class)["my_waitlist"]["offer_expires_at"] is None


def test_offer_not_taken_in_time_goes_to_the_next(api_client, full_class, people):
    first, second, third = people
    enroll(api_client, first, full_class)
    for person in (second, third):
        auth(api_client, person).post(f"/api/course-classes/{full_class.id}/waitlist/")
    full_class.max_capacity = 2  # one more seat
    full_class.save()
    open_row(api_client, second, full_class)  # hands out the offer
    WaitlistEntry.objects.filter(person=second).update(offer_expires_at=timezone.now() - timedelta(minutes=1))

    row = open_row(api_client, third, full_class)

    assert row["seats_left"] == 1
    assert row["my_waitlist"]["offer_expires_at"] is not None
    assert not WaitlistEntry.objects.filter(person=second).exists()


def test_leaving_gives_the_held_seat_to_the_next(api_client, full_class, people):
    first, second, third = people
    enroll(api_client, first, full_class)
    for person in (second, third):
        auth(api_client, person).post(f"/api/course-classes/{full_class.id}/waitlist/")
    full_class.max_capacity = 2
    full_class.save()
    assert open_row(api_client, second, full_class)["my_waitlist"]["offer_expires_at"]

    assert auth(api_client, second).delete(f"/api/course-classes/{full_class.id}/waitlist/").status_code == 204

    assert open_row(api_client, third, full_class)["my_waitlist"]["offer_expires_at"] is not None


def test_coordination_sees_and_edits_the_line(api_client, full_class, people, courses_leader):
    first, second, third = people
    enroll(api_client, first, full_class)
    for person in (second, third):
        auth(api_client, person).post(f"/api/course-classes/{full_class.id}/waitlist/")

    client = auth(api_client, courses_leader)
    url = f"/api/course-classes/{full_class.id}/waitlist-entries/"
    entries = client.get(url).data
    assert [(e["person"]["name"], e["position"]) for e in entries] == [("Aluno 2", 1), ("Aluno 3", 2)]
    assert client.get(f"/api/course-classes/{full_class.id}/").data["waitlist_count"] == 2

    assert client.delete(f"{url}{entries[0]['id']}/").status_code == 204
    assert [e["position"] for e in client.get(url).data] == [1]

    assert auth(api_client, third).get(url).status_code in (403, 404)


# ── Materials ────────────────────────────────────────────────────────────────


@pytest.fixture
def lesson(full_class):
    return Lesson.objects.create(course_class=full_class, order=1, title="Introdução")


def test_teacher_adds_materials_and_students_see_them(api_client, full_class, lesson, people, campus):
    teacher = make_person(email="prof.mat@ilan.church", name="Prof", campus=campus)
    CourseClassTeacher.objects.create(course_class=full_class, person=teacher, role=TeacherRole.TEACHER)
    student = people[0]
    enroll(api_client, student, full_class)

    client = auth(api_client, teacher)
    created = client.post(
        f"/api/lessons/{lesson.id}/materials/",
        {"title": "Slides", "url": "https://drive.google.com/x"},
        format="json",
    )
    assert created.status_code == 201, created.data
    assert client.get(f"/api/lessons/{lesson.id}/").data["materials"][0]["title"] == "Slides"
    bad = client.post(f"/api/lessons/{lesson.id}/materials/", {"title": "X", "url": "não é link"}, format="json")
    assert bad.status_code == 400

    mine = auth(api_client, student).get("/api/course-enrollments/?mine=1").data
    mine = mine.get("results", mine)
    (lesson_row,) = mine[0]["attendance"]["lessons"]
    assert lesson_row["materials"] == [
        {"id": created.data["id"], "title": "Slides", "url": "https://drive.google.com/x"}
    ]

    # Students can't add or remove.
    assert auth(api_client, student).post(
        f"/api/lessons/{lesson.id}/materials/", {"title": "Y", "url": "https://x.y"}, format="json"
    ).status_code in (403, 404)
    removed = auth(api_client, teacher).delete(f"/api/lessons/{lesson.id}/materials/{created.data['id']}/")
    assert removed.status_code == 204
