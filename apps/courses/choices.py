from django.db import models


class EnrollmentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    COMPLETED = "completed", "Completed"
    DROPPED = "dropped", "Dropped"


class TeacherRole(models.TextChoices):
    TEACHER = "teacher", "Teacher"
    ASSISTANT = "assistant", "Assistant"
