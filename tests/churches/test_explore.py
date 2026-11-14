"""Explorar: what anyone sees without logging in. A public cell shows only its
church, weekday and time; members-only things never show up."""

from datetime import time, timedelta

import pytest
from django.utils import timezone

from apps.courses.models import Course, CourseClass
from apps.payments.models import EventTicketConfig
from apps.production.models import Event
from tests.conftest import make_cell

pytestmark = pytest.mark.django_db

URL = "/api/public/explore/"
TODAY = timezone.localdate()


@pytest.fixture
def anonymous(api_client):
    api_client.force_authenticate(user=None)
    return api_client


@pytest.fixture
def public_cell(campus):
    return make_cell(
        campus=campus, name="GT Casa da Ana", address="Rua das Flores, 10", visibility="public",
        meeting_day=5, meeting_time=time(20, 0),
    )


@pytest.fixture
def public_class(church, campus):
    course = Course.objects.create(church=church, name="Escola Bíblica")
    return CourseClass.objects.create(
        course=course, campus=campus, name="Turma Sábado", self_enrollment=True, visibility="public",
        end_date=TODAY + timedelta(days=30),
    )


@pytest.fixture
def public_event(campus):
    event = Event.objects.create(
        title="Conferência Aberta", campus=campus, visibility="public", starts_at=timezone.now() + timedelta(days=5)
    )
    EventTicketConfig.objects.create(event=event, price_cents=2500)
    return event


def test_explore_lists_only_public_things(anonymous, campus, church, public_cell, public_class, public_event):
    make_cell(campus=campus, name="GT Fechado")
    Event.objects.create(title="Só membros", campus=campus, starts_at=timezone.now() + timedelta(days=2))
    Event.objects.create(
        title="Já passou", campus=campus, visibility="public", starts_at=timezone.now() - timedelta(days=2)
    )
    data = anonymous.get(URL).data
    assert [e["title"] for e in data["events"]] == ["Conferência Aberta"]
    assert data["events"][0]["registration"] == {"price_from_cents": 2500}
    assert [c["course_name"] for c in data["classes"]] == ["Escola Bíblica"]
    assert [c["id"] for c in data["cells"]] == [str(public_cell.id)]
    assert data["churches"] == [{"id": str(church.id), "name": church.name}]


def test_a_public_cell_shows_only_church_day_and_time(anonymous, public_cell, church):
    for row in (anonymous.get(URL).data["cells"][0], anonymous.get(f"/api/public/cells/{public_cell.id}/").data):
        assert set(row) == {"id", "church", "meeting_day", "meeting_day_display", "meeting_time"}
        assert row["church"]["name"] == church.name and row["meeting_time"] == "20:00"
        assert "Ana" not in str(row) and "Flores" not in str(row)


def test_members_only_details_are_not_found(anonymous, campus, church):
    cell = make_cell(campus=campus, name="GT Fechado")
    course = Course.objects.create(church=church, name="Interno")
    closed = CourseClass.objects.create(course=course, campus=campus, name="T", self_enrollment=True)
    assert anonymous.get(f"/api/public/cells/{cell.id}/").status_code == 404
    assert anonymous.get(f"/api/public/classes/{closed.id}/").status_code == 404


def test_public_class_page(anonymous, public_class, church):
    res = anonymous.get(f"/api/public/classes/{public_class.id}/")
    assert res.status_code == 200
    assert res.data["course_name"] == "Escola Bíblica" and res.data["church"]["name"] == church.name


def test_explore_by_church(anonymous, public_cell, public_event):
    from apps.churches.models import Church

    other = Church.objects.create(name="Outra", abbreviation="O")
    data = anonymous.get(URL, {"church": str(other.id)}).data
    assert data["events"] == [] and data["cells"] == [] and data["classes"] == []
