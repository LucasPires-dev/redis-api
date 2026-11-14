"""The seed gives one account per access level, so the hierarchy can be tried
locally without wiring it by hand. These tests pin down who gets what."""

from io import StringIO

import pytest
from django.core.management import call_command

from apps.churches.models import Campus
from apps.churches.permissions import (
    can_coordinate_campus_cells,
    can_manage_campus,
    general_led_kinds,
    is_cells_general_leader,
)
from apps.courses.permissions import can_coordinate_campus_courses, is_courses_general_leader
from apps.people.models import Person
from apps.production.permissions import is_event_producer, is_general_event_producer

pytestmark = pytest.mark.django_db

PASSWORD = "TrocarSenha123!"


@pytest.fixture
def seeded():
    call_command("seed_initial_church", stdout=StringIO())
    return Campus.objects.get(name="Arena ILAN")


def person(email):
    return Person.objects.get(email=email)


def test_every_account_can_log_in(seeded):
    assert Person.objects.count() == 16
    assert all(p.check_password(PASSWORD) for p in Person.objects.all())


def test_manager_and_campus_leaders(seeded):
    assert person("gestor@ilan.church").is_manager
    assert can_manage_campus(person("lider.campus1@ilan.church"), seeded)
    assert can_manage_campus(person("lider.campus2@ilan.church"), seeded)


def test_general_responsibles(seeded):
    assert "adolescentes" in general_led_kinds(person("geral.adolescentes@ilan.church"))
    assert is_cells_general_leader(person("geral.celulas@ilan.church"))
    assert is_courses_general_leader(person("geral.cursos@ilan.church"))
    assert is_general_event_producer(person("geral.eventos@ilan.church"))


def test_local_responsibles_are_not_general(seeded):
    cells = person("celulas.arena@ilan.church")
    courses = person("cursos.arena@ilan.church")
    producer = person("eventos.arena@ilan.church")

    assert can_coordinate_campus_cells(cells, seeded) and not is_cells_general_leader(cells)
    assert can_coordinate_campus_courses(courses, seeded) and not is_courses_general_leader(courses)
    assert is_event_producer(producer, seeded) and not is_general_event_producer(producer)


def test_plain_member_has_no_role(seeded):
    member = person("membro@ilan.church")

    assert not member.is_manager
    assert not can_manage_campus(member, seeded)
    assert not general_led_kinds(member)
    assert not is_event_producer(member, seeded)


def test_rerunning_is_idempotent_and_keeps_changed_passwords(seeded):
    manager = person("gestor@ilan.church")
    manager.set_password("MinhaSenhaNova!")
    manager.save()

    call_command("seed_initial_church", stdout=StringIO())

    assert Person.objects.count() == 16
    assert person("gestor@ilan.church").check_password("MinhaSenhaNova!")
