from datetime import time

import pytest

from apps.churches.choices import Weekday
from apps.churches.models import Cell

pytestmark = pytest.mark.django_db

# Imported by module path because the filename starts with a digit.
schedule_migration = __import__(
    "apps.churches.migrations.0009_cell_structured_schedule", fromlist=["parse_schedule"]
)


@pytest.fixture
def cells(campus):
    return {
        "sexta": Cell.objects.create(
            campus=campus, name="Sexta", meeting_day=Weekday.FRIDAY, meeting_time=time(20, 0)
        ),
        "quarta_cedo": Cell.objects.create(
            campus=campus, name="Quarta cedo", meeting_day=Weekday.WEDNESDAY, meeting_time=time(19, 0)
        ),
        "quarta_tarde": Cell.objects.create(
            campus=campus, name="Quarta tarde", meeting_day=Weekday.WEDNESDAY, meeting_time=time(20, 30)
        ),
        # An older cell without a schedule (the API no longer creates these).
        "sem_dia": Cell.objects.create(campus=campus, name="Sem dia", target_audience="todos"),
    }


def names(response):
    return [c["name"] for c in response.data["results"]]


def test_serializes_meeting_day_and_time(api_client, manager, cells):
    api_client.force_authenticate(manager)
    data = api_client.get(f"/api/cells/{cells['sexta'].id}/").data
    assert data["meeting_day"] == 5
    assert data["meeting_day_display"] == "Sexta-feira"
    assert data["meeting_time"] == "20:00"


def test_filter_by_meeting_day(api_client, manager, cells):
    api_client.force_authenticate(manager)
    response = api_client.get("/api/cells/?meeting_day=3")
    assert sorted(names(response)) == ["Quarta cedo", "Quarta tarde"]


def test_filter_by_several_meeting_days(api_client, manager, cells):
    api_client.force_authenticate(manager)
    response = api_client.get("/api/cells/?meeting_day__in=3,5")
    assert sorted(names(response)) == ["Quarta cedo", "Quarta tarde", "Sexta"]


def test_filter_by_time_range(api_client, manager, cells):
    api_client.force_authenticate(manager)
    response = api_client.get("/api/cells/?meeting_time__gte=20:00")
    assert sorted(names(response)) == ["Quarta tarde", "Sexta"]


def test_order_by_weekly_schedule(api_client, manager, cells):
    api_client.force_authenticate(manager)
    response = api_client.get("/api/cells/?meeting_day__in=3,5&ordering=meeting_day,meeting_time")
    assert names(response) == ["Quarta cedo", "Quarta tarde", "Sexta"]


def test_update_schedule(api_client, manager, cells):
    api_client.force_authenticate(manager)
    cell = cells["sem_dia"]
    response = api_client.patch(
        f"/api/cells/{cell.id}/", {"meeting_day": 2, "meeting_time": "19:30"}, format="json"
    )
    assert response.status_code == 200
    cell.refresh_from_db()
    assert cell.meeting_day == Weekday.TUESDAY
    assert cell.meeting_time == time(19, 30)


def test_rejects_invalid_meeting_day(api_client, manager, cells):
    api_client.force_authenticate(manager)
    response = api_client.patch(f"/api/cells/{cells['sexta'].id}/", {"meeting_day": 8}, format="json")
    assert response.status_code == 400


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Quarta", (3, None)),
        ("quarta-feira", (3, None)),
        ("Sábado", (6, None)),
        ("SAB", (6, None)),
        ("Qua 19h30", (3, time(19, 30))),
        ("terça às 20h", (2, time(20, 0))),
        ("domingo 18:45", (7, time(18, 45))),
        ("toda semana", (None, None)),
    ],
)
def test_migration_parses_old_free_text(raw, expected):
    assert schedule_migration.parse_schedule(raw) == expected
