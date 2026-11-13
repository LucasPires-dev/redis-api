"""Models for the courses module.

Hierarchy:

    Course (e.g. "Leadership School")
    └── CourseClass (one offering of a course at a specific campus/schedule)
        ├── CourseClassTeacher (teacher or assistant assigned to the class)
        ├── Enrollment (a person enrolled in the class)
        └── Lesson (one session of the class)
            └── LessonAttendance (who attended a specific lesson)

A Course belongs to a Church and optionally to a Campus. When campus is null
the course is church-wide. A CourseClass always belongs to a Campus (even when
the parent course is church-wide — the class happens at a physical site).

Conventions follow apps.churches.models: UUID PKs, created_at/updated_at
timestamps, Leadership-style join tables for teachers.
"""

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.churches.choices import LeadershipRole

from .choices import (
    SEAT_HOLDING_PAYMENT_STATUSES,
    EnrollmentPaymentStatus,
    EnrollmentStatus,
    PaymentMethod,
    TeacherRole,
)


class Course(models.Model):
    """A course definition (template). CourseClasses are concrete offerings."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    church = models.ForeignKey(
        "churches.Church", on_delete=models.CASCADE, related_name="courses"
    )
    # null = church-wide; set = campus-specific.
    campus = models.ForeignKey(
        "churches.Campus",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="courses",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # Courses to complete before enrolling in this one (see tracks.py).
    # Reverse access: course.required_by.all()
    prerequisites = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="required_by"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["church", "name"],
                name="unique_course_name_per_church",
            ),
        ]

    def __str__(self):
        return self.name


class CourseClass(models.Model):
    """One offering of a course: specific campus, schedule and date range."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="classes")
    campus = models.ForeignKey(
        "churches.Campus", on_delete=models.CASCADE, related_name="course_classes"
    )
    name = models.CharField(max_length=150, help_text="e.g. 'Class A — Evening'")
    # Schedule info (optional, informational). Reuses Weekday from churches.
    weekday = models.PositiveSmallIntegerField(
        choices=[(i, d) for i, d in enumerate(
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"], 1
        )],
        null=True,
        blank=True,
    )
    start_time = models.TimeField(null=True, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)

    # ── Enrollment and fees (see billing.py) ──
    # Members can find the class and enroll themselves (otherwise only the
    # coordinators enroll people).
    self_enrollment = models.BooleanField(default=False)
    max_capacity = models.PositiveIntegerField(null=True, blank=True)  # null = unlimited
    enrollment_deadline = models.DateField(null=True, blank=True)  # last day to self-enroll
    # Days a self-enrollment holds the seat while unpaid; also the charge due date.
    reservation_days = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(30)]
    )
    max_installments = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(12)]
    )
    # Paid enrollments cancelled before the class starts get a full refund;
    # after it starts, only if a coordinator overrides it.
    refund_before_start = models.BooleanField(default=True)
    # Completion: the share of the lessons a student must attend (see completion.py).
    min_attendance_percent = models.PositiveSmallIntegerField(
        default=75, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def base_price_cents(self):
        """What every enrollment pays at least: the sum of the required items."""
        return sum(item.price_cents for item in self.fee_items.all() if item.required)

    @property
    def is_paid(self):
        return any(item.price_cents for item in self.fee_items.all())

    class Meta:
        verbose_name = "course class"
        verbose_name_plural = "course classes"
        ordering = ["course", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "name"],
                name="unique_class_name_per_course",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.course.name})"


class CourseClassTeacher(models.Model):
    """Assigns a person as teacher or assistant of a course class."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course_class = models.ForeignKey(
        CourseClass, on_delete=models.CASCADE, related_name="teachers"
    )
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="teaching_assignments",
    )
    role = models.CharField(max_length=16, choices=TeacherRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["course_class", "person"],
                name="unique_teacher_per_class",
            ),
        ]

    def __str__(self):
        return f"{self.person} — {self.get_role_display()} at {self.course_class}"


class Enrollment(models.Model):
    """A person enrolled in a specific course class."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course_class = models.ForeignKey(
        CourseClass, on_delete=models.CASCADE, related_name="enrollments"
    )
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="course_enrollments",
    )
    status = models.CharField(
        max_length=16,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ACTIVE,
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    # ── Payment (see billing.py) ──
    payment_status = models.CharField(
        max_length=16, choices=EnrollmentPaymentStatus.choices, default=EnrollmentPaymentStatus.FREE
    )
    # Frozen at enrollment: later price changes don't touch existing enrollments.
    gross_cents = models.PositiveIntegerField(default=0)  # sum of the chosen items
    discount_cents = models.PositiveIntegerField(default=0)  # scholarship
    amount_cents = models.PositiveIntegerField(default=0)  # gross - discount: what is owed
    refunded_cents = models.PositiveIntegerField(default=0)
    scholarship_reason = models.CharField(max_length=200, blank=True)
    scholarship_granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    installments = models.PositiveSmallIntegerField(default=1)
    # The current Asaas charge (first installment when paying in installments).
    asaas_payment_id = models.CharField(max_length=100, blank=True)
    asaas_installment_id = models.CharField(max_length=100, blank=True)
    payment_url = models.URLField(blank=True)
    # Self-enrollments hold the seat until then while nothing was paid.
    expires_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)  # when fully paid
    # An installment went overdue (the enrollment keeps its seat).
    has_overdue_installment = models.BooleanField(default=False)
    material_delivered_at = models.DateTimeField(null=True, blank=True)
    material_delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    # Set when the enrollment is completed; printed on the certificate and
    # checked on the public verification page (see certificates.py).
    certificate_code = models.CharField(max_length=16, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["person__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["certificate_code"],
                condition=~models.Q(certificate_code=""),
                name="unique_enrollment_certificate_code",
            ),
            # One active enrollment per person and class. Cancelled, expired and
            # refunded ones stay as history, so the person can enroll again.
            models.UniqueConstraint(
                fields=["course_class", "person"],
                condition=models.Q(payment_status__in=SEAT_HOLDING_PAYMENT_STATUSES)
                & ~models.Q(status=EnrollmentStatus.DROPPED),
                name="unique_active_enrollment_per_class",
            ),
        ]

    def __str__(self):
        return f"{self.person} in {self.course_class}"

    @property
    def paid_cents(self):
        return sum(p.amount_cents for p in self.payments.all())

    @property
    def balance_cents(self):
        """What is still owed (0 once paid, waived or closed)."""
        if self.payment_status in (EnrollmentPaymentStatus.PENDING, EnrollmentPaymentStatus.PARTIAL):
            return max(self.amount_cents - self.paid_cents, 0)
        return 0


class ClassFeeItem(models.Model):
    """Something an enrollment in the class pays for: a required item (handout,
    materials) or an optional one (t-shirt). `options` lists the choices the
    person must pick from, e.g. sizes ["P", "M", "G"]."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course_class = models.ForeignKey(CourseClass, on_delete=models.CASCADE, related_name="fee_items")
    name = models.CharField(max_length=100)
    price_cents = models.PositiveIntegerField()
    required = models.BooleanField(default=True)
    options = models.JSONField(default=list, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["course_class", "name"], name="unique_fee_item_name_per_class"),
        ]

    def __str__(self):
        return f"{self.name} ({self.course_class})"


class EnrollmentItem(models.Model):
    """An item chosen in an enrollment, with name and price frozen at that time
    (the class's item may later change or be removed)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="items")
    fee_item = models.ForeignKey(
        ClassFeeItem, null=True, blank=True, on_delete=models.SET_NULL, related_name="enrollment_items"
    )
    name = models.CharField(max_length=100)
    price_cents = models.PositiveIntegerField()
    option = models.CharField(max_length=50, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name}{f' ({self.option})' if self.option else ''} — {self.enrollment}"


class EnrollmentPayment(models.Model):
    """Money received for an enrollment: one row per paid Asaas charge (each
    installment) or per payment recorded by hand. The ledger behind
    `paid_cents`; the unique Asaas id makes a repeated webhook harmless."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=16, choices=PaymentMethod.choices)
    amount_cents = models.PositiveIntegerField()
    asaas_payment_id = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=200, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    paid_at = models.DateTimeField()
    # Set when the money went back to the person (Asaas refund or in person).
    refunded_at = models.DateTimeField(null=True, blank=True)
    refund_note = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["paid_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["asaas_payment_id"],
                condition=~models.Q(asaas_payment_id=""),
                name="unique_enrollment_payment_asaas_id",
            ),
        ]

    def __str__(self):
        return f"R$ {self.amount_cents / 100:.2f} — {self.enrollment}"


class ClassExpense(models.Model):
    """Money spent on a class (handouts, coffee break), for its balance."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course_class = models.ForeignKey(CourseClass, on_delete=models.CASCADE, related_name="expenses")
    description = models.CharField(max_length=200)
    amount_cents = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    date = models.DateField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.description} — R$ {self.amount_cents / 100:.2f}"


class Lesson(models.Model):
    """One session/lesson of a course class."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course_class = models.ForeignKey(
        CourseClass, on_delete=models.CASCADE, related_name="lessons"
    )
    # Order within the class (lesson 1, 2, 3...).
    order = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=200, blank=True)
    date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["course_class", "order"],
                name="unique_lesson_order_per_class",
            ),
        ]

    def __str__(self):
        label = self.title or f"Lesson {self.order}"
        return f"{label} — {self.course_class}"


class LessonAttendance(models.Model):
    """Whether a specific enrolled person attended a specific lesson."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lesson = models.ForeignKey(
        Lesson, on_delete=models.CASCADE, related_name="attendances"
    )
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lesson_attendances",
    )
    present = models.BooleanField(default=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["lesson", "person"],
                name="unique_attendance_per_lesson",
            ),
        ]

    def __str__(self):
        status = "present" if self.present else "absent"
        return f"{self.person} — {status} at {self.lesson}"


# --- Growth tracks --------------------------------------------------------------------


class CourseTrack(models.Model):
    """A growth track (trilha): the church's courses in the order a person
    should take them, e.g. Membresia → Fundamentos → Liderança. Kept by the
    church-wide coordination; every member sees their progress in it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    church = models.ForeignKey("churches.Church", on_delete=models.CASCADE, related_name="course_tracks")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["church", "name"], name="unique_track_name_per_church"),
        ]

    def __str__(self):
        return self.name


class CourseTrackStep(models.Model):
    """One course of a track, at its position (1, 2, 3...)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    track = models.ForeignKey(CourseTrack, on_delete=models.CASCADE, related_name="steps")
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="track_steps")
    order = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["track", "course"], name="unique_course_per_track"),
        ]

    def __str__(self):
        return f"{self.order}. {self.course} ({self.track})"


# --- Course responsibilities ----------------------------------------------------------
# Same general + local pair used by cells (CellGeneralLeadership / CampusCellsLeadership):
# the general one acts on every campus, the local one only on their campus.


class CourseGeneralLeadership(models.Model):
    """The people in charge of courses across the whole church (responsável geral
    pelos cursos). One record per church; `leaders` may hold more than one person.

    Appointed by the manager (Django Admin). They manage every course and class on
    every campus, including church-wide courses, and can appoint each campus's
    courses leader (CampusCoursesLeadership).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    church = models.OneToOneField(
        "churches.Church", on_delete=models.CASCADE, related_name="course_general_leadership"
    )
    # Reverse access: person.course_general_leaderships.all()
    leaders = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="course_general_leaderships"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Responsável geral pelos cursos de {self.church}"


class CampusCoursesLeadership(models.Model):
    """Links a person to a campus as the one in charge of that campus's courses
    (responsável pelos cursos do campus), as leader or co-leader.

    Appointed by the campus leader (or the manager / general courses leader).
    Within that campus they create and manage courses and every class held
    there. The campus leader keeps the same powers. The person must belong to
    the campus.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Reverse access: campus.courses_leaderships.all()
    campus = models.ForeignKey(
        "churches.Campus", on_delete=models.CASCADE, related_name="courses_leaderships"
    )
    # Reverse access: person.campus_courses_leaderships.all()
    person = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="campus_courses_leaderships"
    )
    role = models.CharField(max_length=16, choices=LeadershipRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["campus", "person"], name="unique_campus_courses_leadership_per_person"
            ),
        ]

    def __str__(self):
        return f"{self.person} — {self.get_role_display()} dos cursos de {self.campus}"
