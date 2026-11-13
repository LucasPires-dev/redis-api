"""Prerequisites between courses and growth tracks.

A course may require others to be completed first: enrolling in any of its
classes needs a completed enrollment in each prerequisite course (any class,
any campus). Members are blocked; coordinators may enroll anyway when they
say so (a pastoral decision).

A track (trilha) is an ordered list of the church's courses — e.g.
Membresia → Fundamentos → Liderança — kept by the church-wide coordination.
Each person sees where they stand in it.
"""

from rest_framework import serializers

from . import billing
from .choices import SEAT_HOLDING_PAYMENT_STATUSES, EnrollmentStatus
from .models import CourseClass, Enrollment


def completed_course_ids(person):
    return set(
        Enrollment.objects.filter(person=person, status=EnrollmentStatus.COMPLETED).values_list(
            "course_class__course_id", flat=True
        )
    )


def missing_prerequisites(course, person, completed=None):
    """The prerequisite courses the person hasn't completed yet, by name."""
    completed = completed_course_ids(person) if completed is None else completed
    return [c for c in course.prerequisites.all().order_by("name") if c.id not in completed]


def check_prerequisites(course_class, person):
    missing = missing_prerequisites(course_class.course, person)
    if missing:
        names = ", ".join(c.name for c in missing)
        raise serializers.ValidationError(
            {
                "detail": f"Para se inscrever, é preciso concluir antes: {names}.",
                "missing_prerequisites": [{"id": str(c.id), "name": c.name} for c in missing],
            }
        )


def creates_cycle(course, prerequisites):
    """Whether making `prerequisites` required by `course` would loop back to it
    (A requires B requires A)."""
    seen = set()
    stack = list(prerequisites)
    while stack:
        current = stack.pop()
        if current.pk == course.pk:
            return True
        if current.pk in seen:
            continue
        seen.add(current.pk)
        stack.extend(current.prerequisites.all())
    return False


# ── Tracks ───────────────────────────────────────────────────────────────────


def track_progress(track, person):
    """Each step with where the person stands, plus how far along they are."""
    steps = list(track.steps.select_related("course").order_by("order"))
    course_ids = [s.course_id for s in steps]
    enrollments = Enrollment.objects.filter(
        person=person, course_class__course_id__in=course_ids
    ).exclude(status=EnrollmentStatus.DROPPED)
    completed = {e.course_class.course_id for e in enrollments.select_related("course_class")
                 if e.status == EnrollmentStatus.COMPLETED}
    in_progress = {
        e.course_class.course_id
        for e in enrollments.filter(
            status=EnrollmentStatus.ACTIVE, payment_status__in=SEAT_HOLDING_PAYMENT_STATUSES
        ).select_related("course_class")
    }
    open_counts = {}
    for class_course_id in CourseClass.objects.filter(
        billing.open_classes_filter(), course_id__in=course_ids
    ).values_list("course_id", flat=True):
        open_counts[class_course_id] = open_counts.get(class_course_id, 0) + 1

    result = []
    for step in steps:
        if step.course_id in completed:
            state = "completed"
        elif step.course_id in in_progress:
            state = "in_progress"
        else:
            state = "not_started"
        result.append({
            "id": str(step.id),
            "order": step.order,
            "course": {"id": str(step.course_id), "name": step.course.name,
                       "description": step.course.description},
            "state": state,
            "open_classes": open_counts.get(step.course_id, 0),
        })
    done = sum(1 for s in result if s["state"] == "completed")
    following = next((s for s in result if s["state"] != "completed"), None)
    return {
        "completed": done,
        "total": len(result),
        "next_course": following["course"] if following else None,
        "steps": result,
    }


def church_of(person):
    from apps.churches.models import Church

    if person.campus_id:
        return person.campus.church
    return Church.objects.first()
