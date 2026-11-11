"""Choosing general (church-wide) leaders — the top of the access hierarchy.

The institution's leaders (manager) choose a general leader per area: each
ministry kind, cells and courses. Those leaders then manage their area on every
campus (covered in each area's own tests). Nobody else may choose them — not
even a general leader choosing a peer.
"""

import pytest

from apps.churches.choices import MinistryKind
from apps.churches.models import CellGeneralLeadership, MinistryGeneralLeadership
from apps.churches.permissions import can_coordinate_cells, general_led_kinds
from apps.courses.models import CourseGeneralLeadership
from apps.courses.permissions import is_courses_general_leader

pytestmark = pytest.mark.django_db


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def ministry_url(kind):
    return f"/api/ministry-general-leaderships/{kind}/leaders/"


# --- Ministry general leaders (per ministry kind) -------------------------------------


def test_manager_chooses_ministry_general_leader(api_client, manager, regular_person):
    response = auth(api_client, manager).post(
        ministry_url("jovens"), {"person_id": str(regular_person.id)}, format="json"
    )

    assert response.status_code == 201, response.data
    assert [p["id"] for p in response.data] == [str(regular_person.id)]
    # The appointment takes effect: they now lead Jovens on every campus.
    assert general_led_kinds(regular_person) == {MinistryKind.JOVENS}


def test_ministry_kind_can_have_more_than_one_general_leader(api_client, manager, regular_person, leader):
    client = auth(api_client, manager)
    client.post(ministry_url("jovens"), {"person_id": str(regular_person.id)}, format="json")
    client.post(ministry_url("jovens"), {"person_id": str(leader.id)}, format="json")

    leaders = MinistryGeneralLeadership.objects.get(kind=MinistryKind.JOVENS).leaders.all()
    assert set(leaders) == {regular_person, leader}


def test_manager_removes_ministry_general_leader(api_client, manager, regular_person):
    MinistryGeneralLeadership.objects.create(kind=MinistryKind.JOVENS).leaders.add(regular_person)

    response = auth(api_client, manager).delete(f"{ministry_url('jovens')}{regular_person.id}/")

    assert response.status_code == 204
    assert general_led_kinds(regular_person) == set()


def test_removing_someone_who_is_not_general_leader_returns_404(api_client, manager, regular_person):
    response = auth(api_client, manager).delete(f"{ministry_url('jovens')}{regular_person.id}/")

    assert response.status_code == 404


def test_invalid_ministry_kind_is_rejected(api_client, manager, regular_person):
    response = auth(api_client, manager).post(
        ministry_url("nao_existe"), {"person_id": str(regular_person.id)}, format="json"
    )

    assert response.status_code == 400


def test_general_leaders_are_listed_for_everyone(api_client, regular_person, leader):
    # The frontend shows "líder geral" on the ministry screens to any member.
    MinistryGeneralLeadership.objects.create(kind=MinistryKind.JOVENS).leaders.add(leader)

    response = auth(api_client, regular_person).get("/api/ministry-general-leaderships/")

    assert response.status_code == 200
    jovens = next(g for g in response.data["results"] if g["kind"] == "jovens")
    assert [p["id"] for p in jovens["leaders"]] == [str(leader.id)]


# --- Cells and courses general leaders (per church) -------------------------------------


@pytest.mark.parametrize(
    "area, model, is_general",
    [
        ("cells", CellGeneralLeadership, can_coordinate_cells),
        ("courses", CourseGeneralLeadership, is_courses_general_leader),
    ],
)
def test_manager_chooses_and_removes_area_general_leader(
    api_client, manager, regular_person, church, area, model, is_general
):
    client = auth(api_client, manager)
    url = f"/api/churches/{church.id}/{area}-general-leaders/"

    added = client.post(url, {"person_id": str(regular_person.id)}, format="json")
    assert added.status_code == 201, added.data
    assert is_general(regular_person)
    assert [p["id"] for p in client.get(url).data] == [str(regular_person.id)]

    removed = client.delete(f"{url}{regular_person.id}/")
    assert removed.status_code == 204
    assert not model.objects.get(church=church).leaders.filter(pk=regular_person.pk).exists()


# --- Only the manager chooses general leaders -------------------------------------------


@pytest.mark.parametrize("chooser", ["campus_leader", "leader", "regular_person"])
@pytest.mark.parametrize("area", ["ministry", "cells", "courses"])
def test_only_the_manager_chooses_general_leaders(api_client, request, church, other_campus, chooser, area):
    from tests.conftest import make_person

    # request.getfixturevalue resolves the fixture by name, so one test covers every role.
    person = request.getfixturevalue(chooser)
    target = make_person(email="alvo@ilan.church", name="Alvo", campus=other_campus)
    url = ministry_url("jovens") if area == "ministry" else f"/api/churches/{church.id}/{area}-general-leaders/"

    response = auth(api_client, person).post(url, {"person_id": str(target.id)}, format="json")

    assert response.status_code == 403


def test_general_leader_cannot_choose_a_peer(api_client, regular_person, leader):
    # A general leader manages their area, but doesn't pick other general leaders.
    MinistryGeneralLeadership.objects.create(kind=MinistryKind.JOVENS).leaders.add(regular_person)

    response = auth(api_client, regular_person).post(
        ministry_url("jovens"), {"person_id": str(leader.id)}, format="json"
    )

    assert response.status_code == 403


# --- People pickers used to appoint ----------------------------------------------------


def test_manager_searches_people_across_the_church(api_client, manager, church, other_campus):
    from tests.conftest import make_person

    make_person(email="bia.fora@ilan.church", name="Bia de Fora", campus=other_campus)

    response = auth(api_client, manager).get(f"/api/churches/{church.id}/people-search/?q=Bia")

    assert response.status_code == 200
    assert [p["name"] for p in response.data] == ["Bia de Fora"]


def test_only_manager_searches_the_whole_church(api_client, campus_leader, church):
    response = auth(api_client, campus_leader).get(f"/api/churches/{church.id}/people-search/?q=Bia")

    assert response.status_code == 403


def test_general_ministry_leader_searches_people_of_any_campus(api_client, regular_person, other_campus):
    # They appoint that ministry's local leaders on every campus, so they need the picker.
    from tests.conftest import make_person

    MinistryGeneralLeadership.objects.create(kind=MinistryKind.JOVENS).leaders.add(regular_person)
    make_person(email="bia.fora@ilan.church", name="Bia de Fora", campus=other_campus)

    response = auth(api_client, regular_person).get(f"/api/campuses/{other_campus.id}/people-search/?q=Bia")

    assert response.status_code == 200
    assert [p["name"] for p in response.data] == ["Bia de Fora"]
