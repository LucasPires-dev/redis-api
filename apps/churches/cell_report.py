"""Coordination report for cells: what happened week by week in a season, per
cell and per campus, and how much of each campus is in a cell.

Everything is computed from what the leaders register (CellMeeting rows), like
the calendar and the health: nothing here is stored. Weeks start on Monday;
a meeting counts in the week of its date.
"""

from datetime import timedelta

from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from . import cell_health, meeting_calendar
from .choices import CellRole, VisitorStatus


def week_start(day):
    return day - timedelta(days=day.isoweekday() - 1)


def report_weeks(cells, today=None):
    """Mondays from the earliest start among the cells up to the current
    week (or the latest end, if the season is over)."""
    today = today or timezone.localdate()
    starts = [c.effective_start for c in cells if c.effective_start]
    ends = [c.effective_end for c in cells if c.effective_end]
    if not starts:
        return []
    first = week_start(min(starts))
    last = week_start(min(max(ends), today) if ends else today)
    weeks = []
    current = first
    while current <= last:
        weeks.append(current)
        current += timedelta(weeks=1)
    return weeks


def _participants(cell):
    people = {leadership.person_id for leadership in cell.leaderships.all()}
    people.update(member.pk for member in cell.members.all())
    return people


def _ratio(part, whole, digits=1):
    return round(part / whole, digits) if whole else None


def cell_row(cell, weeks, today=None):
    """Totals and weekly series of one cell. Uses prefetched relations only:
    leaderships, members, visitors (statuses) and meetings with their
    attendees and visitors."""
    today = today or timezone.localdate()
    meetings = list(cell.meetings.all())
    held = cell_health.held_meetings(meetings, today)
    participants = _participants(cell)

    index = {week: i for i, week in enumerate(weeks)}
    series = [{"held": 0, "attendance": 0, "visitors": 0, "decisions": 0} for _ in weeks]
    attendance = visitors = decisions = 0
    rates = []
    for meeting in held:
        present = len(meeting.attendees.all())
        guests = len(meeting.visitors.all()) + meeting.visitors_count
        attendance += present
        visitors += guests
        decisions += meeting.decisions_count
        if participants:
            rates.append(present / len(participants))
        slot = index.get(week_start(meeting.date))
        if slot is not None:
            bucket = series[slot]
            bucket["held"] += 1
            bucket["attendance"] += present
            bucket["visitors"] += guests
            bucket["decisions"] += meeting.decisions_count
    statuses = [v.status for v in cell.visitors.all()]

    return {
        "id": str(cell.pk),
        "name": cell.name,
        "campus": {"id": str(cell.campus_id), "name": cell.campus.name},
        "leaders": [lead.person.name for lead in cell.leaderships.all() if lead.role == CellRole.LEADER],
        "trainees": [lead.person.name for lead in cell.leaderships.all() if lead.role == CellRole.TRAINEE],
        # The mother cell, for the generations tree.
        "parent": {"id": str(cell.parent_cell_id), "name": cell.parent_cell.name} if cell.parent_cell_id else None,
        "multiplied_on": cell.multiplied_on,
        # Cells born from this one (prefetched daughter_cells with a date).
        "multiplications": sum(1 for d in cell.daughter_cells.all() if d.multiplied_on),
        "multiplication_date": cell.multiplication_date,
        "supervisor": cell.supervisor.name if cell.supervisor_id else None,
        "last_supervision_visit": max((v.date for v in cell.supervision_visits.all()), default=None),
        "participants": len(participants),
        "meetings_held": len(held),
        "meetings_not_held": sum(1 for m in meetings if not m.held and m.date <= today),
        "meetings_pending": meeting_calendar.pending_count(cell, meetings, today=today),
        "attendance": attendance,
        "avg_attendance": _ratio(attendance, len(held)),
        "attendance_rate": round(100 * sum(rates) / len(rates)) if rates else None,
        "visitors": visitors,
        "decisions": decisions,
        "new_members": statuses.count(VisitorStatus.MEMBER),
        "forwarded": statuses.count(VisitorStatus.FORWARDED),
        "health": cell_health.cell_health(cell, meetings, today)["status"],
        "series": series,
    }


def campus_coverage(campus_ids, season_cells):
    """{campus_id: (people, in_a_cell)}: active people of each campus, and how
    many of them lead, co-lead or belong to any of `season_cells` (a Cell
    queryset — cells of the same season, from any campus)."""
    from apps.people.models import Person

    in_cell = Exists(
        season_cells.filter(Q(members=OuterRef("pk")) | Q(leaderships__person=OuterRef("pk")))
    )
    rows = (
        Person.objects.filter(campus_id__in=campus_ids, is_active=True)
        .annotate(in_cell=in_cell)
        .values_list("campus_id", "in_cell")
    )
    coverage = {campus_id: [0, 0] for campus_id in campus_ids}
    for campus_id, is_in in rows:
        coverage[campus_id][0] += 1
        coverage[campus_id][1] += int(is_in)
    return coverage


def build_report(cells, coverage, today=None):
    """The whole report for a list of cells (see cell_row) and the campus
    coverage (see campus_coverage)."""
    today = today or timezone.localdate()
    cells = list(cells)
    weeks = report_weeks(cells, today)
    rows = [cell_row(cell, weeks, today) for cell in cells]

    campuses = {}
    for cell, row in zip(cells, rows):
        campus = campuses.setdefault(
            cell.campus_id,
            {
                "id": str(cell.campus_id),
                "name": cell.campus.name,
                "cells": 0,
                "participant_ids": set(),
                "meetings_held": 0,
                "attendance": 0,
                "visitors": 0,
                "decisions": 0,
                "new_members": 0,
            },
        )
        campus["cells"] += 1
        campus["participant_ids"] |= _participants(cell)
        for key in ("meetings_held", "attendance", "visitors", "decisions", "new_members"):
            campus[key] += row[key]

    campus_rows = []
    for campus_id, campus in campuses.items():
        people, in_a_cell = coverage.get(campus_id, (0, 0))
        participant_ids = campus.pop("participant_ids")
        campus_rows.append(
            {
                **campus,
                "participants": len(participant_ids),
                "avg_attendance": _ratio(campus["attendance"], campus["meetings_held"]),
                "people": people,
                "people_in_cells": in_a_cell,
                "coverage": round(100 * in_a_cell / people) if people else None,
            }
        )
    campus_rows.sort(key=lambda c: c["name"])

    totals = {
        key: sum(row[key] for row in rows)
        for key in (
            "meetings_held",
            "meetings_pending",
            "attendance",
            "visitors",
            "decisions",
            "new_members",
            "multiplications",
        )
    }
    totals["trainees"] = sum(len(row["trainees"]) for row in rows)
    totals["planned_multiplications"] = sum(1 for row in rows if row["multiplication_date"])
    totals["cells"] = len(rows)
    totals["participants"] = sum(c["participants"] for c in campus_rows)
    totals["avg_attendance"] = _ratio(totals["attendance"], totals["meetings_held"])
    people = sum(c["people"] for c in campus_rows)
    in_cells = sum(c["people_in_cells"] for c in campus_rows)
    totals["coverage"] = round(100 * in_cells / people) if people else None

    return {"weeks": weeks, "cells": rows, "campuses": campus_rows, "totals": totals}
