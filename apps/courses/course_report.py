"""Course reports for coordination, and the students a class should care for.

One row per class the person coordinates: enrollment, attendance,
completion, dropouts and money. Everything is fetched in a handful of queries
for all the classes at once (no per-class summaries).

Definitions:
- enrolled: holding a seat (active or completed);
- dropouts: left after enrolling — dropped, cancelled or refunded. A
  reservation that expired unpaid never started, so it isn't a dropout;
- attendance: lessons attended out of those whose attendance was taken for
  the student (see completion.py), summed over the class;
- to care for: an active student with two or more absences in a row in the
  latest lessons, or below the class's minimum after at least three lessons.
"""

from collections import defaultdict

from django.db.models import Sum
from django.utils import timezone

from apps.churches.models import Campus

from . import billing
from .choices import SEAT_HOLDING_PAYMENT_STATUSES, EnrollmentPaymentStatus, EnrollmentStatus
from .models import CourseClass, Enrollment, EnrollmentPayment, LessonAttendance
from .permissions import can_coordinate_courses, coordinated_course_campuses

DROPOUT_PAYMENT_STATUSES = [EnrollmentPaymentStatus.CANCELLED, EnrollmentPaymentStatus.REFUNDED]
OWING_PAYMENT_STATUSES = [EnrollmentPaymentStatus.PENDING, EnrollmentPaymentStatus.PARTIAL]

# A student is flagged after this many absences in a row...
ABSENCES_IN_A_ROW = 2
# ...or when below the minimum once this many lessons counted for them.
MIN_LESSONS_FOR_BELOW_MINIMUM = 3


def can_see_reports(person):
    return can_coordinate_courses(person) or coordinated_course_campuses(person).exists()


def report_campuses(person):
    """Campuses whose classes the person sees in the report."""
    if can_coordinate_courses(person):
        church_id = getattr(person.campus, "church_id", None)
        qs = Campus.objects.all()
        return qs.filter(church_id=church_id) if church_id else qs
    return coordinated_course_campuses(person)


def class_state(course_class, today):
    if course_class.start_date and today < course_class.start_date:
        return "upcoming"
    if course_class.end_date and today > course_class.end_date:
        return "finished"
    return "ongoing"


def rate(part, whole):
    return round(100 * part / whole) if whole else None


# ── Students to care for ─────────────────────────────────────────────────────


def care_entry(history, min_percent):
    """Why a student needs attention, from their attendance in lesson order
    ([(lesson_order, lesson_date, present), ...]). None when they're fine."""
    counted = len(history)
    present = sum(1 for _, _, p in history if p)
    streak = 0
    for _, _, p in reversed(history):
        if p:
            break
        streak += 1
    reasons = []
    if streak >= ABSENCES_IN_A_ROW:
        reasons.append("absences_in_a_row")
    if counted >= MIN_LESSONS_FOR_BELOW_MINIMUM and present * 100 < min_percent * counted:
        reasons.append("below_minimum")
    if not reasons:
        return None
    last_present = max((d for _, d, p in history if p and d), default=None)
    return {
        "reasons": reasons,
        "absences_in_a_row": streak,
        "attendance": {"present": present, "counted": counted, "percent": rate(present, counted)},
        "last_present_on": last_present,
    }


def _histories(class_ids):
    """{class_id: {person_id: [(order, date, present), ...]}} in lesson order."""
    rows = (
        LessonAttendance.objects.filter(lesson__course_class_id__in=class_ids)
        .values_list("lesson__course_class_id", "person_id", "lesson__order", "lesson__date", "present")
        .order_by("lesson__order")
    )
    histories = defaultdict(lambda: defaultdict(list))
    for class_id, person_id, order, date, present in rows:
        histories[class_id][person_id].append((order, date, present))
    return histories


def students_to_care(course_class):
    """The class's active students who need attention, the most urgent first."""
    histories = _histories([course_class.id])[course_class.id]
    enrollments = billing.active_enrollments(course_class).filter(
        status=EnrollmentStatus.ACTIVE
    ).select_related("person")
    result = []
    for enrollment in enrollments:
        entry = care_entry(histories.get(enrollment.person_id, []), course_class.min_attendance_percent)
        if entry:
            person = enrollment.person
            result.append({"person": {"id": str(person.id), "name": person.name, "email": person.email}, **entry})
    result.sort(key=lambda e: (-e["absences_in_a_row"], e["attendance"]["percent"] or 0, e["person"]["name"]))
    return result


# ── Coordination report ──────────────────────────────────────────────────────


def build_report(person, campus_id=None, state=None, today=None):
    today = today or timezone.localdate()
    campuses = report_campuses(person)
    classes = list(
        CourseClass.objects.filter(campus__in=campuses)
        .select_related("course", "campus")
        .prefetch_related("lessons")
        .order_by("-start_date", "course__name", "name")
    )
    if campus_id:
        classes = [c for c in classes if str(c.campus_id) == str(campus_id)]
    if state:
        classes = [c for c in classes if class_state(c, today) == state]
    class_ids = [c.id for c in classes]

    billing.expire_overdue(Enrollment.objects.filter(course_class_id__in=class_ids))
    enrollments = list(
        Enrollment.objects.filter(course_class_id__in=class_ids).values(
            "id", "course_class_id", "person_id", "status", "payment_status", "amount_cents"
        )
    )
    paid = dict(
        EnrollmentPayment.objects.filter(enrollment__course_class_id__in=class_ids)
        .values_list("enrollment_id")
        .annotate(total=Sum("amount_cents"))
    )
    received = dict(
        EnrollmentPayment.objects.filter(
            enrollment__course_class_id__in=class_ids, refunded_at__isnull=True
        )
        .values_list("enrollment__course_class_id")
        .annotate(total=Sum("amount_cents"))
    )
    histories = _histories(class_ids)
    by_class = defaultdict(list)
    for e in enrollments:
        by_class[e["course_class_id"]].append(e)

    rows = []
    for c in classes:
        holding = [
            e for e in by_class[c.id]
            if e["payment_status"] in SEAT_HOLDING_PAYMENT_STATUSES and e["status"] != EnrollmentStatus.DROPPED
        ]
        active = [e for e in holding if e["status"] == EnrollmentStatus.ACTIVE]
        completed = [e for e in holding if e["status"] == EnrollmentStatus.COMPLETED]
        dropouts = [
            e for e in by_class[c.id]
            if e["status"] == EnrollmentStatus.DROPPED or e["payment_status"] in DROPOUT_PAYMENT_STATUSES
        ]
        started = len(holding) + len(dropouts)

        class_histories = histories.get(c.id, {})
        present = sum(1 for h in class_histories.values() for _, _, p in h if p)
        counted = sum(len(h) for h in class_histories.values())
        lessons_held = len({order for h in class_histories.values() for order, _, _ in h})
        to_care = sum(
            1 for e in active
            if care_entry(class_histories.get(e["person_id"], []), c.min_attendance_percent)
        )
        expected = sum(e["amount_cents"] for e in holding)
        outstanding = sum(
            max(e["amount_cents"] - paid.get(e["id"], 0), 0)
            for e in holding if e["payment_status"] in OWING_PAYMENT_STATUSES
        )

        rows.append({
            "id": str(c.id),
            "course": {"id": str(c.course_id), "name": c.course.name},
            "name": c.name,
            "campus": {"id": str(c.campus_id), "name": c.campus.name},
            "start_date": c.start_date,
            "end_date": c.end_date,
            "state": class_state(c, today),
            "min_attendance_percent": c.min_attendance_percent,
            "capacity": c.max_capacity,
            "enrolled": len(holding),
            "active": len(active),
            "completed": len(completed),
            "dropouts": len(dropouts),
            "completion_rate": rate(len(completed), started),
            "dropout_rate": rate(len(dropouts), started),
            "lesson_count": len(c.lessons.all()),
            "lessons_held": lessons_held,
            "attendance_rate": rate(present, counted),
            "to_care": to_care,
            "is_paid": expected > 0 or c.id in received,
            "expected_cents": expected,
            "received_cents": received.get(c.id, 0),
            "outstanding_cents": outstanding,
        })

    present_total = sum(
        1 for c in classes for h in histories.get(c.id, {}).values() for _, _, p in h if p
    )
    counted_total = sum(len(h) for c in classes for h in histories.get(c.id, {}).values())
    started_total = sum(r["enrolled"] + r["dropouts"] for r in rows)
    totals = {
        "classes": len(rows),
        "ongoing": sum(1 for r in rows if r["state"] == "ongoing"),
        "enrolled": sum(r["enrolled"] for r in rows),
        "active": sum(r["active"] for r in rows),
        "completed": sum(r["completed"] for r in rows),
        "dropouts": sum(r["dropouts"] for r in rows),
        "completion_rate": rate(sum(r["completed"] for r in rows), started_total),
        "dropout_rate": rate(sum(r["dropouts"] for r in rows), started_total),
        "attendance_rate": rate(present_total, counted_total),
        "to_care": sum(r["to_care"] for r in rows),
        "expected_cents": sum(r["expected_cents"] for r in rows),
        "received_cents": sum(r["received_cents"] for r in rows),
        "outstanding_cents": sum(r["outstanding_cents"] for r in rows),
    }
    return {
        "campuses": [{"id": str(c.id), "name": c.name} for c in campuses.order_by("name")],
        "totals": totals,
        "classes": rows,
    }
