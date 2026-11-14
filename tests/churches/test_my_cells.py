"""?mine=true on /api/cells/: the personal "Minhas células" tab. A campus
leader sees every cell of the campus, but "mine" keeps only the ones they
lead or belong to."""

import pytest

from apps.churches.choices import LeadershipRole
from apps.churches.models import CellLeadership
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

URL = "/api/cells/"


def names(response):
    data = response.data
    rows = data["results"] if isinstance(data, dict) else data
    return sorted(row["name"] for row in rows)


def test_mine_keeps_led_and_member_cells_only(api_client, campus, campus_leader):
    led = make_cell(name="Liderada", campus=campus)
    CellLeadership.objects.create(cell=led, person=campus_leader, role=LeadershipRole.LEADER)
    member_of = make_cell(name="Participa", campus=campus)
    member_of.members.add(campus_leader)
    make_cell(name="Outra", campus=campus)
    api_client.force_authenticate(user=campus_leader)

    assert names(api_client.get(URL)) == ["Liderada", "Outra", "Participa"]
    assert names(api_client.get(URL, {"mine": "true"})) == ["Liderada", "Participa"]


def test_mine_is_empty_for_someone_without_a_cell(api_client, campus):
    make_cell(name="Alheia", campus=campus)
    person = make_person(email="sem.celula@ilan.church", campus=campus)
    api_client.force_authenticate(user=person)

    assert names(api_client.get(URL, {"mine": "true"})) == []
