"""Registering and editing people through /api/people/.

Guards two security rules: nobody can promote someone to manager through the
API, and login credentials (email/password) can only be changed by the person
themselves — otherwise anyone allowed to edit a person (ministry/campus leader,
reconciliador) could take over their account.
"""

import pytest

from tests.conftest import Person, make_person

pytestmark = pytest.mark.django_db

PASSWORD = "Senha123!"


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def login(api_client, email, password):
    # Uses a fresh, unauthenticated request against the real login endpoint.
    api_client.force_authenticate(user=None)
    return api_client.post("/api/auth/login/", {"email": email, "password": password}, format="json")


@pytest.fixture
def brave_member(campus, brave):
    person = make_person(email="membro.brave@ilan.church", name="Membro Brave", campus=campus)
    brave.members.add(person)
    return person


@pytest.fixture
def convert(campus, reconciliador):
    from apps.reconciliation.models import Reconciliation

    person = make_person(email="convertido@ilan.church", name="Convertido", campus=campus)
    Reconciliation.objects.create(convert=person, reconciliador=reconciliador, campus=campus)
    return person


# --- Registering a person ------------------------------------------------------------


def test_leader_registers_a_person(api_client, leader, campus):
    response = auth(api_client, leader).post(
        "/api/people/",
        {"name": "Nova", "email": "nova@ilan.church", "password": PASSWORD, "campus_id": str(campus.id)},
        format="json",
    )

    assert response.status_code == 201, response.data
    assert Person.objects.get(email="nova@ilan.church").check_password(PASSWORD)


def test_registering_an_email_that_differs_only_by_case_is_rejected(api_client, leader, regular_person):
    # A clean 400 instead of a DB error when "Fulano@" already exists as "fulano@".
    response = auth(api_client, leader).post(
        "/api/people/",
        {"name": "Duplicada", "email": regular_person.email.upper(), "password": PASSWORD},
        format="json",
    )

    assert response.status_code == 400
    assert "email" in response.data


def test_registering_requires_a_password(api_client, leader):
    response = auth(api_client, leader).post(
        "/api/people/", {"name": "Nova", "email": "nova@ilan.church"}, format="json"
    )

    assert response.status_code == 400
    assert "password" in response.data


def test_password_is_never_returned(api_client, leader):
    response = auth(api_client, leader).post(
        "/api/people/", {"name": "Nova", "email": "nova@ilan.church", "password": PASSWORD}, format="json"
    )

    assert "password" not in response.data


def test_is_manager_in_payload_is_ignored_on_register(api_client, leader):
    # Privilege escalation guard: is_manager is read-only in the API — only the
    # Django Admin or management commands can grant it.
    auth(api_client, leader).post(
        "/api/people/",
        {"name": "Nova", "email": "nova@ilan.church", "password": PASSWORD, "is_manager": True},
        format="json",
    )

    assert not Person.objects.get(email="nova@ilan.church").is_manager


def test_person_cannot_make_themselves_manager(api_client, regular_person):
    auth(api_client, regular_person).patch(
        f"/api/people/{regular_person.id}/", {"is_manager": True}, format="json"
    )

    regular_person.refresh_from_db()
    assert not regular_person.is_manager


# --- Editing a person ----------------------------------------------------------------


def test_person_edits_their_own_profile(api_client, regular_person):
    response = auth(api_client, regular_person).patch(
        f"/api/people/{regular_person.id}/", {"phone": "11988887777"}, format="json"
    )

    assert response.status_code == 200, response.data
    regular_person.refresh_from_db()
    assert regular_person.phone == "11988887777"


def test_leader_edits_non_credential_fields_of_a_member(api_client, leader, brave_member):
    response = auth(api_client, leader).patch(
        f"/api/people/{brave_member.id}/", {"name": "Nome Corrigido"}, format="json"
    )

    assert response.status_code == 200, response.data
    brave_member.refresh_from_db()
    assert brave_member.name == "Nome Corrigido"


def test_patch_without_password_keeps_the_current_one(api_client, regular_person):
    auth(api_client, regular_person).patch(
        f"/api/people/{regular_person.id}/", {"name": "Novo Nome"}, format="json"
    )

    regular_person.refresh_from_db()
    assert regular_person.check_password(PASSWORD)


# --- Credentials: only the person themselves ---------------------------------------------


def test_person_changes_their_own_password(api_client, regular_person):
    response = auth(api_client, regular_person).patch(
        f"/api/people/{regular_person.id}/", {"password": "NovaSenha456!"}, format="json"
    )

    assert response.status_code == 200, response.data
    # Proves the new password was hashed and actually works for logging in.
    assert login(api_client, regular_person.email, "NovaSenha456!").status_code == 200
    assert login(api_client, regular_person.email, PASSWORD).status_code == 401


def test_person_changes_their_own_email(api_client, regular_person):
    response = auth(api_client, regular_person).patch(
        f"/api/people/{regular_person.id}/", {"email": "joao.novo@ilan.church"}, format="json"
    )

    assert response.status_code == 200, response.data
    regular_person.refresh_from_db()
    assert regular_person.email == "joao.novo@ilan.church"


# Every role that can edit someone else — none of them may touch credentials.
EDITORS = ["manager", "campus_leader", "leader"]


@pytest.mark.parametrize("editor_fixture", EDITORS)
@pytest.mark.parametrize(
    "payload", [{"password": "Invasor123!"}, {"email": "invasor@ilan.church"}], ids=["password", "email"]
)
def test_others_cannot_change_credentials(api_client, request, brave_member, editor_fixture, payload):
    # request.getfixturevalue resolves the fixture by name, so one test covers every role.
    editor = request.getfixturevalue(editor_fixture)

    response = auth(api_client, editor).patch(f"/api/people/{brave_member.id}/", payload, format="json")

    assert response.status_code == 403
    brave_member.refresh_from_db()
    assert brave_member.email == "membro.brave@ilan.church"
    assert brave_member.check_password(PASSWORD)


def test_reconciliador_updates_baptism_but_not_credentials(api_client, reconciliador, convert):
    # The reconciliador's edit access exists to record baptism, not to take over the account.
    allowed = auth(api_client, reconciliador).patch(
        f"/api/people/{convert.id}/", {"baptized": True}, format="json"
    )
    blocked = api_client.patch(f"/api/people/{convert.id}/", {"password": "Invasor123!"}, format="json")

    assert allowed.status_code == 200, allowed.data
    assert blocked.status_code == 403
    convert.refresh_from_db()
    assert convert.baptized
    assert convert.check_password(PASSWORD)


def test_resending_the_unchanged_email_is_allowed(api_client, leader, brave_member):
    # A full-form edit resends every field; an unchanged email is not a credential change.
    response = auth(api_client, leader).patch(
        f"/api/people/{brave_member.id}/",
        {"email": brave_member.email, "name": "Nome Corrigido"},
        format="json",
    )

    assert response.status_code == 200, response.data
