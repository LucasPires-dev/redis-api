"""A cell's meeting calendar, computed from its period and meeting day.

The scheduled dates are never stored: they come from the cell's effective
start/end and `meeting_day`, so changing the day mid-season just recomputes
them. Only what the leaders register (CellMeeting rows) is stored.

Each scheduled date is matched to a meeting on that exact date or, failing
that, to a meeting in the same week (a meeting moved to another day still
covers its week). Meetings left over are "extra" meetings.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

# Entry statuses.
REGISTERED = "registered"  # held, attendance recorded
NOT_HELD = "not_held"  # marked as not happened (holiday, cancelled)
PENDING = "pending"  # past date, nothing registered
TODAY = "today"  # today, nothing registered yet
UPCOMING = "upcoming"  # future date


@dataclass
class CalendarEntry:
    date: date  # the meeting's date when there is one, else the scheduled date
    scheduled_date: date | None  # None for an extra meeting
    status: str
    meeting: object | None  # CellMeeting


def scheduled_dates(cell):
    """Every date of the cell's period that falls on its meeting day."""
    start, end = cell.effective_start, cell.effective_end
    if not cell.meeting_day or not start or not end:
        return []
    current = start + timedelta(days=(cell.meeting_day - start.isoweekday()) % 7)
    dates = []
    while current <= end:
        dates.append(current)
        current += timedelta(weeks=1)
    return dates


def _week(day):
    return day.isocalendar()[:2]


def build_calendar(cell, meetings, today=None):
    """Entries (newest first) for the cell's scheduled dates and meetings."""
    today = today or timezone.localdate()
    slots = scheduled_dates(cell)
    slot_set = set(slots)
    slot_by_week = {_week(slot): slot for slot in slots}

    meetings = sorted(meetings, key=lambda m: m.date)
    taken = {}  # slot -> meeting
    for meeting in meetings:
        if meeting.date in slot_set:
            taken[meeting.date] = meeting
    matched = {id(m) for m in taken.values()}
    extras = []
    for meeting in meetings:
        if id(meeting) in matched:
            continue
        slot = slot_by_week.get(_week(meeting.date))
        if slot and slot not in taken:
            taken[slot] = meeting
        else:
            extras.append(meeting)

    def meeting_status(meeting):
        return REGISTERED if meeting.held else NOT_HELD

    entries = []
    for slot in slots:
        meeting = taken.get(slot)
        if meeting:
            entries.append(CalendarEntry(meeting.date, slot, meeting_status(meeting), meeting))
        elif slot < today:
            entries.append(CalendarEntry(slot, slot, PENDING, None))
        elif slot == today:
            entries.append(CalendarEntry(slot, slot, TODAY, None))
        else:
            entries.append(CalendarEntry(slot, slot, UPCOMING, None))
    entries += [CalendarEntry(m.date, None, meeting_status(m), m) for m in extras]
    entries.sort(key=lambda e: e.date, reverse=True)
    return entries


def summarize(entries):
    held = [e.meeting for e in entries if e.status == REGISTERED]
    # Visitors: the ones registered by name plus the ones counted by number.
    visitors = [len(m.visitors.all()) + m.visitors_count for m in held]
    attendance = [len(m.attendees.all()) + v for m, v in zip(held, visitors)]
    count = lambda status: sum(1 for e in entries if e.status == status)  # noqa: E731
    return {
        "scheduled": sum(1 for e in entries if e.scheduled_date),
        "registered": len(held),
        "not_held": count(NOT_HELD),
        "pending": count(PENDING),
        "upcoming": count(UPCOMING) + count(TODAY),
        "average_attendance": round(sum(attendance) / len(attendance)) if attendance else None,
        "total_visitors": sum(visitors),
        "total_decisions": sum(m.decisions_count for m in held),
    }


def pending_count(cell, meetings, today=None):
    return sum(1 for e in build_calendar(cell, meetings, today) if e.status == PENDING)
