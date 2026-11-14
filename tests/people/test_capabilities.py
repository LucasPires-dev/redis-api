"""Per-area capabilities on /api/auth/me/: independent flags that drive the tabs
of each area screen. A person with several roles gets every matching flag —
no role hides another."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import LeadershipRole
from apps.churches.models import CampusCellsLeadership, CellLeadership, Season
from apps.courses.choices import TeacherRole
from apps.courses.models import CampusCoursesLeadership, Course, CourseClass, CourseClassTeacher
from apps.production.models import EventProducerAssignment
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db
TODAY = timezone.localdate()


def me(api_client, person):
    api_client.force_authenticate(user=person)
    response = api_client.get("/api/auth/me/")
    assert response.status_code == 200
    return response.json()["capabilities"]


@pytest.fixture
def season(church):
    return Season.objects.create(
        church=church, name="Atual", start_date=TODAY - timedelta(days=10), end_date=TODAY + timedelta(days=90)
    )


@pytest.fixture
def cell(campus, season):
    return make_cell(campus=campus, season=season, name="GT Vida")


def test_plain_member_has_no_management_flags(api_client, regular_person):
    caps = me(api_client, regular_person)
    assert caps == {
        "cells": {"lead": False, "coordinate": False},
        "courses": {"teach": False, "coordinate": False, "coordinate_all": False},
        "events": {"create": False, "produce": False, "production": False},
    }


def test_manager_coordinates_everything(api_client, manager):
    caps = me(api_client, manager)
    assert caps["cells"]["coordinate"]
    assert caps["courses"]["coordinate"] and caps["courses"]["coordinate_all"]
    assert caps["events"]["create"] and caps["events"]["produce"] and caps["events"]["production"]


def test_cell_leader_who_coordinates_keeps_both(api_client, campus, cell):
    person = make_person(email="lider.coord@ilan.church", name="Líder e Coord", campus=campus)
    CellLeadership.objects.create(cell=cell, person=person, role=LeadershipRole.LEADER)
    CampusCellsLeadership.objects.create(campus=campus, person=person, role=LeadershipRole.LEADER)
    caps = me(api_client, person)
    assert caps["cells"] == {"lead": True, "coordinate": True}


def test_campus_leader_coordinates_campus_cells_and_courses(api_client, campus_leader):
    caps = me(api_client, campus_leader)
    assert caps["cells"]["coordinate"]
    assert caps["courses"]["coordinate"] and not caps["courses"]["coordinate_all"]
    assert caps["events"]["create"] and not caps["events"]["produce"]


def test_teacher_who_coordinates_keeps_both(api_client, church, campus):
    person = make_person(email="prof.coord@ilan.church", name="Prof e Coord", campus=campus)
    CampusCoursesLeadership.objects.create(campus=campus, person=person, role=LeadershipRole.LEADER)
    course = Course.objects.create(church=church, campus=campus, name="Fundamentos")
    course_class = CourseClass.objects.create(course=course, campus=campus, name="Turma A")
    CourseClassTeacher.objects.create(course_class=course_class, person=person, role=TeacherRole.TEACHER)
    caps = me(api_client, person)
    assert caps["courses"]["teach"] and caps["courses"]["coordinate"]


def test_campus_producer_produces(api_client, campus):
    person = make_person(email="prod@ilan.church", name="Produtor", campus=campus)
    EventProducerAssignment.objects.create(person=person, campus=campus)
    caps = me(api_client, person)
    assert caps["events"] == {"create": True, "produce": True, "production": True}


def test_ministry_member_works_in_production_without_creating(api_client, media_member):
    caps = me(api_client, media_member)
    assert caps["events"] == {"create": False, "produce": False, "production": True}


def test_ministry_leader_creates_ministry_events(api_client, leader):
    caps = me(api_client, leader)
    assert caps["events"]["create"] and caps["events"]["production"] and not caps["events"]["produce"]
