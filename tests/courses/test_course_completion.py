"""Completion of a class: each student's attendance against the class's
minimum, closing the class for the students the managers confirm, and the
student's own attendance in "Matrículas"."""

import pytest

from apps.courses import completion
from apps.courses.choices import EnrollmentStatus
from apps.courses.models import Enrollment, Lesson, LessonAttendance
from tests.conftest import make_person

pytestmark = pytest.mark.django_db


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def lessons(free_class):
    return [Lesson.objects.create(course_class=free_class, order=i, title=f"Aula {i}") for i in range(1, 5)]


@pytest.fixture
def students(free_class, campus):
    people = [make_person(email=f"aluno{i}@ilan.church", name=f"Aluno {i}", campus=campus) for i in range(1, 4)]
    for person in people:
        Enrollment.objects.create(course_class=free_class, person=person)
    return people


def take(lesson, present_by_person):
    for person, present in present_by_person.items():
        LessonAttendance.objects.create(lesson=lesson, person=person, present=present)


@pytest.fixture
def attendance(lessons, students):
    """Aluno 1: 4/4. Aluno 2: 2/4 (50%). Aluno 3 enrolled late: 3/3 of the last lessons."""
    a1, a2, a3 = students
    take(lessons[0], {a1: True, a2: True})
    take(lessons[1], {a1: True, a2: False, a3: True})
    take(lessons[2], {a1: True, a2: True, a3: True})
    take(lessons[3], {a1: True, a2: False, a3: True})


def test_attendance_counts_only_lessons_taken_for_the_student(free_class, students, attendance):
    by_person = completion.attendance_by_person(free_class)
    a1, a2, a3 = students

    assert by_person[a1.id] == {"present": 4, "counted": 4, "percent": 100}
    assert by_person[a2.id] == {"present": 2, "counted": 4, "percent": 50}
    # The first lesson happened before Aluno 3 enrolled: it doesn't count against them.
    assert by_person[a3.id] == {"present": 3, "counted": 3, "percent": 100}


def test_criterion_uses_the_class_minimum_without_rounding(free_class):
    free_class.min_attendance_percent = 75
    assert completion.meets_criterion(free_class, completion.summary(3, 4))
    assert not completion.meets_criterion(free_class, completion.summary(2, 3))  # 66.7%
    assert not completion.meets_criterion(free_class, completion.EMPTY_SUMMARY)
    free_class.min_attendance_percent = 50
    assert completion.meets_criterion(free_class, completion.summary(2, 4))


def test_roster_shows_attendance_and_who_meets_the_minimum(
    api_client, courses_leader, free_class, students, attendance
):
    response = auth(api_client, courses_leader).get(f"/api/course-classes/{free_class.id}/enrollments/")

    assert response.status_code == 200
    rows = {row["person"]["email"]: row for row in response.data}
    assert rows["aluno1@ilan.church"]["attendance"]["percent"] == 100
    assert rows["aluno1@ilan.church"]["meets_attendance"] is True
    assert rows["aluno2@ilan.church"]["attendance"] == {"present": 2, "counted": 4, "percent": 50}
    assert rows["aluno2@ilan.church"]["meets_attendance"] is False


def test_the_minimum_is_set_per_class(api_client, courses_leader, free_class):
    url = f"/api/course-classes/{free_class.id}/"
    client = auth(api_client, courses_leader)

    assert client.get(url).data["min_attendance_percent"] == 75
    response = client.patch(url, {"min_attendance_percent": 60}, format="json")
    assert response.status_code == 200, response.data
    assert response.data["min_attendance_percent"] == 60
    assert client.patch(url, {"min_attendance_percent": 120}, format="json").status_code == 400


def test_closing_the_class_completes_the_confirmed_students(
    api_client, courses_leader, free_class, students, attendance
):
    a1, a2, a3 = students

    response = auth(api_client, courses_leader).post(
        f"/api/course-classes/{free_class.id}/complete/",
        {"person_ids": [str(a1.id), str(a3.id)]},
        format="json",
    )

    assert response.status_code == 200, response.data
    status = {row["person"]["email"]: row["status"] for row in response.data}
    assert status == {
        "aluno1@ilan.church": "completed",
        "aluno2@ilan.church": "active",
        "aluno3@ilan.church": "completed",
    }
    assert Enrollment.objects.get(person=a1).completed_at is not None
    assert api_client.get(f"/api/course-classes/{free_class.id}/").data["completed_count"] == 2


def test_teacher_may_close_the_class_but_not_a_student(api_client, free_class, students, campus):
    from apps.courses.choices import TeacherRole
    from apps.courses.models import CourseClassTeacher

    teacher = make_person(email="prof.livre@ilan.church", name="Prof", campus=campus)
    CourseClassTeacher.objects.create(course_class=free_class, person=teacher, role=TeacherRole.TEACHER)
    body = {"person_ids": [str(students[0].id)]}
    url = f"/api/course-classes/{free_class.id}/complete/"

    assert auth(api_client, students[1]).post(url, body, format="json").status_code in (403, 404)
    assert auth(api_client, teacher).post(url, body, format="json").status_code == 200


def test_dropped_students_are_not_completed(api_client, courses_leader, free_class, students):
    Enrollment.objects.filter(person=students[0]).update(status=EnrollmentStatus.DROPPED)

    auth(api_client, courses_leader).post(
        f"/api/course-classes/{free_class.id}/complete/", {"person_ids": [str(students[0].id)]}, format="json"
    )

    assert Enrollment.objects.get(person=students[0]).status == EnrollmentStatus.DROPPED


def test_student_sees_their_own_attendance_lesson_by_lesson(
    api_client, free_class, students, lessons, attendance
):
    response = auth(api_client, students[2]).get("/api/course-enrollments/?mine=1")

    assert response.status_code == 200
    (mine,) = response.data["results"] if "results" in response.data else response.data
    assert mine["course_class"]["min_attendance_percent"] == 75
    assert mine["attendance"]["percent"] == 100
    assert mine["attendance"]["counted"] == 3
    assert [lesson["present"] for lesson in mine["attendance"]["lessons"]] == [None, True, True, True]
    assert mine["meets_attendance"] is True


def test_lesson_total_counts_completed_students(api_client, courses_leader, free_class, students, lessons):
    Enrollment.objects.filter(person=students[0]).update(status=EnrollmentStatus.COMPLETED)

    response = auth(api_client, courses_leader).get(f"/api/lessons/?course_class_id={free_class.id}")

    rows = response.data["results"] if "results" in response.data else response.data
    assert rows[0]["total_enrolled"] == 3
