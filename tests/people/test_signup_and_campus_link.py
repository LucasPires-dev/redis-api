import pytest
from django.core.cache import cache

from apps.churches.choices import JoinRequestStatus
from apps.churches.models import CampusJoinRequest
from tests.conftest import Person, make_person

pytestmark = pytest.mark.django_db

REGISTER_URL = "/api/auth/register/"
JOIN_REQUESTS_URL = "/api/campus-join-requests/"


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def unlinked(db):
    return make_person(email="nova@exemplo.com", name="Nova Pessoa")


@pytest.fixture
def invite_code(campus):
    campus.invite_code = "ABCD2345"
    campus.save()
    return campus.invite_code


def register(api_client, **overrides):
    payload = {"name": "Maria", "email": "Maria@Exemplo.com", "password": "UmaSenhaBoa!42"}
    payload.update(overrides)
    return api_client.post(REGISTER_URL, payload, format="json")


# --- Sign-up -----------------------------------------------------------------


def test_anyone_can_register_as_unlinked_member(api_client):
    response = register(api_client, is_manager=True, is_staff=True, campus_id="x", phone="11 9999")

    assert response.status_code == 201
    assert response.data["access"] and response.data["refresh"]
    person = Person.objects.get(email="maria@exemplo.com")
    assert person.campus_id is None
    assert not person.is_manager and not person.is_staff and not person.is_superuser
    assert person.phone == "11 9999"
    assert person.check_password("UmaSenhaBoa!42")


def test_registered_account_can_log_in(api_client):
    register(api_client)
    response = api_client.post(
        "/api/auth/login/", {"email": "maria@exemplo.com", "password": "UmaSenhaBoa!42"}, format="json"
    )
    assert response.status_code == 200


def test_register_rejects_existing_email_ignoring_case(api_client, regular_person):
    response = register(api_client, email="JOAO@ilan.church")
    assert response.status_code == 400
    assert "email" in response.data


def test_register_rejects_weak_password(api_client):
    response = register(api_client, password="123")
    assert response.status_code == 400
    assert "password" in response.data
    assert not Person.objects.filter(email="maria@exemplo.com").exists()


def test_register_with_invite_links_to_campus(api_client, campus, invite_code):
    response = register(api_client, invite_code=invite_code.lower())
    assert response.status_code == 201
    assert Person.objects.get(email="maria@exemplo.com").campus == campus


def test_register_with_invalid_invite_fails(api_client, campus):
    response = register(api_client, invite_code="NOPE0000")
    assert response.status_code == 400
    assert "invite_code" in response.data
    assert not Person.objects.filter(email="maria@exemplo.com").exists()


def test_register_is_rate_limited(api_client):
    for i in range(10):
        assert register(api_client, email=f"p{i}@exemplo.com").status_code == 201
    assert register(api_client, email="p10@exemplo.com").status_code == 429


# --- Invite codes ------------------------------------------------------------


def test_invite_lookup_is_public(api_client, campus, invite_code):
    response = api_client.get(f"/api/auth/invite/{invite_code}/")
    assert response.status_code == 200
    assert response.data["name"] == campus.name
    assert "leaders" not in response.data


def test_invite_lookup_unknown_code_is_404(api_client, campus):
    assert api_client.get("/api/auth/invite/NOPE0000/").status_code == 404


def test_campus_leader_creates_rotates_and_disables_invite(api_client, campus, campus_leader):
    api_client.force_authenticate(campus_leader)
    url = f"/api/campuses/{campus.id}/invite/"

    first = api_client.post(url).data["code"]
    assert len(first) == 8
    assert api_client.get(url).data["code"] == first

    second = api_client.post(url).data["code"]
    assert second != first
    assert api_client.get(f"/api/auth/invite/{first}/").status_code == 404

    assert api_client.delete(url).status_code == 204
    campus.refresh_from_db()
    assert campus.invite_code is None


@pytest.mark.parametrize("who", ["regular_person", "leader", "unlinked"])
def test_only_campus_leadership_manages_invite(api_client, campus, who, request):
    api_client.force_authenticate(request.getfixturevalue(who))
    assert api_client.post(f"/api/campuses/{campus.id}/invite/").status_code == 403


def test_join_by_invite_links_and_closes_pending_request(api_client, campus, other_campus, unlinked, invite_code):
    pending = CampusJoinRequest.objects.create(person=unlinked, campus=other_campus)
    api_client.force_authenticate(unlinked)

    response = api_client.post("/api/campuses/join/", {"code": invite_code}, format="json")

    assert response.status_code == 200
    unlinked.refresh_from_db()
    assert unlinked.campus == campus
    pending.refresh_from_db()
    assert pending.status == JoinRequestStatus.CANCELLED


def test_join_by_invite_rejects_person_of_another_campus(api_client, other_campus, invite_code):
    person = make_person(email="outro@ilan.church", name="Outro", campus=other_campus)
    api_client.force_authenticate(person)
    response = api_client.post("/api/campuses/join/", {"code": invite_code}, format="json")
    assert response.status_code == 400
    person.refresh_from_db()
    assert person.campus == other_campus


def test_join_by_invalid_code_fails(api_client, unlinked, campus):
    api_client.force_authenticate(unlinked)
    response = api_client.post("/api/campuses/join/", {"code": "NOPE0000"}, format="json")
    assert response.status_code == 400
    unlinked.refresh_from_db()
    assert unlinked.campus_id is None


# --- Join requests -----------------------------------------------------------


def test_unlinked_person_requests_and_campus_leader_approves(api_client, campus, unlinked, campus_leader):
    api_client.force_authenticate(unlinked)
    created = api_client.post(JOIN_REQUESTS_URL, {"campus_id": str(campus.id)}, format="json")
    assert created.status_code == 201
    assert created.data["status"] == JoinRequestStatus.PENDING

    me = api_client.get("/api/auth/me/").data
    assert me["campus"] is None
    assert me["pending_join_request"]["campus"]["id"] == str(campus.id)

    api_client.force_authenticate(campus_leader)
    listed = api_client.get(JOIN_REQUESTS_URL, {"status": "pending"}).data["results"]
    assert [r["id"] for r in listed] == [created.data["id"]]
    approved = api_client.post(f"{JOIN_REQUESTS_URL}{created.data['id']}/approve/")

    assert approved.status_code == 200
    assert approved.data["status"] == JoinRequestStatus.APPROVED
    assert approved.data["decided_by"]["id"] == str(campus_leader.id)
    unlinked.refresh_from_db()
    assert unlinked.campus == campus
    assert not unlinked.campus_leaderships.exists()


def test_cannot_request_twice_or_when_already_linked(api_client, campus, other_campus, unlinked, regular_person):
    api_client.force_authenticate(unlinked)
    assert api_client.post(JOIN_REQUESTS_URL, {"campus_id": str(campus.id)}, format="json").status_code == 201
    second = api_client.post(JOIN_REQUESTS_URL, {"campus_id": str(other_campus.id)}, format="json")
    assert second.status_code == 400

    api_client.force_authenticate(regular_person)
    linked = api_client.post(JOIN_REQUESTS_URL, {"campus_id": str(other_campus.id)}, format="json")
    assert linked.status_code == 400


def test_leader_of_another_campus_cannot_see_or_approve(api_client, campus, other_campus, unlinked):
    from apps.churches.choices import LeadershipRole
    from apps.churches.models import CampusLeadership

    other_leader = make_person(email="lider-outro@ilan.church", name="Líder Outro", campus=other_campus)
    CampusLeadership.objects.create(campus=other_campus, person=other_leader, role=LeadershipRole.LEADER)
    join_request = CampusJoinRequest.objects.create(person=unlinked, campus=campus)

    api_client.force_authenticate(other_leader)
    assert api_client.get(JOIN_REQUESTS_URL).data["results"] == []
    assert api_client.post(f"{JOIN_REQUESTS_URL}{join_request.id}/approve/").status_code == 404
    unlinked.refresh_from_db()
    assert unlinked.campus_id is None


def test_requester_cannot_approve_own_request(api_client, campus, unlinked):
    join_request = CampusJoinRequest.objects.create(person=unlinked, campus=campus)
    api_client.force_authenticate(unlinked)
    assert api_client.post(f"{JOIN_REQUESTS_URL}{join_request.id}/approve/").status_code == 403
    unlinked.refresh_from_db()
    assert unlinked.campus_id is None


def test_manager_rejects_and_answered_request_is_final(api_client, campus, unlinked, manager):
    join_request = CampusJoinRequest.objects.create(person=unlinked, campus=campus)
    api_client.force_authenticate(manager)

    rejected = api_client.post(f"{JOIN_REQUESTS_URL}{join_request.id}/reject/")
    assert rejected.status_code == 200
    assert rejected.data["status"] == JoinRequestStatus.REJECTED
    assert api_client.post(f"{JOIN_REQUESTS_URL}{join_request.id}/approve/").status_code == 400
    unlinked.refresh_from_db()
    assert unlinked.campus_id is None


def test_requester_cancels_pending_request(api_client, campus, unlinked, campus_leader):
    join_request = CampusJoinRequest.objects.create(person=unlinked, campus=campus)

    api_client.force_authenticate(campus_leader)
    assert api_client.post(f"{JOIN_REQUESTS_URL}{join_request.id}/cancel/").status_code == 403

    api_client.force_authenticate(unlinked)
    response = api_client.post(f"{JOIN_REQUESTS_URL}{join_request.id}/cancel/")
    assert response.status_code == 200
    assert response.data["status"] == JoinRequestStatus.CANCELLED
    assert api_client.get("/api/auth/me/").data["pending_join_request"] is None


# --- What an unlinked account can see and change -----------------------------


def test_unlinked_sees_campuses_without_leaders(api_client, campus, campus_leader, unlinked):
    api_client.force_authenticate(unlinked)
    campuses = api_client.get("/api/campuses/").data["results"]
    assert campuses and all("leaders" not in c for c in campuses)
    assert api_client.get(f"/api/campuses/{campus.id}/leaderships/").status_code == 403
    assert api_client.get("/api/ministry-general-leaderships/").status_code == 403


def test_linked_member_still_sees_campus_leaders(api_client, campus, campus_leader, regular_person):
    api_client.force_authenticate(regular_person)
    response = api_client.get(f"/api/campuses/{campus.id}/")
    assert [p["id"] for p in response.data["leaders"]] == [str(campus_leader.id)]


def test_person_cannot_set_own_campus(api_client, campus, unlinked):
    api_client.force_authenticate(unlinked)
    response = api_client.patch(f"/api/people/{unlinked.id}/", {"campus_id": str(campus.id)}, format="json")
    assert response.status_code == 403
    unlinked.refresh_from_db()
    assert unlinked.campus_id is None


def test_person_can_still_edit_own_profile(api_client, regular_person):
    api_client.force_authenticate(regular_person)
    response = api_client.patch(f"/api/people/{regular_person.id}/", {"phone": "11 98888"}, format="json")
    assert response.status_code == 200
