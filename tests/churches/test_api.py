import pytest

from tests.conftest import make_person

pytestmark = pytest.mark.django_db


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def test_leader_lists_only_their_ministry(api_client, leader, brave, other_ministry):
    resp = auth(api_client, leader).get("/api/ministries/")
    ids = [m["id"] for m in resp.data["results"]]
    assert ids == [str(brave.id)]


def test_only_manager_can_create_ministry(api_client, leader, campus):
    resp = auth(api_client, leader).post(
        "/api/ministries/", {"campus_id": str(campus.id), "kind": "jovens", "name": "Novo"}
    )
    assert resp.status_code == 403


def test_leader_and_co_leader_can_manage_members_equally(
    api_client, leader, co_leader, regular_person, brave
):
    resp = auth(api_client, leader).post(
        f"/api/ministries/{brave.id}/members/", {"person_id": str(regular_person.id)}
    )
    assert resp.status_code == 201

    resp = auth(api_client, co_leader).get(f"/api/ministries/{brave.id}/members/")
    assert resp.status_code == 200
    assert [p["id"] for p in resp.data] == [str(regular_person.id)]


def test_leader_cannot_remove_their_own_leadership(api_client, leader, brave):
    resp = auth(api_client, leader).delete(f"/api/ministries/{brave.id}/leaderships/{leader.id}/")
    assert resp.status_code == 403


def test_manager_can_remove_a_leadership(api_client, manager, leader, brave):
    resp = auth(api_client, manager).delete(f"/api/ministries/{brave.id}/leaderships/{leader.id}/")
    assert resp.status_code == 204


def test_regular_person_cannot_register_people(api_client, regular_person):
    resp = auth(api_client, regular_person).post(
        "/api/people/", {"name": "X", "email": "x@ilan.church", "password": "Senha123!"}
    )
    assert resp.status_code == 403


def test_regular_person_only_sees_themselves(api_client, regular_person, leader):
    resp = auth(api_client, regular_person).get("/api/people/")
    ids = [p["id"] for p in resp.data["results"]]
    assert ids == [str(regular_person.id)]


def test_manager_can_assign_campus_leadership(api_client, manager, campus, regular_person):
    resp = auth(api_client, manager).post(
        f"/api/campuses/{campus.id}/leaderships/", {"person_id": str(regular_person.id), "role": "leader"}
    )
    assert resp.status_code == 201


def test_non_manager_cannot_assign_campus_leadership(api_client, campus_leader, campus, regular_person):
    resp = auth(api_client, campus_leader).post(
        f"/api/campuses/{campus.id}/leaderships/", {"person_id": str(regular_person.id), "role": "leader"}
    )
    assert resp.status_code == 403


def test_campus_leader_cannot_remove_their_own_leadership(api_client, campus_leader, campus):
    resp = auth(api_client, campus_leader).delete(f"/api/campuses/{campus.id}/leaderships/{campus_leader.id}/")
    assert resp.status_code == 403


def test_manager_can_remove_a_campus_leadership(api_client, manager, campus_leader, campus):
    resp = auth(api_client, manager).delete(f"/api/campuses/{campus.id}/leaderships/{campus_leader.id}/")
    assert resp.status_code == 204


def test_campus_leader_sees_people_at_their_campus(api_client, campus_leader, regular_person, other_campus):
    other_person = make_person(email="outro-campus@ilan.church", name="Outro Campus", campus=other_campus)
    resp = auth(api_client, campus_leader).get("/api/people/")
    ids = {p["id"] for p in resp.data["results"]}
    assert str(regular_person.id) in ids
    assert str(other_person.id) not in ids
