"""Who sees whom in /api/people/ (PersonViewSet.get_queryset).

This is the first access layer: a person outside the queryset doesn't appear in
the list and returns 404 on detail, regardless of PersonPermission.
"""

import pytest

from tests.conftest import make_person

pytestmark = pytest.mark.django_db


def auth(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


def listed_ids(api_client, person):
    response = auth(api_client, person).get("/api/people/")
    assert response.status_code == 200
    return [p["id"] for p in response.data["results"]]


@pytest.fixture
def brave_member(campus, brave):
    person = make_person(email="membro.brave@ilan.church", name="Membro Brave", campus=campus)
    brave.members.add(person)
    return person


@pytest.fixture
def other_campus_person(other_campus):
    return make_person(email="fora@ilan.church", name="De Fora", campus=other_campus)


def test_manager_sees_everyone(api_client, manager, brave_member, other_campus_person):
    ids = listed_ids(api_client, manager)

    assert {str(brave_member.id), str(other_campus_person.id)} <= set(ids)


def test_regular_person_sees_only_themselves(api_client, regular_person, brave_member):
    assert listed_ids(api_client, regular_person) == [str(regular_person.id)]


def test_regular_person_gets_404_on_someone_else(api_client, regular_person, brave_member):
    # 404, not 403: the API doesn't even confirm the person exists.
    response = auth(api_client, regular_person).get(f"/api/people/{brave_member.id}/")

    assert response.status_code == 404


def test_ministry_leader_sees_their_members_only(
    api_client, leader, brave_member, other_ministry, other_campus_person
):
    other_ministry.members.add(other_campus_person)

    ids = listed_ids(api_client, leader)

    assert str(brave_member.id) in ids
    assert str(leader.id) in ids
    assert str(other_campus_person.id) not in ids


def test_campus_leader_sees_people_at_their_campus_only(
    api_client, campus_leader, regular_person, other_campus_person
):
    ids = listed_ids(api_client, campus_leader)

    assert str(regular_person.id) in ids
    assert str(other_campus_person.id) not in ids


def test_reconciliador_sees_only_their_own_convert(api_client, campus, reconciliador, other_campus):
    from apps.reconciliation.models import Reconciliation

    mine = make_person(email="meu@ilan.church", name="Meu Convertido", campus=campus)
    Reconciliation.objects.create(convert=mine, reconciliador=reconciliador, campus=campus)

    other_reconciliador = make_person(email="outro.rec@ilan.church", name="Outro Rec", campus=campus)
    theirs = make_person(email="dele@ilan.church", name="Convertido Dele", campus=campus)
    Reconciliation.objects.create(convert=theirs, reconciliador=other_reconciliador, campus=campus)

    ids = listed_ids(api_client, reconciliador)

    assert str(mine.id) in ids
    assert str(theirs.id) not in ids
    assert auth(api_client, reconciliador).get(f"/api/people/{theirs.id}/").status_code == 404


def test_person_in_two_led_ministries_is_listed_once(api_client, leader, campus, brave, brave_member):
    # The ministries join would repeat the person without .distinct() in get_queryset.
    from apps.churches.choices import LeadershipRole, MinistryKind
    from apps.churches.models import Ministry, MinistryLeadership

    second = Ministry.objects.create(campus=campus, kind=MinistryKind.JOVENS, name="Jovens")
    MinistryLeadership.objects.create(ministry=second, person=leader, role=LeadershipRole.LEADER)
    second.members.add(brave_member)

    ids = listed_ids(api_client, leader)

    assert ids.count(str(brave_member.id)) == 1
