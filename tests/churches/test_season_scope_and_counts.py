"""Seasons belong to a church: people only list their own church's seasons and
a general cells leader only writes their own church's. Plus the cell's
participant_count, which counts leaders that aren't in `members` too."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.choices import LeadershipRole
from apps.churches.models import CellGeneralLeadership, CellLeadership, Church, Season
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()
URL = "/api/seasons/"


def rows(response):
    data = response.data
    return data["results"] if isinstance(data, dict) else data


@pytest.fixture
def other_church(db):
    return Church.objects.create(name="Outra Igreja")


@pytest.fixture
def seasons(church, other_church):
    own = Season.objects.create(
        church=church, name="Nossa", start_date=TODAY - timedelta(days=5), end_date=TODAY + timedelta(days=60)
    )
    other = Season.objects.create(
        church=other_church, name="Deles", start_date=TODAY - timedelta(days=5), end_date=TODAY + timedelta(days=60)
    )
    return own, other


@pytest.fixture
def cells_leader(church, campus):
    person = make_person(email="geral.celulas@ilan.church", name="Geral Células", campus=campus)
    CellGeneralLeadership.objects.create(church=church).leaders.add(person)
    return person


def test_people_list_only_their_church_seasons(api_client, regular_person, seasons):
    api_client.force_authenticate(user=regular_person)
    assert [s["name"] for s in rows(api_client.get(URL))] == ["Nossa"]


def test_manager_lists_every_church(api_client, manager, seasons):
    api_client.force_authenticate(user=manager)
    assert sorted(s["name"] for s in rows(api_client.get(URL))) == ["Deles", "Nossa"]


def test_general_leader_cannot_touch_another_church_season(api_client, cells_leader, seasons, other_church):
    _, other = seasons
    api_client.force_authenticate(user=cells_leader)

    # Not even listed, so editing it is a 404 rather than a silent change.
    assert api_client.patch(f"{URL}{other.id}/", {"name": "X"}, format="json").status_code == 404
    created = api_client.post(
        URL,
        {
            "church_id": str(other_church.id),
            "name": "Invadida",
            "start_date": str(TODAY + timedelta(days=100)),
            "end_date": str(TODAY + timedelta(days=200)),
        },
        format="json",
    )
    assert created.status_code == 403
    assert not Season.objects.filter(name="Invadida").exists()


def test_general_leader_edits_own_church_season(api_client, cells_leader, seasons):
    own, _ = seasons
    api_client.force_authenticate(user=cells_leader)
    response = api_client.patch(f"{URL}{own.id}/", {"name": "Renomeada"}, format="json")
    assert response.status_code == 200, response.data


def test_participant_count_includes_leaders_outside_members(api_client, manager, campus):
    cell = make_cell(name="Contagem", campus=campus)
    leader = make_person(email="lider.contagem@ilan.church", campus=campus)
    member = make_person(email="membro.contagem@ilan.church", campus=campus)
    both = make_person(email="ambos.contagem@ilan.church", campus=campus)
    CellLeadership.objects.create(cell=cell, person=leader, role=LeadershipRole.LEADER)
    CellLeadership.objects.create(cell=cell, person=both, role=LeadershipRole.CO_LEADER)
    cell.members.add(member, both)
    api_client.force_authenticate(user=manager)

    data = api_client.get(f"/api/cells/{cell.id}/").data

    assert data["member_count"] == 2
    assert data["participant_count"] == 3
