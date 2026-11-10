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
from django.db import models
from django.db.models import Q

from apps.churches.choices import LeadershipRole

from .choices import EnrollmentStatus, TeacherRole


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
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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

    class Meta:
        ordering = ["person__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["course_class", "person"],
                name="unique_enrollment_per_class",
            ),
        ]

    def __str__(self):
        return f"{self.person} in {self.course_class}"


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
