"""Course responsibilities: who appoints whom, and what each level can do.

Same general + local pattern as cells:
- manager → appoints the general courses leader (Django Admin);
- general courses leader → every course and class on every campus, and appoints
  each campus's courses leader;
- campus leader → appoints their campus's courses leader and keeps managing the
  campus's courses too;
- campus courses leader → courses of their campus and every class held there
  (including classes of church-wide courses), but not church-wide courses.
"""

import pytest

from apps.courses.models import CampusCoursesLeadership, Course, CourseGeneralLeadership
from apps.churches.choices import LeadershipRole
from tests.conftest import make_person

pytestmark = pytest.mark.django_db


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def courses_general_leader(church, campus):
    person = make_person(email="geral.cursos@ilan.church", name="Geral Cursos", campus=campus)
    CourseGeneralLeadership.objects.create(church=church).leaders.add(person)
    return person


@pytest.fixture
def campus_courses_leader(campus):
    person = make_person(email="cursos.campus@ilan.church", name="Resp Cursos Campus", campus=campus)
    CampusCoursesLeadership.objects.create(campus=campus, person=person, role=LeadershipRole.LEADER)
    return person


@pytest.fixture
def campus_course(church, campus):
    return Course.objects.create(church=church, campus=campus, name="Escola de Líderes")


@pytest.fixture
def other_campus_course(church, other_campus):
    return Course.objects.create(church=church, campus=other_campus, name="Curso de Fora")


@pytest.fixture
def church_wide_course(church):
    return Course.objects.create(church=church, campus=None, name="Fundamentos da Fé")


def leadership_url(campus):
    return f"/api/campuses/{campus.id}/courses-leaderships/"


# --- Appointing the campus courses leader ----------------------------------------------


def test_campus_leader_appoints_campus_courses_leader(api_client, campus_leader, regular_person, campus):
    response = auth(api_client, campus_leader).post(
        leadership_url(campus), {"person_id": str(regular_person.id), "role": "leader"}, format="json"
    )

    assert response.status_code == 201, response.data
    listed = api_client.get(leadership_url(campus)).data
    assert [entry["person"]["id"] for entry in listed] == [str(regular_person.id)]


def test_general_courses_leader_appoints_at_any_campus(
    api_client, courses_general_leader, other_campus
):
    person_there = make_person(email="la@ilan.church", name="De Lá", campus=other_campus)

    response = auth(api_client, courses_general_leader).post(
        leadership_url(other_campus), {"person_id": str(person_there.id), "role": "leader"}, format="json"
    )

    assert response.status_code == 201, response.data


def test_campus_courses_leader_cannot_appoint_peers(api_client, campus_courses_leader, regular_person, campus):
    # Same level can't promote peers — only the level above.
    response = auth(api_client, campus_courses_leader).post(
        leadership_url(campus), {"person_id": str(regular_person.id), "role": "co_leader"}, format="json"
    )

    assert response.status_code == 403


def test_courses_leader_must_belong_to_the_campus(api_client, campus_leader, campus, other_campus):
    outsider = make_person(email="fora@ilan.church", name="De Fora", campus=other_campus)

    response = auth(api_client, campus_leader).post(
        leadership_url(campus), {"person_id": str(outsider.id), "role": "leader"}, format="json"
    )

    assert response.status_code == 400
    assert "person_id" in response.data


def test_campus_leader_removes_campus_courses_leader(api_client, campus_leader, campus_courses_leader, campus):
    response = auth(api_client, campus_leader).delete(f"{leadership_url(campus)}{campus_courses_leader.id}/")

    assert response.status_code == 204
    assert not CampusCoursesLeadership.objects.filter(campus=campus).exists()


# --- What the campus courses leader can do -------------------------------------------


def test_campus_courses_leader_creates_course_at_their_campus(api_client, campus_courses_leader, campus):
    response = auth(api_client, campus_courses_leader).post(
        "/api/courses/", {"name": "Novo Curso", "campus_id": str(campus.id)}, format="json"
    )

    assert response.status_code == 201, response.data


def test_campus_courses_leader_cannot_create_church_wide_course(api_client, campus_courses_leader):
    # A church-wide course belongs to the church-wide level (manager / general leader).
    response = auth(api_client, campus_courses_leader).post(
        "/api/courses/", {"name": "Curso Geral"}, format="json"
    )

    assert response.status_code == 403


def test_campus_courses_leader_edits_only_their_campus_courses(
    api_client, campus_courses_leader, campus_course, other_campus_course
):
    client = auth(api_client, campus_courses_leader)

    assert client.patch(f"/api/courses/{campus_course.id}/", {"name": "Renomeado"}, format="json").status_code == 200
    # Outside their scope the course isn't even visible.
    assert client.patch(f"/api/courses/{other_campus_course.id}/", {"name": "X"}, format="json").status_code == 404


def test_campus_courses_leader_opens_class_of_church_wide_course_at_their_campus(
    api_client, campus_courses_leader, church_wide_course, campus
):
    response = auth(api_client, campus_courses_leader).post(
        "/api/course-classes/",
        {"course_id": str(church_wide_course.id), "campus_id": str(campus.id), "name": "Turma A"},
        format="json",
    )

    assert response.status_code == 201, response.data


def test_campus_courses_leader_cannot_open_class_at_another_campus(
    api_client, campus_courses_leader, church_wide_course, other_campus
):
    response = auth(api_client, campus_courses_leader).post(
        "/api/course-classes/",
        {"course_id": str(church_wide_course.id), "campus_id": str(other_campus.id), "name": "Turma B"},
        format="json",
    )

    assert response.status_code == 403


def test_campus_leader_keeps_managing_campus_courses(api_client, campus_leader, campus_course):
    # Appointing a courses leader doesn't take the campus leader's powers away.
    response = auth(api_client, campus_leader).patch(
        f"/api/courses/{campus_course.id}/", {"name": "Renomeado"}, format="json"
    )

    assert response.status_code == 200, response.data


# --- What the general courses leader can do ------------------------------------------


def test_general_courses_leader_sees_and_manages_courses_on_every_campus(
    api_client, courses_general_leader, campus_course, other_campus_course, church_wide_course
):
    client = auth(api_client, courses_general_leader)

    listed = {c["id"] for c in client.get("/api/courses/").data["results"]}
    assert {str(campus_course.id), str(other_campus_course.id), str(church_wide_course.id)} <= listed
    assert client.patch(f"/api/courses/{other_campus_course.id}/", {"name": "X"}, format="json").status_code == 200


def test_general_courses_leader_creates_church_wide_course(api_client, courses_general_leader):
    response = auth(api_client, courses_general_leader).post(
        "/api/courses/", {"name": "Curso Geral"}, format="json"
    )

    assert response.status_code == 201, response.data


# --- Visibility for everyone else --------------------------------------------------------


def test_regular_person_cannot_manage_courses(api_client, regular_person, campus_course, campus):
    client = auth(api_client, regular_person)

    assert client.post("/api/courses/", {"name": "X", "campus_id": str(campus.id)}, format="json").status_code == 403
    assert client.patch(f"/api/courses/{campus_course.id}/", {"name": "X"}, format="json").status_code in (403, 404)


def test_me_exposes_course_responsibilities(api_client, campus_courses_leader, campus):
    # The frontend uses /me to decide which screens to show.
    data = auth(api_client, campus_courses_leader).get("/api/auth/me/").data

    assert data["is_courses_general_leader"] is False
    assert [c["id"] for c in data["led_campuses_courses"]] == [str(campus.id)]
