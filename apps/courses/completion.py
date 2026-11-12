"""Attendance and completion of a course class.

A lesson counts for a student once attendance was taken for them: the teacher
saves the whole roster of a lesson at once, so a student who enrolled after a
lesson isn't held against it. A student meets the class's criterion when they
attended at least `min_attendance_percent` of the lessons that counted for them.

Completing is still a decision of the class's managers: `complete` marks the
people they confirm (the screen pre-selects who meets the criterion).
"""

from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from . import billing, certificates
from .choices import EnrollmentStatus
from .models import LessonAttendance


def summary(present, counted):
    return {
        "present": present,
        "counted": counted,
        "percent": round(100 * present / counted) if counted else None,
    }


EMPTY_SUMMARY = summary(0, 0)


def attendance_by_person(course_class):
    """{person_id: {present, counted, percent}} for everyone with attendance taken."""
    counts = defaultdict(lambda: [0, 0])
    rows = LessonAttendance.objects.filter(lesson__course_class=course_class).values_list(
        "person_id", "present"
    )
    for person_id, present in rows:
        counts[person_id][1] += 1
        if present:
            counts[person_id][0] += 1
    return {person_id: summary(p, c) for person_id, (p, c) in counts.items()}


def meets_criterion(course_class, attendance):
    """Whether an attendance summary reaches the class's minimum (exactly, not rounded)."""
    counted = attendance["counted"]
    return counted > 0 and attendance["present"] * 100 >= course_class.min_attendance_percent * counted


def lesson_history(enrollment):
    """Every lesson of the class with whether the student was there (None: not taken)."""
    course_class = enrollment.course_class
    taken = dict(
        LessonAttendance.objects.filter(
            lesson__course_class=course_class, person_id=enrollment.person_id
        ).values_list("lesson_id", "present")
    )
    return [
        {"id": str(lesson.id), "order": lesson.order, "title": lesson.title, "date": lesson.date,
         "present": taken.get(lesson.id)}
        for lesson in course_class.lessons.all()
    ]


@transaction.atomic
def complete(course_class, person_ids):
    """Marks the given students' active enrollments as completed. Returns them."""
    enrollments = list(
        billing.active_enrollments(course_class).filter(
            status=EnrollmentStatus.ACTIVE, person_id__in=person_ids
        )
    )
    now = timezone.now()
    for enrollment in enrollments:
        enrollment.status = EnrollmentStatus.COMPLETED
        enrollment.completed_at = now
        enrollment.save(update_fields=["status", "completed_at", "updated_at"])
        certificates.ensure_code(enrollment)
    return enrollments
