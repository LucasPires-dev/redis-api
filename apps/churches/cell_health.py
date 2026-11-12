"""How a cell is doing, and who in it needs a call.

Health is computed from what the leaders register (CellMeeting rows) and the
cell's calendar (meeting_calendar): never stored, so it follows the data. The
rules are deliberately simple so a coordinator can read the reasons and agree:

- critical (red): no leader; 3+ scheduled meetings without a record; less than
  half of the cell present on average; attendance down 30%+.
- attention (yellow): 1-2 meetings without a record; less than 70% present;
  attendance down 15%+; no visitors in the last meetings; required fields
  missing (the cell isn't active).
- healthy (green): none of the above, with meetings registered.
- new: nothing registered yet and nothing wrong.

"Recent" means the last RECENT held meetings; the trend compares them with
the RECENT before.
"""

from django.utils import timezone

from . import meeting_calendar
from .choices import LeadershipRole

RECENT = 4

CRITICAL = "critical"
ATTENTION = "attention"
HEALTHY = "healthy"
NEW = "new"

# Pessoas para cuidar.
ABSENCE_STREAK = 3  # consecutive held meetings missed
VISITOR_MISSED = 2  # held meetings since the visitor last came


def _avg(values):
    return sum(values) / len(values) if values else None


def held_meetings(meetings, today=None):
    """Held meetings up to today, oldest first."""
    today = today or timezone.localdate()
    return sorted((m for m in meetings if m.held and m.date <= today), key=lambda m: m.date)


def cell_health(cell, meetings, today=None):
    """{status, attendance_rate, trend, recent_visitors, reasons} for a cell.

    Uses prefetched relations only (leaderships, members, meetings with their
    attendees and visitors), so it adds no queries in a list."""
    today = today or timezone.localdate()
    meetings = list(meetings)
    held = held_meetings(meetings, today)
    recent = held[-RECENT:]
    previous = held[: -RECENT][-RECENT:]

    roster = {leadership.person_id for leadership in cell.leaderships.all()}
    roster.update(member.pk for member in cell.members.all())

    def present(meeting):
        return len(meeting.attendees.all())

    def visitors(meeting):
        return len(meeting.visitors.all()) + meeting.visitors_count

    rate = _avg([present(m) / len(roster) for m in recent]) if roster and recent else None
    avg_recent = _avg([present(m) + visitors(m) for m in recent])
    avg_previous = _avg([present(m) + visitors(m) for m in previous])
    trend = (
        (avg_recent - avg_previous) / avg_previous
        if len(previous) == RECENT and avg_previous and avg_recent is not None
        else None
    )
    recent_visitors = sum(visitors(m) for m in recent)
    pending = meeting_calendar.pending_count(cell, meetings, today=today)
    has_leader = any(leadership.role == LeadershipRole.LEADER for leadership in cell.leaderships.all())

    reasons = []

    def flag(level, text):
        reasons.append({"level": level, "text": text})

    if not has_leader:
        flag(CRITICAL, "Sem líder")
    if pending >= 3:
        flag(CRITICAL, f"{pending} encontros sem registro")
    elif pending:
        flag(ATTENTION, f"{pending} {'encontro' if pending == 1 else 'encontros'} sem registro")
    if rate is not None:
        if rate < 0.5:
            flag(CRITICAL, f"Frequência de {round(rate * 100)}% nos últimos encontros")
        elif rate < 0.7:
            flag(ATTENTION, f"Frequência de {round(rate * 100)}% nos últimos encontros")
    if trend is not None:
        if trend <= -0.3:
            flag(CRITICAL, f"Presença caiu {round(-trend * 100)}%")
        elif trend <= -0.15:
            flag(ATTENTION, f"Presença caiu {round(-trend * 100)}%")
    if len(recent) >= 3 and recent_visitors == 0:
        flag(ATTENTION, "Sem visitantes nos últimos encontros")
    if cell.missing_fields():
        flag(ATTENTION, "Cadastro incompleto: a célula não está ativa")

    levels = {r["level"] for r in reasons}
    if CRITICAL in levels:
        status = CRITICAL
    elif ATTENTION in levels:
        status = ATTENTION
    elif held:
        status = HEALTHY
    else:
        status = NEW

    return {
        "status": status,
        "attendance_rate": round(rate * 100) if rate is not None else None,
        "trend": round(trend * 100) if trend is not None else None,
        "recent_visitors": recent_visitors,
        "reasons": reasons,
    }


def people_to_care(cell, meetings, visitors, today=None):
    """Who the leader should reach out to: participants who missed the last
    ABSENCE_STREAK held meetings in a row, and visitors still visiting who
    haven't come back for VISITOR_MISSED held meetings."""
    held = held_meetings(meetings, today)
    newest_first = list(reversed(held))

    people = {leadership.person_id: leadership.person for leadership in cell.leaderships.all()}
    people.update({member.pk: member for member in cell.members.all()})

    absent = []
    for person in people.values():
        streak = 0
        last_attended = None
        for meeting in newest_first:
            if any(a.pk == person.pk for a in meeting.attendees.all()):
                last_attended = meeting.date
                break
            streak += 1
        if streak >= ABSENCE_STREAK:
            absent.append({"person": person, "absences": streak, "last_attended": last_attended})
    absent.sort(key=lambda row: (-row["absences"], row["person"].name))

    not_returning = []
    for visitor in visitors:
        visits = [m.date for m in held if any(v.pk == visitor.pk for v in m.visitors.all())]
        if not visits:
            continue
        missed = sum(1 for m in held if m.date > visits[-1])
        if missed >= VISITOR_MISSED:
            not_returning.append(
                {"visitor": visitor, "last_visit": visits[-1], "visit_count": len(visits), "missed": missed}
            )
    not_returning.sort(key=lambda row: row["last_visit"])
    return absent, not_returning
