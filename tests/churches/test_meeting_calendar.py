from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from apps.churches import meeting_calendar as mc
from apps.churches.choices import LeadershipRole
from apps.churches.models import Cell, CellLeadership, CellMeeting, Season
from tests.conftest import make_cell, make_person

TODAY = timezone.localdate()


# --- Pure calendar logic (fixed dates) -------------------------------------------
# August 2026: the 3rd is a Monday, so Wednesdays are 5, 12, 19 and 26.


def unsaved_cell(meeting_day=3):
    return Cell(name="GT", start_date=date(2026, 8, 3), end_date=date(2026, 8, 31), meeting_day=meeting_day)


def meeting(day, held=True, attendees=0, visitors=0):
    people = [object()] * attendees
    return SimpleNamespace(
        date=date(2026, 8, day),
        held=held,
        visitors_count=visitors,
        decisions_count=0,
        attendees=SimpleNamespace(all=lambda: people),
        visitors=SimpleNamespace(all=lambda: []),
    )


def statuses(entries):
    return [(e.date.day, e.scheduled_date and e.scheduled_date.day, e.status) for e in entries]


def test_scheduled_dates_follow_the_meeting_day():
    assert [d.day for d in mc.scheduled_dates(unsaved_cell())] == [5, 12, 19, 26]


def test_no_meeting_day_means_no_scheduled_dates():
    assert mc.scheduled_dates(unsaved_cell(meeting_day=None)) == []


def test_statuses_by_date():
    entries = mc.build_calendar(unsaved_cell(), [meeting(5)], today=date(2026, 8, 19))
    assert statuses(entries) == [
        (26, 26, mc.UPCOMING),
        (19, 19, mc.TODAY),
        (12, 12, mc.PENDING),
        (5, 5, mc.REGISTERED),
    ]


def test_meeting_moved_within_the_week_covers_the_scheduled_date():
    # Wednesday the 12th moved to Thursday the 13th.
    entries = mc.build_calendar(unsaved_cell(), [meeting(13)], today=date(2026, 8, 20))
    assert (13, 12, mc.REGISTERED) in statuses(entries)
    assert mc.summarize(entries)["pending"] == 2  # 5 and 19


def test_second_meeting_in_the_same_week_is_extra():
    entries = mc.build_calendar(unsaved_cell(), [meeting(12), meeting(15)], today=date(2026, 8, 20))
    assert (15, None, mc.REGISTERED) in statuses(entries)
    assert (12, 12, mc.REGISTERED) in statuses(entries)


def test_changing_the_day_mid_season_keeps_past_weeks_covered():
    # Met on Tuesdays (4, 11) before the cell moved to Wednesdays.
    entries = mc.build_calendar(unsaved_cell(), [meeting(4), meeting(11)], today=date(2026, 8, 20))
    assert mc.summarize(entries)["pending"] == 1  # only the 19th


def test_not_held_is_not_pending_and_stays_out_of_the_average():
    entries = mc.build_calendar(
        unsaved_cell(),
        [meeting(5, attendees=6, visitors=2), meeting(12, held=False)],
        today=date(2026, 8, 20),
    )
    summary = mc.summarize(entries)
    assert summary["not_held"] == 1
    assert summary["pending"] == 1
    assert summary["registered"] == 1
    assert summary["average_attendance"] == 8
    assert summary["total_visitors"] == 2
    assert summary["scheduled"] == 4


# --- API ------------------------------------------------------------------------------


@pytest.fixture
def season(church):
    return Season.objects.create(
        church=church, name="Atual", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=30)
    )


@pytest.fixture
def cell(campus, season):
    # Meets on today's weekday: scheduled 28, 21, 14 and 7 days ago, today, and ahead.
    return make_cell(campus=campus, season=season, name="GT Vida", meeting_day=TODAY.isoweekday())


@pytest.fixture
def cell_leader(campus, cell):
    person = make_person(email="lider.gt@ilan.church", name="Líder GT", campus=campus)
    CellLeadership.objects.create(cell=cell, person=person, role=LeadershipRole.LEADER)
    return person


@pytest.fixture
def member(campus, cell):
    person = make_person(email="membro.gt@ilan.church", name="Membro GT", campus=campus)
    cell.members.add(person)
    return person


def calendar(api_client, cell):
    response = api_client.get(f"/api/cells/{cell.id}/calendar/")
    assert response.status_code == 200, response.data
    return response.data


@pytest.mark.django_db
def test_calendar_shows_pending_today_and_upcoming(api_client, member, cell):
    api_client.force_authenticate(member)
    data = calendar(api_client, cell)
    by_date = {e["date"]: e["status"] for e in data["entries"]}
    assert by_date[TODAY] == mc.TODAY
    assert by_date[TODAY - timedelta(days=7)] == mc.PENDING
    assert by_date[TODAY + timedelta(days=7)] == mc.UPCOMING
    assert data["summary"]["pending"] == 4


@pytest.mark.django_db
def test_registering_a_pending_date_clears_it(api_client, cell_leader, member, cell):
    api_client.force_authenticate(cell_leader)
    week_ago = TODAY - timedelta(days=7)
    response = api_client.post(
        "/api/cell-meetings/",
        {"cell_id": str(cell.id), "date": str(week_ago), "attendee_ids": [str(member.id)], "visitors_count": 3},
        format="json",
    )
    assert response.status_code == 201, response.data

    data = calendar(api_client, cell)
    entry = next(e for e in data["entries"] if e["date"] == week_ago)
    assert entry["status"] == mc.REGISTERED
    assert entry["meeting"]["attendance_count"] == 4
    assert data["summary"]["pending"] == 3
    assert data["summary"]["total_visitors"] == 3
    assert api_client.get(f"/api/cells/{cell.id}/").data["pending_meetings"] == 3


@pytest.mark.django_db
def test_not_held_needs_a_reason(api_client, cell_leader, cell):
    api_client.force_authenticate(cell_leader)
    response = api_client.post(
        "/api/cell-meetings/", {"cell_id": str(cell.id), "date": str(TODAY), "held": False}, format="json"
    )
    assert response.status_code == 400
    assert "cancel_reason" in response.data


@pytest.mark.django_db
def test_not_held_drops_attendance_and_may_be_in_the_future(api_client, cell_leader, member, cell):
    api_client.force_authenticate(cell_leader)
    next_week = TODAY + timedelta(days=7)
    response = api_client.post(
        "/api/cell-meetings/",
        {
            "cell_id": str(cell.id),
            "date": str(next_week),
            "held": False,
            "cancel_reason": "Feriado",
            "attendee_ids": [str(member.id)],
            "visitors_count": 2,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    assert response.data["attendees"] == []
    assert response.data["visitors_count"] == 0
    entry = next(e for e in calendar(api_client, cell)["entries"] if e["date"] == next_week)
    assert entry["status"] == mc.NOT_HELD


@pytest.mark.django_db
def test_attendance_cannot_be_registered_in_the_future(api_client, cell_leader, cell):
    api_client.force_authenticate(cell_leader)
    response = api_client.post(
        "/api/cell-meetings/",
        {"cell_id": str(cell.id), "date": str(TODAY + timedelta(days=7))},
        format="json",
    )
    assert response.status_code == 400
    assert "date" in response.data


@pytest.mark.django_db
def test_switching_back_to_held_clears_the_reason(api_client, cell_leader, cell):
    meeting = CellMeeting.objects.create(cell=cell, date=TODAY, held=False, cancel_reason="Chuva")
    api_client.force_authenticate(cell_leader)
    response = api_client.patch(f"/api/cell-meetings/{meeting.id}/", {"held": True}, format="json")
    assert response.status_code == 200, response.data
    meeting.refresh_from_db()
    assert meeting.held is True
    assert meeting.cancel_reason == ""


@pytest.mark.django_db
def test_moving_a_meeting_onto_another_ones_date_is_rejected(api_client, cell_leader, cell):
    CellMeeting.objects.create(cell=cell, date=TODAY)
    other = CellMeeting.objects.create(cell=cell, date=TODAY - timedelta(days=7))
    api_client.force_authenticate(cell_leader)
    response = api_client.patch(f"/api/cell-meetings/{other.id}/", {"date": str(TODAY)}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_extra_meeting_has_no_scheduled_date(api_client, cell_leader, cell):
    # Same week as the scheduled date, which is already registered.
    CellMeeting.objects.create(cell=cell, date=TODAY - timedelta(days=7))
    extra = TODAY - timedelta(days=6) if (TODAY - timedelta(days=6)).isoweekday() != 1 else TODAY - timedelta(days=8)
    CellMeeting.objects.create(cell=cell, date=extra)
    api_client.force_authenticate(cell_leader)
    entry = next(e for e in calendar(api_client, cell)["entries"] if e["date"] == extra)
    assert entry["scheduled_date"] is None


@pytest.mark.django_db
def test_cell_without_meeting_day_lists_only_meetings(api_client, cell_leader, cell):
    cell.meeting_day = None
    cell.save()
    CellMeeting.objects.create(cell=cell, date=TODAY)
    api_client.force_authenticate(cell_leader)
    data = calendar(api_client, cell)
    assert [e["status"] for e in data["entries"]] == [mc.REGISTERED]
    assert data["summary"]["pending"] == 0


@pytest.mark.django_db
def test_outsider_cannot_see_the_calendar(api_client, regular_person, cell):
    api_client.force_authenticate(regular_person)
    assert api_client.get(f"/api/cells/{cell.id}/calendar/").status_code == 404
