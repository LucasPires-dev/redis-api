"""Person account creation (PersonManager) and the model rules with real consequences.

Every login account goes through create_user/create_superuser, so these tests
guard the safe defaults (hashed password, no privileges by accident) and the
email-as-login rules. Plain Django field behavior (ordering, max_length, UUID
generation) is deliberately not tested here.
"""

import pytest
from django.db import IntegrityError, transaction

from tests.conftest import Person

pytestmark = pytest.mark.django_db

PASSWORD = "Senha123!"


# --- create_user ---------------------------------------------------------------------


def test_create_user_hashes_the_password():
    person = Person.objects.create_user(email="ana@ilan.church", name="Ana", password=PASSWORD)

    # Both checks are needed: the first alone would also pass with a corrupted hash.
    assert person.password != PASSWORD
    assert person.check_password(PASSWORD)


def test_create_user_without_password_has_no_usable_login():
    # Members registered only as records (no access account) are created this way.
    person = Person.objects.create_user(email="membro@ilan.church", name="Membro")

    assert not person.has_usable_password()


# Runs once per value: an empty string and a missing email must both be rejected.
@pytest.mark.parametrize("email", ["", None])
def test_create_user_requires_email(email):
    with pytest.raises(ValueError):
        Person.objects.create_user(email=email, name="Sem Email", password=PASSWORD)


def test_create_user_defaults_grant_no_privileges():
    # Security guard: nobody should get staff/manager/superuser powers by accident.
    person = Person.objects.create_user(email="ana@ilan.church", name="Ana", password=PASSWORD)

    assert person.is_active
    assert not person.is_staff
    assert not person.is_manager
    assert not person.is_superuser


def test_create_user_lowercases_the_email_domain():
    # Django's normalize_email lowercases only the domain — the local part is kept
    # as typed (see the xfail tests below for why that is a problem).
    person = Person.objects.create_user(email="ana@ILAN.CHURCH", name="Ana", password=PASSWORD)

    assert person.email == "ana@ilan.church"


# --- create_superuser ----------------------------------------------------------------


def test_create_superuser_is_also_a_manager():
    # Not obvious: a superuser gets full API access too, not just the Django admin.
    person = Person.objects.create_superuser(email="root@ilan.church", name="Root", password=PASSWORD)

    assert person.is_superuser
    assert person.is_staff
    assert person.is_manager


def test_create_superuser_rejects_is_superuser_false():
    # Guards against creating a "superuser" that silently lacks the superuser flag.
    with pytest.raises(ValueError):
        Person.objects.create_superuser(
            email="root@ilan.church", name="Root", password=PASSWORD, is_superuser=False
        )


# --- Email as the login ----------------------------------------------------------------


def test_email_is_unique():
    Person.objects.create_user(email="ana@ilan.church", name="Ana", password=PASSWORD)

    # transaction.atomic() opens a savepoint: the IntegrityError rolls back only this
    # block, instead of breaking the test's whole transaction.
    with pytest.raises(IntegrityError), transaction.atomic():
        Person.objects.create_user(email="ana@ilan.church", name="Outra Ana", password=PASSWORD)


# The two xfail tests below describe the DESIRED behavior. strict=True makes pytest
# report an error once the bug is fixed (XPASS) — then just remove the decorator.
@pytest.mark.xfail(
    strict=True,
    reason="Known bug: normalize_email only lowercases the domain, so 'Ana@' and 'ana@' "
    "become two different accounts.",
)
def test_email_uniqueness_ignores_case():
    Person.objects.create_user(email="Ana@ilan.church", name="Ana", password=PASSWORD)

    with pytest.raises(IntegrityError), transaction.atomic():
        Person.objects.create_user(email="ana@ilan.church", name="Outra Ana", password=PASSWORD)


@pytest.mark.xfail(
    strict=True,
    reason="Known bug: login compares the email case-sensitively, so a phone that "
    "auto-capitalizes the first letter can't log in.",
)
def test_login_ignores_email_case(api_client):
    Person.objects.create_user(email="ana@ilan.church", name="Ana", password=PASSWORD)

    response = api_client.post(
        "/api/auth/login/", {"email": "Ana@ilan.church", "password": PASSWORD}, format="json"
    )

    assert response.status_code == 200


# --- Login and account status ---------------------------------------------------------
# Tested through the real login endpoint: is_active and an unusable password only take
# effect in the authentication flow, not on the model itself.


def test_active_person_can_log_in(api_client):
    Person.objects.create_user(email="ana@ilan.church", name="Ana", password=PASSWORD)

    response = api_client.post(
        "/api/auth/login/", {"email": "ana@ilan.church", "password": PASSWORD}, format="json"
    )

    assert response.status_code == 200
    assert "access" in response.data


def test_inactive_person_cannot_log_in(api_client):
    # Deactivating (instead of deleting) keeps history but must block access.
    Person.objects.create_user(
        email="ana@ilan.church", name="Ana", password=PASSWORD, is_active=False
    )

    response = api_client.post(
        "/api/auth/login/", {"email": "ana@ilan.church", "password": PASSWORD}, format="json"
    )

    assert response.status_code == 401


def test_person_without_password_cannot_log_in(api_client):
    Person.objects.create_user(email="membro@ilan.church", name="Membro")

    response = api_client.post(
        "/api/auth/login/", {"email": "membro@ilan.church", "password": ""}, format="json"
    )

    # An empty password may be refused by validation (400) or by authentication (401);
    # what matters is that no token is issued.
    assert response.status_code in (400, 401)


# --- Campus relationship -------------------------------------------------------------


def test_deleting_campus_keeps_its_people(campus):
    # on_delete=SET_NULL: member data must survive a campus being removed.
    person = Person.objects.create_user(
        email="ana@ilan.church", name="Ana", password=PASSWORD, campus=campus
    )

    campus.delete()

    # Reload from the DB — the in-memory object would still hold the old campus.
    person.refresh_from_db()
    assert person.campus is None
