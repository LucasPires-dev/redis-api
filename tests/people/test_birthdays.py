"""Birthdays: the date is optional (sign-up and own profile); leaders see the
day and month of the people of the cells they manage, never the year; the
cell's leadership gets a notice on the day."""

from datetime import date, datetime, time

import pytest
from django.utils import timezone

from apps.churches.choices import CellRole
from apps.churches.models import CellLeadership
from apps.notifications import reminders
from apps.notifications.models import Notification
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

ME = "/api/auth/me/"
URL = "/api/birthdays/"


def as_(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def cell(campus):
    cell = make_cell(campus=campus, name="GT Alegria")
    leader = make_person(email="lider.alegria@ilan.church", name="Líder Alegria", campus=campus)
    CellLeadership.objects.create(cell=cell, person=leader, role=CellRole.LEADER)
    cell.leader = leader
    return cell


def test_birth_date_is_optional_and_editable(api_client, regular_person):
    client = as_(api_client, regular_person)
    assert client.get(ME).data["birth_date"] is None
    res = client.patch(ME, {"birth_date": "1990-05-17", "phone": "11999990000"}, format="json")
    assert res.status_code == 200 and res.data["birth_date"] == "1990-05-17"
    assert client.patch(ME, {"birth_date": None}, format="json").data["birth_date"] is None
    assert client.patch(ME, {"birth_date": "2999-01-01"}, format="json").status_code == 400
    assert client.patch(ME, {"name": "  "}, format="json").status_code == 400


def test_sign_up_with_or_without_birth_date(api_client):
    api_client.force_authenticate(user=None)
    base = {"name": "Nova Pessoa", "password": "Sol!Nascente-2030"}
    assert api_client.post("/api/auth/register/", {**base, "email": "a@example.com"}).status_code == 201
    res = api_client.post("/api/auth/register/", {**base, "email": "b@example.com", "birth_date": "2001-03-09"})
    assert res.status_code == 201
    from apps.people.models import Person

    assert Person.objects.get(email="b@example.com").birth_date == date(2001, 3, 9)


def test_leaders_see_day_and_month_only(api_client, cell, campus, regular_person):
    ana = make_person(email="ana@ilan.church", name="Ana", campus=campus, birth_date=date(1985, 7, 20))
    bia = make_person(email="bia@ilan.church", name="Bia", campus=campus, birth_date=date(1999, 7, 3))
    sem = make_person(email="sem@ilan.church", name="Sem Data", campus=campus)
    cell.members.add(ana, bia, sem)
    make_person(email="fora@ilan.church", name="Fora", campus=campus, birth_date=date(1990, 7, 1))

    rows = as_(api_client, cell.leader).get(URL, {"month": 7, "cell": str(cell.id)}).data
    assert [(r["name"], r["day"]) for r in rows] == [("Bia", 3), ("Ana", 20)]
    assert "1985" not in str(rows) and rows[0]["cells"] == ["GT Alegria"]
    # Without ?cell: every cell the person manages.
    assert len(as_(api_client, cell.leader).get(URL, {"month": 7}).data) == 2

    assert as_(api_client, regular_person).get(URL, {"cell": str(cell.id)}).status_code == 403
    assert as_(api_client, regular_person).get(URL, {"month": 7}).data == []
    assert as_(api_client, cell.leader).get(URL, {"month": 13}).status_code == 400


def test_the_cell_leaders_hear_on_the_day(cell, campus):
    today = timezone.localdate()
    ana = make_person(
        email="ana@ilan.church", name="Ana", campus=campus, birth_date=date(1990, today.month, min(today.day, 28))
    )
    cell.members.add(ana)
    now = timezone.make_aware(datetime.combine(ana.birth_date.replace(year=today.year), time(8)))
    assert reminders.birthdays_today(now) == 1
    assert reminders.birthdays_today(now) == 0  # once a year
    note = Notification.objects.get(recipient=cell.leader, kind="birthday")
    assert "Ana" in note.title and note.link == f"/app/celulas/{cell.id}"
    assert not Notification.objects.filter(recipient=ana, kind="birthday").exists()


def test_leap_day_birthdays_count_on_the_28th(cell, campus):
    leap = make_person(email="leap@ilan.church", name="Bissexto", campus=campus, birth_date=date(2000, 2, 29))
    cell.members.add(leap)
    cell.start_date = cell.end_date = None
    cell.save()
    now = timezone.make_aware(datetime(2027, 2, 28, 8))
    cell.season = None
    cell.save()
    assert reminders.birthdays_today(now) == 1
