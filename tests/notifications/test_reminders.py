"""Scheduled reminders: who gets them, and never twice."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.churches.choices import CellRole
from apps.churches.models import CellLeadership, CellMeeting
from apps.courses.choices import EnrollmentPaymentStatus, TeacherRole
from apps.courses.models import (
    Course,
    CourseClass,
    CourseClassTeacher,
    Enrollment,
    Lesson,
    LessonAttendance,
    WaitlistEntry,
)
from apps.notifications import reminders
from apps.notifications.models import Notification
from apps.payments.models import EventRegistration, PaymentStatus
from apps.production.models import Event
from tests.conftest import make_cell, make_person

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def notes(person, kind):
    return list(Notification.objects.filter(recipient=person, kind=kind))


@pytest.fixture
def member(campus):
    return make_person(email="membro.r@ilan.church", name="Membro", campus=campus)


@pytest.fixture
def leader(campus):
    return make_person(email="lider.r@ilan.church", name="Líder", campus=campus)


@pytest.fixture
def course_class(church, campus):
    course = Course.objects.create(church=church, name="Fundamentos")
    return CourseClass.objects.create(
        course=course, campus=campus, name="Turma A", self_enrollment=True, max_capacity=1,
        reservation_days=2, start_date=TODAY - timedelta(days=20), end_date=TODAY + timedelta(days=30),
    )


# ── Hourly ───────────────────────────────────────────────────────────────────


def test_reservation_close_to_expiring(course_class, member, leader):
    now = timezone.now()
    Enrollment.objects.create(
        course_class=course_class, person=member, amount_cents=10000,
        payment_status=EnrollmentPaymentStatus.PENDING, expires_at=now + timedelta(hours=6),
    )
    Enrollment.objects.create(  # still has time
        course_class=course_class, person=leader, amount_cents=10000,
        payment_status=EnrollmentPaymentStatus.PENDING, expires_at=now + timedelta(hours=30),
    )
    assert reminders.reservations_expiring(now) == 1
    assert reminders.reservations_expiring(now) == 0  # once
    (note,) = notes(member, "reservation_expiring")
    assert note.title == "Sua vaga vence em breve: Fundamentos — Turma A"
    assert note.body.startswith("Conclua o pagamento até ")
    assert notes(leader, "reservation_expiring") == []


def test_held_seat_close_to_expiring(course_class, member):
    now = timezone.now()
    WaitlistEntry.objects.create(course_class=course_class, person=member, offer_expires_at=now + timedelta(hours=3))
    assert reminders.offers_expiring(now) == 1
    assert reminders.offers_expiring(now) == 0
    assert notes(member, "offer_expiring")[0].link == "/app/cursos"


def test_freed_seats_are_offered_without_anyone_opening_the_class(course_class, member, leader):
    # The only seat is held by a reservation that ran out.
    Enrollment.objects.create(
        course_class=course_class, person=leader, amount_cents=10000,
        payment_status=EnrollmentPaymentStatus.PENDING, expires_at=timezone.now() - timedelta(hours=1),
    )
    WaitlistEntry.objects.create(course_class=course_class, person=member)

    reminders.refresh_waitlists()
    assert WaitlistEntry.objects.get(person=member).offer_expires_at is not None
    assert len(notes(member, "waitlist_offer")) == 1


# ── Daily ────────────────────────────────────────────────────────────────────


@pytest.fixture
def cell(campus, leader, member):
    yesterday = TODAY - timedelta(days=1)
    cell = make_cell(
        campus=campus, name="Célula Esperança", meeting_day=yesterday.isoweekday(),
        start_date=TODAY - timedelta(days=60), end_date=TODAY + timedelta(days=60),
    )
    CellLeadership.objects.create(cell=cell, person=leader, role=CellRole.LEADER)
    trainee = make_person(email="timoteo.r@ilan.church", name="Timóteo", campus=campus)
    CellLeadership.objects.create(cell=cell, person=trainee, role=CellRole.TRAINEE)
    cell.members.add(member)
    return cell


def test_meeting_not_registered_reminds_the_leaders(cell, leader):
    assert reminders.meetings_not_registered(TODAY) == 1
    assert reminders.meetings_not_registered(TODAY) == 0
    (note,) = notes(leader, "meeting_not_registered")
    assert note.title == "Registre o encontro de ontem: Célula Esperança"
    assert note.link == f"/app/celulas/{cell.id}"
    # Leaders in training aren't asked to register.
    assert Notification.objects.filter(kind="meeting_not_registered").count() == 1


def test_registered_meeting_needs_no_reminder(cell):
    CellMeeting.objects.create(cell=cell, date=TODAY - timedelta(days=1), held=False)
    assert reminders.meetings_not_registered(TODAY) == 0


def test_event_tomorrow(campus, member, leader):
    tomorrow = timezone.now() + timedelta(days=1)
    event = Event.objects.create(title="Conferência", campus=campus, ministry_kind="jovens", starts_at=tomorrow)
    EventRegistration.objects.create(event=event, person=member, payment_status=PaymentStatus.CONFIRMED)
    EventRegistration.objects.create(event=event, person=leader, payment_status=PaymentStatus.PENDING)

    assert reminders.events_tomorrow(TODAY) == 1
    assert reminders.events_tomorrow(TODAY) == 0
    (note,) = notes(member, "event_tomorrow")
    assert note.title == "Amanhã: Conferência"
    assert notes(leader, "event_tomorrow") == []


def test_clean_up_drops_old_notifications(member):
    now = timezone.now()
    old_read = Notification.objects.create(recipient=member, kind="t", title="Lida antiga", read_at=now)
    recent = Notification.objects.create(recipient=member, kind="t", title="Recente")
    old_unread = Notification.objects.create(recipient=member, kind="t", title="Não lida antiga")
    Notification.objects.filter(pk=old_read.pk).update(read_at=now - timedelta(days=100))
    Notification.objects.filter(pk=old_unread.pk).update(created_at=now - timedelta(days=200))

    assert reminders.clean_up(now) == 2
    assert list(Notification.objects.filter(recipient=member)) == [recent]


# ── Weekly ───────────────────────────────────────────────────────────────────


def test_cells_to_care_summary(cell, leader, member):
    for weeks in (1, 2, 3):
        meeting = CellMeeting.objects.create(cell=cell, date=TODAY - timedelta(weeks=weeks), held=True)
        meeting.attendees.add(leader, *[lead.person for lead in cell.leaderships.filter(role=CellRole.TRAINEE)])

    assert reminders.cells_to_care(TODAY) == 1
    assert reminders.cells_to_care(TODAY) == 0  # once a week
    (note,) = notes(leader, "cell_care")
    assert note.title == "Pessoas para cuidar: Célula Esperança"
    assert note.body == "1 participante faltou aos últimos encontros."
    # Next week it comes again.
    assert reminders.cells_to_care(TODAY + timedelta(weeks=1)) == 1


def test_classes_to_care_summary(course_class, member, leader):
    CourseClassTeacher.objects.create(course_class=course_class, person=leader, role=TeacherRole.TEACHER)
    Enrollment.objects.create(course_class=course_class, person=member)
    for order in (1, 2):
        lesson = Lesson.objects.create(course_class=course_class, order=order, date=TODAY - timedelta(days=order))
        LessonAttendance.objects.create(lesson=lesson, person=member, present=False)

    assert reminders.classes_to_care(TODAY) == 1
    assert reminders.classes_to_care(TODAY) == 0
    (note,) = notes(leader, "class_care")
    assert note.title == "Alunos para cuidar: Fundamentos — Turma A"
    assert note.body.startswith("1 aluno está com faltas seguidas")
    assert note.link == f"/app/cursos/{course_class.course_id}/turma/{course_class.id}"


# ── Running them ─────────────────────────────────────────────────────────────


def test_one_failing_reminder_doesnt_stop_the_others(monkeypatch):
    from apps.notifications import tasks

    def broken():
        raise RuntimeError("boom")

    def fine():
        return 3

    assert tasks.run([broken, fine]) == {"broken": "error", "fine": 3}


def test_command_runs_every_batch(capsys):
    call_command("send_reminders")
    out = capsys.readouterr().out
    assert "hourly:" in out and "daily:" in out and "weekly:" in out
