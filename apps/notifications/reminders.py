"""Scheduled reminders (run by Celery Beat, see tasks.py and CELERY_BEAT_SCHEDULE).

Each function looks at the state of things *now* and notifies whoever should
act. Every reminder has a dedupe key, so a run that repeats (a retry, two beat
instances, a manual `send_reminders`) never sends the same reminder twice.
Each returns how many reminders it raised, for logs and tests.
"""

from datetime import timedelta

from django.db.models import Prefetch
from django.utils import timezone

from apps.churches import cell_health, meeting_calendar
from apps.churches.choices import CellRole, VisitorStatus
from apps.churches.models import Cell, CellLeadership, CellMeeting, CellVisitor
from apps.courses import billing, course_report, waitlist
from apps.courses.choices import EnrollmentPaymentStatus, EnrollmentStatus
from apps.courses.models import CourseClass, Enrollment, WaitlistEntry
from apps.payments.models import EventRegistration, PaymentStatus
from apps.production.choices import EventStatus, RosterStatus
from apps.production.models import EventRosterEntry

from .models import Notification
from .service import notify
from .triggers import class_label, when_text

# How close to the deadline a reservation or a held seat gets its reminder.
DEADLINE_WINDOW = timedelta(hours=12)
# Read notifications are kept this long; unread ones a bit longer.
KEEP_READ = timedelta(days=90)
KEEP_UNREAD = timedelta(days=180)

CELL_LEADER_ROLES = [CellRole.LEADER, CellRole.CO_LEADER]


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


# ── Hourly ───────────────────────────────────────────────────────────────────


def refresh_waitlists():
    """Hands out seats freed meanwhile (an expired reservation, a cancellation),
    without waiting for someone to open the class. Offers notify the person."""
    billing.expire_overdue()
    class_ids = WaitlistEntry.objects.values_list("course_class_id", flat=True).distinct()
    classes = CourseClass.objects.filter(pk__in=list(class_ids))
    for course_class in classes:
        waitlist.refresh(course_class)
    return classes.count()


def reservations_expiring(now=None):
    """Self-enrollments still unpaid whose seat is about to be released."""
    now = now or timezone.now()
    enrollments = Enrollment.objects.filter(
        payment_status=EnrollmentPaymentStatus.PENDING,
        payments__isnull=True,
        expires_at__gt=now,
        expires_at__lte=now + DEADLINE_WINDOW,
    ).exclude(status=EnrollmentStatus.DROPPED).select_related("course_class__course")
    sent = 0
    for enrollment in enrollments:
        sent += len(
            notify(
                enrollment.person_id,
                "reservation_expiring",
                f"Sua vaga vence em breve: {class_label(enrollment.course_class)}",
                body=f"Conclua o pagamento até {when_text(enrollment.expires_at)} para não perder a vaga.",
                link="/app/cursos/matriculas",
                dedupe_key=f"reservation-expiring:{enrollment.pk}:{enrollment.expires_at:%Y%m%d%H%M}",
            )
        )
    return sent


def offers_expiring(now=None):
    """Seats held for someone in line who hasn't enrolled yet."""
    now = now or timezone.now()
    entries = WaitlistEntry.objects.filter(
        offer_expires_at__gt=now, offer_expires_at__lte=now + DEADLINE_WINDOW
    ).select_related("course_class__course")
    sent = 0
    for entry in entries:
        sent += len(
            notify(
                entry.person_id,
                "offer_expiring",
                f"Sua vaga reservada vence em breve: {class_label(entry.course_class)}",
                body=f"Faça a inscrição até {when_text(entry.offer_expires_at)}; "
                "depois a vaga passa para o próximo da fila.",
                link="/app/cursos",
                dedupe_key=f"offer-expiring:{entry.pk}",
            )
        )
    return sent


# ── Daily ────────────────────────────────────────────────────────────────────


def _cell_leaders(cell):
    return [lead.person_id for lead in cell.leaderships.all() if lead.role in CELL_LEADER_ROLES]


def _active_cells(today):
    return Cell.objects.active(today).prefetch_related(
        Prefetch("leaderships", queryset=CellLeadership.objects.only("cell_id", "person_id", "role"))
    )


def meetings_not_registered(today=None):
    """Yesterday's scheduled meeting with nothing registered: remind the leaders."""
    today = today or timezone.localdate()
    yesterday = today - timedelta(days=1)
    sent = 0
    for cell in _active_cells(today).filter(meeting_day=yesterday.isoweekday()):
        if yesterday not in meeting_calendar.scheduled_dates(cell):
            continue
        meetings = list(CellMeeting.objects.filter(cell=cell))
        entries = meeting_calendar.build_calendar(cell, meetings, today)
        if not any(e.scheduled_date == yesterday and e.status == meeting_calendar.PENDING for e in entries):
            continue
        sent += len(
            notify(
                _cell_leaders(cell),
                "meeting_not_registered",
                f"Registre o encontro de ontem: {cell.name}",
                body="Marque a presença, os visitantes e as decisões, ou indique que o encontro não aconteceu.",
                link=f"/app/celulas/{cell.pk}",
                dedupe_key=f"meeting-pending:{cell.pk}:{yesterday:%Y%m%d}",
            )
        )
    return sent


def events_tomorrow(today=None):
    """The day before an event, to everyone with a confirmed registration."""
    today = today or timezone.localdate()
    tomorrow = today + timedelta(days=1)
    registrations = EventRegistration.objects.filter(
        payment_status=PaymentStatus.CONFIRMED,
        event__starts_at__date=tomorrow,
    ).exclude(event__status=EventStatus.CLOSED).select_related("event")
    sent = 0
    for registration in registrations:
        event = registration.event
        sent += len(
            notify(
                registration.person_id,
                "event_tomorrow",
                f"Amanhã: {event.title}",
                body=f"Começa às {timezone.localtime(event.starts_at):%H:%M}. Até lá!",
                link="/app/eventos/inscricoes",
                dedupe_key=f"event-tomorrow:{registration.pk}",
            )
        )
    return sent


def _serving(entry) -> str:
    return entry.ministry.name + (f" · {entry.function}" if entry.function else "")


def roster_tomorrow(today=None):
    """The day before, to everyone confirmed on an event's roster."""
    today = today or timezone.localdate()
    entries = EventRosterEntry.objects.filter(
        status=RosterStatus.CONFIRMED, event__starts_at__date=today + timedelta(days=1)
    ).exclude(event__status=EventStatus.CLOSED).select_related("event", "ministry")
    sent = 0
    for entry in entries:
        event = entry.event
        sent += len(
            notify(
                entry.person_id,
                "roster_tomorrow",
                f"Amanhã você serve: {event.title}",
                body=f"{_serving(entry)}. Começa às {timezone.localtime(event.starts_at):%H:%M}.",
                link="/app/eventos/escalas",
                dedupe_key=f"roster-tomorrow:{entry.pk}:{entry.person_id}",
            )
        )
    return sent


ANSWER_WITHIN = timedelta(days=3)


def roster_unanswered(today=None):
    """Invitations still without an answer when the event is a few days away."""
    today = today or timezone.localdate()
    entries = EventRosterEntry.objects.filter(
        status=RosterStatus.INVITED,
        event__starts_at__date__gt=today,
        event__starts_at__date__lte=today + ANSWER_WITHIN,
    ).exclude(event__status=EventStatus.CLOSED).select_related("event", "ministry")
    sent = 0
    for entry in entries:
        sent += len(
            notify(
                entry.person_id,
                "roster_unanswered",
                f"Responda à escala: {entry.event.title}",
                body=f"{_serving(entry)}, {when_text(entry.event.starts_at)}. Confirme ou recuse para a "
                "liderança se organizar.",
                link="/app/eventos/escalas",
                dedupe_key=f"roster-unanswered:{entry.pk}:{entry.person_id}",
            )
        )
    return sent


def clean_up(now=None):
    """Old notifications go away, so the center stays light."""
    now = now or timezone.now()
    deleted, _ = Notification.objects.filter(read_at__lt=now - KEEP_READ).delete()
    old, _ = Notification.objects.filter(created_at__lt=now - KEEP_UNREAD).delete()
    return deleted + old


# ── Weekly ───────────────────────────────────────────────────────────────────


def _week_key(today):
    year, week, _ = today.isocalendar()
    return f"{year}w{week}"


def cells_to_care(today=None):
    """Weekly summary of each cell's "Pessoas para cuidar" for its leaders."""
    today = today or timezone.localdate()
    sent = 0
    for cell in _active_cells(today).prefetch_related("members"):
        meetings = list(CellMeeting.objects.filter(cell=cell).prefetch_related("attendees", "visitors"))
        visitors = CellVisitor.objects.filter(cell=cell, status=VisitorStatus.VISITING)
        absent, not_returning = cell_health.people_to_care(cell, meetings, visitors, today)
        if not absent and not not_returning:
            continue
        parts = []
        if absent:
            who = _plural(len(absent), "participante faltou", "participantes faltaram")
            parts.append(f"{who} aos últimos encontros")
        if not_returning:
            parts.append(f"{_plural(len(not_returning), 'visitante não voltou', 'visitantes não voltaram')}")
        sent += len(
            notify(
                _cell_leaders(cell),
                "cell_care",
                f"Pessoas para cuidar: {cell.name}",
                body=" e ".join(parts).capitalize() + ".",
                link=f"/app/celulas/{cell.pk}",
                dedupe_key=f"cell-care:{cell.pk}:{_week_key(today)}",
            )
        )
    return sent


def classes_to_care(today=None):
    """Weekly summary of each running class's "Alunos para cuidar" for its teachers."""
    today = today or timezone.localdate()
    classes = CourseClass.objects.select_related("course").prefetch_related("teachers")
    sent = 0
    for course_class in classes:
        if course_report.class_state(course_class, today) != "ongoing":
            continue
        teachers = [t.person_id for t in course_class.teachers.all()]
        if not teachers:
            continue
        students = course_report.students_to_care(course_class)
        if not students:
            continue
        sent += len(
            notify(
                teachers,
                "class_care",
                f"Alunos para cuidar: {class_label(course_class)}",
                body=f"{_plural(len(students), 'aluno está', 'alunos estão')} com faltas seguidas "
                "ou abaixo da frequência mínima.",
                link=f"/app/cursos/{course_class.course_id}/turma/{course_class.pk}",
                dedupe_key=f"class-care:{course_class.pk}:{_week_key(today)}",
            )
        )
    return sent


HOURLY = [refresh_waitlists, reservations_expiring, offers_expiring]
DAILY = [meetings_not_registered, events_tomorrow, roster_tomorrow, roster_unanswered, clean_up]
WEEKLY = [cells_to_care, classes_to_care]
