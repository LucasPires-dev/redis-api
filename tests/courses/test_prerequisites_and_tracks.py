"""Prerequisites between courses and growth tracks."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.courses.choices import EnrollmentStatus
from apps.courses.models import Course, CourseClass, CourseGeneralLeadership, CourseTrack, Enrollment
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def open_class(course, campus, name="Turma 1"):
    return CourseClass.objects.create(
        course=course, campus=campus, name=name, self_enrollment=True, end_date=TODAY + timedelta(days=30)
    )


def complete(person, course, campus):
    done = CourseClass.objects.create(course=course, campus=campus, name=f"Antiga {course.name}")
    Enrollment.objects.create(course_class=done, person=person, status=EnrollmentStatus.COMPLETED)


@pytest.fixture
def fundamentos(church):
    return Course.objects.create(church=church, name="Fundamentos")


@pytest.fixture
def lideranca(church, fundamentos):
    course = Course.objects.create(church=church, name="Liderança")
    course.prerequisites.add(fundamentos)
    return course


@pytest.fixture
def general_leader(church, campus):
    person = make_person(email="geral.cursos@ilan.church", name="Geral", campus=campus)
    CourseGeneralLeadership.objects.create(church=church).leaders.add(person)
    return person


@pytest.fixture
def member(campus):
    return make_person(email="membro.trilha@ilan.church", name="Membro Trilha", campus=campus)


# ── Prerequisites ────────────────────────────────────────────────────────────


def test_member_cannot_enroll_without_the_prerequisite(api_client, lideranca, campus, member):
    cls = open_class(lideranca, campus)

    response = auth(api_client, member).post(f"/api/course-classes/{cls.id}/enroll/", {}, format="json")

    assert response.status_code == 400
    assert "Fundamentos" in str(response.data["detail"])
    assert response.data["missing_prerequisites"][0]["name"] == "Fundamentos"
    assert not Enrollment.objects.filter(person=member).exists()


def test_member_enrolls_after_completing_it(api_client, lideranca, fundamentos, campus, member):
    complete(member, fundamentos, campus)
    cls = open_class(lideranca, campus)

    response = auth(api_client, member).post(f"/api/course-classes/{cls.id}/enroll/", {}, format="json")

    assert response.status_code == 201, response.data


def test_open_classes_say_what_is_missing(api_client, lideranca, fundamentos, campus, member):
    open_class(lideranca, campus, "Turma Liderança")

    rows = auth(api_client, member).get("/api/course-classes/open/").data
    row = next(r for r in rows if r["name"] == "Turma Liderança")
    assert [c["name"] for c in row["prerequisites"]] == ["Fundamentos"]
    assert [c["name"] for c in row["missing_prerequisites"]] == ["Fundamentos"]

    complete(member, fundamentos, campus)
    rows = api_client.get("/api/course-classes/open/").data
    assert next(r for r in rows if r["name"] == "Turma Liderança")["missing_prerequisites"] == []


def test_coordinator_is_warned_and_may_override(api_client, courses_leader, lideranca, campus, member):
    cls = open_class(lideranca, campus)
    url = f"/api/course-classes/{cls.id}/enrollments/"
    client = auth(api_client, courses_leader)

    blocked = client.post(url, {"person_id": str(member.id)}, format="json")
    assert blocked.status_code == 400
    assert blocked.data["missing_prerequisites"][0]["name"] == "Fundamentos"

    allowed = client.post(url, {"person_id": str(member.id), "override_prerequisites": True}, format="json")
    assert allowed.status_code == 201, allowed.data


def test_prerequisites_are_set_on_the_course(api_client, general_leader, church, fundamentos):
    client = auth(api_client, general_leader)

    created = client.post(
        "/api/courses/", {"name": "Discipulado", "prerequisite_ids": [str(fundamentos.id)]}, format="json"
    )

    assert created.status_code == 201, created.data
    assert [c["name"] for c in created.data["prerequisites"]] == ["Fundamentos"]
    cleared = client.patch(f"/api/courses/{created.data['id']}/", {"prerequisite_ids": []}, format="json")
    assert cleared.data["prerequisites"] == []


def test_prerequisites_reject_self_and_cycles(api_client, general_leader, fundamentos, lideranca):
    client = auth(api_client, general_leader)

    itself = client.patch(f"/api/courses/{fundamentos.id}/", {"prerequisite_ids": [str(fundamentos.id)]}, format="json")
    assert itself.status_code == 400
    # Liderança already requires Fundamentos: Fundamentos can't require Liderança.
    cycle = client.patch(f"/api/courses/{fundamentos.id}/", {"prerequisite_ids": [str(lideranca.id)]}, format="json")
    assert cycle.status_code == 400
    assert "ciclo" in str(cycle.data["prerequisite_ids"])


# ── Tracks ───────────────────────────────────────────────────────────────────


@pytest.fixture
def track(church, fundamentos, lideranca):
    membresia = Course.objects.create(church=church, name="Membresia")
    track = CourseTrack.objects.create(church=church, name="Trilha de crescimento")
    for order, course in enumerate([membresia, fundamentos, lideranca], start=1):
        track.steps.create(course=course, order=order)
    return track


def test_general_coordination_keeps_tracks(api_client, general_leader, fundamentos, lideranca):
    client = auth(api_client, general_leader)

    created = client.post(
        "/api/course-tracks/",
        {"name": "Liderança", "course_ids": [str(fundamentos.id), str(lideranca.id)]},
        format="json",
    )
    assert created.status_code == 201, created.data
    assert [s["course"]["name"] for s in created.data["steps"]] == ["Fundamentos", "Liderança"]

    reordered = client.patch(
        f"/api/course-tracks/{created.data['id']}/",
        {"course_ids": [str(lideranca.id), str(fundamentos.id)]},
        format="json",
    )
    assert [s["course"]["name"] for s in reordered.data["steps"]] == ["Liderança", "Fundamentos"]

    duplicated = client.post(
        "/api/course-tracks/", {"name": "Outra", "course_ids": [str(fundamentos.id)] * 2}, format="json"
    )
    assert duplicated.status_code == 400
    same_name = client.post("/api/course-tracks/", {"name": "liderança", "course_ids": [str(fundamentos.id)]},
                            format="json")
    assert same_name.status_code == 400


def test_members_and_local_coordination_only_read_tracks(api_client, member, courses_leader, track, fundamentos):
    for person in (member, courses_leader):
        client = auth(api_client, person)
        assert client.get("/api/course-tracks/").status_code == 200
        response = client.post("/api/course-tracks/", {"name": "Minha", "course_ids": [str(fundamentos.id)]},
                               format="json")
        assert response.status_code == 403
        assert client.delete(f"/api/course-tracks/{track.id}/").status_code == 403


def test_member_sees_their_progress(api_client, member, track, fundamentos, lideranca, campus):
    membresia = Course.objects.get(name="Membresia")
    complete(member, membresia, campus)
    current = CourseClass.objects.create(course=fundamentos, campus=campus, name="Fundamentos agora")
    Enrollment.objects.create(course_class=current, person=member)
    open_class(lideranca, campus, "Liderança aberta")

    (listed,) = auth(api_client, member).get("/api/course-tracks/").data["results"]

    progress = listed["my_progress"]
    assert (progress["completed"], progress["total"]) == (1, 3)
    assert [s["state"] for s in progress["steps"]] == ["completed", "in_progress", "not_started"]
    assert progress["steps"][2]["open_classes"] == 1
    assert progress["next_course"]["name"] == "Fundamentos"
    assert listed["can_manage"] is False
