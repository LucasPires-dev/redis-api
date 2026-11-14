"""Duplicating events, recurring events (series) and the calendar range."""

from datetime import date, time, timedelta

import pytest
from django.utils import timezone

from apps.notifications.models import Notification
from apps.payments.models import EventRegistration, EventTicketConfig, EventTicketType, PaymentStatus
from apps.production import series as series_mod
from apps.production.choices import RosterStatus
from apps.production.models import Event, EventChecklistItem, EventRosterEntry, EventSeries, RundownItem
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

EVENTS = "/api/production-events"
SERIES = "/api/production-series"


def as_(api_client, person):
    api_client.force_authenticate(user=person)
    return api_client


@pytest.fixture
def source(campus, jovens_leader, media_ministry, media_member):
    starts = timezone.now() + timedelta(days=3)
    event = Event.objects.create(
        title="Culto de Jovens", campus=campus, ministry_kind="jovens", event_type="ministry_event",
        starts_at=starts, ends_at=starts + timedelta(hours=2),
    )
    RundownItem.objects.create(
        event=event, order=1, title="Louvor", ministry_kind="som", scheduled_time=starts + timedelta(minutes=10)
    )
    EventChecklistItem.objects.create(event=event, ministry_kind="media", text="Testar projetor", is_done=True)
    EventRosterEntry.objects.create(
        event=event, ministry=media_ministry, person=media_member, function="Câmera", status=RosterStatus.CONFIRMED
    )
    config = EventTicketConfig.objects.create(
        event=event, price_cents=0, max_capacity=50, registration_deadline=starts - timedelta(hours=1)
    )
    EventTicketType.objects.create(config=config, name="Inteira", sales_end=starts - timedelta(days=1))
    return event


def test_duplicate_copies_everything_shifted(api_client, jovens_leader, source, media_member):
    new_start = source.starts_at + timedelta(days=7)
    res = as_(api_client, jovens_leader).post(
        f"{EVENTS}/{source.id}/duplicate/", {"starts_at": new_start.isoformat()}, format="json"
    )
    assert res.status_code == 201, res.data
    copy = Event.objects.get(pk=res.data["id"])
    assert copy.title == source.title and copy.ends_at == source.ends_at + timedelta(days=7)
    (item,) = copy.rundown_items.all()
    assert item.scheduled_time == new_start + timedelta(minutes=10)
    (check,) = copy.checklist_items.all()
    assert check.text == "Testar projetor" and not check.is_done
    (entry,) = copy.roster_entries.all()
    assert entry.person == media_member and entry.status == RosterStatus.INVITED and entry.function == "Câmera"
    assert Notification.objects.filter(recipient=media_member, kind="roster_invite").exists()
    config = copy.ticket_config
    assert config.registration_deadline == new_start - timedelta(hours=1)
    assert config.types.get().sales_end == new_start - timedelta(days=1)
    assert not copy.registrations.exists()


def test_duplicate_without_roster_and_new_title(api_client, jovens_leader, source):
    res = as_(api_client, jovens_leader).post(
        f"{EVENTS}/{source.id}/duplicate/",
        {"starts_at": (source.starts_at + timedelta(days=1)).isoformat(), "title": "Culto extra", "copy_roster": False},
        format="json",
    )
    copy = Event.objects.get(pk=res.data["id"])
    assert copy.title == "Culto extra" and not copy.roster_entries.exists()


def test_only_organizers_duplicate(api_client, regular_person, source):
    res = as_(api_client, regular_person).post(
        f"{EVENTS}/{source.id}/duplicate/", {"starts_at": source.starts_at.isoformat()}, format="json"
    )
    assert res.status_code in (403, 404)


def test_occurrences_keep_the_rhythm():
    s = EventSeries(weekday=6, start_time=time(19), starts_on=date(2026, 11, 2), every_weeks=2)  # Sundays
    days = list(series_mod.occurrences(s, date(2026, 11, 10), date(2026, 12, 31)))
    # First Sunday on/after 2/11 is 8/11; every other week: 22/11, 6/12, 20/12.
    assert days == [date(2026, 11, 22), date(2026, 12, 6), date(2026, 12, 20)]
    s.skipped_dates = ["2026-12-06"]
    s.ends_on = date(2026, 12, 10)
    assert list(series_mod.occurrences(s, date(2026, 11, 10), date(2026, 12, 31))) == [date(2026, 11, 22)]


def series_body(campus, **extra):
    return {
        "title": "Culto de Domingo",
        "event_type": "culto",
        "campus_id": str(campus.id),
        "weekday": 6,
        "start_time": "19:00",
        "duration_minutes": 120,
        "starts_on": timezone.localdate().isoformat(),
        "weeks_ahead": 4,
        **extra,
    }


def test_series_generates_and_keeps_deleted_dates_deleted(api_client, campus_leader, campus):
    client = as_(api_client, campus_leader)
    res = client.post(f"{SERIES}/", series_body(campus), format="json")
    assert res.status_code == 201, res.data
    s = EventSeries.objects.get(pk=res.data["id"])
    events = list(s.events.order_by("starts_at"))
    assert 4 <= len(events) <= 5
    assert all(timezone.localtime(e.starts_at).weekday() == 6 for e in events)
    assert all(timezone.localtime(e.starts_at).hour == 19 for e in events)
    assert events[0].ends_at - events[0].starts_at == timedelta(hours=2)

    # Running again creates nothing new.
    assert client.post(f"{SERIES}/{s.id}/generate/").data["created"] == 0

    # A holiday: that Sunday is deleted and not created again.
    assert client.delete(f"{EVENTS}/{events[0].id}/").status_code == 204
    assert series_mod.generate_all() == 0
    s.refresh_from_db()
    assert s.skipped_dates == [timezone.localdate(events[0].starts_at).isoformat()]

    listed = client.get(f"{SERIES}/").data["results"]
    assert listed[0]["upcoming"] == len(events) - 1 and len(listed[0]["next_dates"]) >= 1


def test_series_from_a_template(api_client, jovens_leader, campus, source):
    body = series_body(campus, event_type="ministry_event", ministry_kind="jovens", template_id=str(source.id))
    res = as_(api_client, jovens_leader).post(f"{SERIES}/", body, format="json")
    assert res.status_code == 201, res.data
    occurrence = EventSeries.objects.get(pk=res.data["id"]).events.order_by("starts_at").first()
    assert occurrence.title == "Culto de Domingo"
    assert occurrence.rundown_items.count() == 1 and occurrence.checklist_items.count() == 1
    assert not occurrence.roster_entries.exists()  # copy_roster off by default
    assert occurrence.ticket_config.max_capacity == 50


def test_who_creates_and_sees_series(api_client, regular_person, campus_leader, campus):
    assert as_(api_client, regular_person).post(f"{SERIES}/", series_body(campus), format="json").status_code == 403
    as_(api_client, campus_leader).post(f"{SERIES}/", series_body(campus), format="json")
    assert as_(api_client, regular_person).get(f"{SERIES}/").data["results"] == []


def test_ending_a_series_keeps_occurrences_with_registrations(api_client, campus_leader, campus):
    client = as_(api_client, campus_leader)
    s = EventSeries.objects.get(pk=client.post(f"{SERIES}/", series_body(campus), format="json").data["id"])
    first, *others = list(s.events.order_by("starts_at"))
    EventTicketConfig.objects.create(event=first, price_cents=0)
    attendee = make_person(email="vem@ilan.church", campus=campus)
    EventRegistration.objects.create(event=first, person=attendee, payment_status=PaymentStatus.CONFIRMED)

    assert client.delete(f"{SERIES}/{s.id}/").status_code == 204
    assert list(Event.objects.filter(pk__in=[e.pk for e in [first, *others]])) == [first]


def test_calendar_range(api_client, campus_leader, campus):
    now = timezone.now()
    Event.objects.create(title="Dentro", campus=campus, starts_at=now + timedelta(days=2))
    Event.objects.create(title="Fora", campus=campus, starts_at=now + timedelta(days=40))
    start = timezone.localdate()
    res = as_(api_client, campus_leader).get(
        f"{EVENTS}/?from={start.isoformat()}&to={(start + timedelta(days=10)).isoformat()}&page_size=100"
    )
    titles = [e["title"] for e in res.data["results"]]
    assert "Dentro" in titles and "Fora" not in titles


def test_calendar_loads_a_whole_month(api_client, campus_leader, campus):
    start = timezone.now() + timedelta(days=1)
    Event.objects.bulk_create(
        Event(title=f"E{i}", campus=campus, starts_at=start + timedelta(hours=i)) for i in range(30)
    )
    res = as_(api_client, campus_leader).get(f"{EVENTS}/?page_size=500")
    assert len(res.data["results"]) == 30
