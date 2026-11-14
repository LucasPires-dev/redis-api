"""A cell needs meeting day, time and audience: the API won't create or save one
without them, and an older cell missing any of them isn't active."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.churches.models import Cell, Season
from tests.conftest import CELL_REQUIRED_PAYLOAD, make_cell

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


@pytest.fixture
def season(church):
    return Season.objects.create(
        church=church, name="Atual", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=60)
    )


@pytest.fixture
def incomplete_cell(campus, season):
    # Created before the fields were required.
    return Cell.objects.create(campus=campus, season=season, name="GT Antigo", meeting_day=3)


def payload(campus, season, **overrides):
    return {
        "campus_id": str(campus.id),
        "season_id": str(season.id),
        "name": "GT Novo",
        **CELL_REQUIRED_PAYLOAD,
        **overrides,
    }


@pytest.mark.parametrize("field", ["meeting_day", "meeting_time", "target_audience"])
def test_create_requires_day_time_and_audience(api_client, manager, campus, season, field):
    api_client.force_authenticate(manager)
    data = payload(campus, season)
    del data[field]
    response = api_client.post("/api/cells/", data, format="json")
    assert response.status_code == 400
    assert list(response.data) == [field]


def test_create_rejects_blank_values(api_client, manager, campus, season):
    api_client.force_authenticate(manager)
    response = api_client.post(
        "/api/cells/",
        payload(campus, season, meeting_day=None, meeting_time=None, target_audience=""),
        format="json",
    )
    assert response.status_code == 400
    assert set(response.data) == {"meeting_day", "meeting_time", "target_audience"}


def test_complete_cell_is_created(api_client, manager, campus, season):
    api_client.force_authenticate(manager)
    response = api_client.post("/api/cells/", payload(campus, season), format="json")
    assert response.status_code == 201, response.data
    assert response.data["missing_fields"] == []
    assert response.data["target_audience"] == "todos"


def test_audience_must_be_a_cell_audience(api_client, manager, campus, season):
    api_client.force_authenticate(manager)
    response = api_client.post("/api/cells/", payload(campus, season, target_audience="som"), format="json")
    assert response.status_code == 400
    assert "target_audience" in response.data


def test_fields_cannot_be_cleared(api_client, manager, campus, season):
    cell = make_cell(campus=campus, season=season, name="GT")
    api_client.force_authenticate(manager)
    response = api_client.patch(f"/api/cells/{cell.id}/", {"meeting_time": None}, format="json")
    assert response.status_code == 400
    assert "meeting_time" in response.data


def test_editing_an_incomplete_cell_requires_filling_it_in(api_client, manager, incomplete_cell):
    api_client.force_authenticate(manager)
    url = f"/api/cells/{incomplete_cell.id}/"
    response = api_client.patch(url, {"name": "GT Renomeado"}, format="json")
    assert response.status_code == 400
    assert set(response.data) == {"meeting_time", "target_audience"}

    response = api_client.patch(
        url, {"name": "GT Renomeado", "meeting_time": "20:00", "target_audience": "jovens"}, format="json"
    )
    assert response.status_code == 200, response.data
    assert response.data["missing_fields"] == []


def test_incomplete_cell_is_flagged_and_not_active(api_client, manager, incomplete_cell):
    api_client.force_authenticate(manager)
    data = api_client.get(f"/api/cells/{incomplete_cell.id}/").data
    assert data["missing_fields"] == ["meeting_time", "target_audience"]
    assert incomplete_cell not in Cell.objects.active()
    names = [c["name"] for c in api_client.get("/api/cells/?active=true").data["results"]]
    assert "GT Antigo" not in names


def test_incomplete_cell_cannot_register_meetings(api_client, manager, incomplete_cell):
    api_client.force_authenticate(manager)
    response = api_client.post(
        "/api/cell-meetings/", {"cell_id": str(incomplete_cell.id), "date": str(TODAY)}, format="json"
    )
    assert response.status_code == 400
    assert "cell_id" in response.data


def test_incomplete_cell_cannot_be_renewed(api_client, manager, church, incomplete_cell, season):
    start = season.end_date + timedelta(days=1)
    next_season = Season.objects.create(
        church=church, name="Próxima", start_date=start, end_date=start + timedelta(days=90)
    )
    api_client.force_authenticate(manager)
    response = api_client.post(
        f"/api/cells/{incomplete_cell.id}/renew/", {"season_id": str(next_season.id)}, format="json"
    )
    assert response.status_code == 400
    assert not Cell.objects.filter(season=next_season).exists()


def test_migration_turns_empty_audience_into_todos(campus, season):
    migration = __import__(
        "apps.churches.migrations.0015_cell_audience_choices", fromlist=["empty_audience_to_todos"]
    )
    cell = Cell.objects.create(campus=campus, season=season, name="GT Todos", target_audience="")

    class Apps:
        @staticmethod
        def get_model(app_label, model_name):
            return Cell

    migration.empty_audience_to_todos(Apps, None)
    cell.refresh_from_db()
    assert cell.target_audience == "todos"
