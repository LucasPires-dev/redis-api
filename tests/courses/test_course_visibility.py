"""Course visibility and per-course flags used by the Cursos tabs."""

import pytest

from apps.courses.choices import TeacherRole
from apps.courses.models import Course, CourseClass, CourseClassTeacher
from tests.conftest import make_person

pytestmark = pytest.mark.django_db


def rows(response):
    body = response.json()
    return body["results"] if isinstance(body, dict) else body


def test_teacher_sees_campus_course_of_class_taught_elsewhere(api_client, church, campus, other_campus):
    """The teacher of a campus course used to get 404 on the course itself."""
    course = Course.objects.create(church=church, campus=other_campus, name="Liderança")
    course_class = CourseClass.objects.create(course=course, campus=other_campus, name="Sábado")
    teacher = make_person(email="prof@ilan.church", name="Prof", campus=campus)
    CourseClassTeacher.objects.create(course_class=course_class, person=teacher, role=TeacherRole.TEACHER)

    api_client.force_authenticate(user=teacher)
    assert api_client.get(f"/api/courses/{course.id}/").status_code == 200
    names = [c["name"] for c in rows(api_client.get("/api/courses/"))]
    assert "Liderança" in names


def test_course_flags(api_client, church, campus, courses_leader, regular_person, manager):
    church_wide = Course.objects.create(church=church, campus=None, name="Fundamentos")
    local = Course.objects.create(church=church, campus=campus, name="Escola de Líderes")

    api_client.force_authenticate(user=courses_leader)
    data = {c["name"]: c for c in rows(api_client.get("/api/courses/"))}
    # Local coordinator: manages the campus course; opens classes of the church-wide one.
    assert data["Escola de Líderes"]["can_manage"] and data["Escola de Líderes"]["can_add_class"]
    assert not data["Fundamentos"]["can_manage"] and data["Fundamentos"]["can_add_class"]

    api_client.force_authenticate(user=regular_person)
    data = {c["name"]: c for c in rows(api_client.get("/api/courses/"))}
    assert not data["Fundamentos"]["can_manage"] and not data["Fundamentos"]["can_add_class"]

    api_client.force_authenticate(user=manager)
    data = {c["name"]: c for c in rows(api_client.get("/api/courses/"))}
    assert all(c["can_manage"] and c["can_add_class"] for c in data.values())
    assert local.id and church_wide.id


def test_teaching_filter_lists_only_classes_taught(api_client, church, campus):
    """A coordinator who also teaches: ?teaching=1 lists just the classes they teach."""
    from apps.churches.choices import LeadershipRole
    from apps.courses.models import CampusCoursesLeadership

    person = make_person(email="coord.prof@ilan.church", name="Coord e Prof", campus=campus)
    CampusCoursesLeadership.objects.create(campus=campus, person=person, role=LeadershipRole.LEADER)
    course = Course.objects.create(church=church, campus=campus, name="Fundamentos")
    taught = CourseClass.objects.create(course=course, campus=campus, name="Terça")
    CourseClass.objects.create(course=course, campus=campus, name="Quinta")
    CourseClassTeacher.objects.create(course_class=taught, person=person, role=TeacherRole.ASSISTANT)

    api_client.force_authenticate(user=person)
    assert {c["name"] for c in rows(api_client.get("/api/course-classes/"))} == {"Terça", "Quinta"}
    assert [c["name"] for c in rows(api_client.get("/api/course-classes/?teaching=1"))] == ["Terça"]
