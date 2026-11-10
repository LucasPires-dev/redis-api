from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.churches.serializers import CampusMiniSerializer

from .choices import EnrollmentStatus, TeacherRole
from .models import (
    Course,
    CourseClass,
    CourseClassTeacher,
    Enrollment,
    Lesson,
    LessonAttendance,
)

Person = get_user_model()


class PersonMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Person
        fields = ["id", "name", "email"]


# ── Course ──────────────────────────────────────────────────────────────────


class CourseSerializer(serializers.ModelSerializer):
    campus = CampusMiniSerializer(read_only=True)
    campus_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    class_count = serializers.SerializerMethodField()
    total_enrolled = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = [
            "id", "name", "description", "campus", "campus_id",
            "class_count", "total_enrolled", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_class_count(self, obj):
        return obj.classes.count()

    def get_total_enrolled(self, obj):
        return Enrollment.objects.filter(
            course_class__course=obj,
            status=EnrollmentStatus.ACTIVE,
        ).count()


# ── CourseClass (turma) ─────────────────────────────────────────────────────


class TeacherSerializer(serializers.ModelSerializer):
    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = CourseClassTeacher
        fields = ["id", "person", "role"]


class TeacherInputSerializer(serializers.Serializer):
    person_id = serializers.UUIDField()
    role = serializers.ChoiceField(choices=TeacherRole.choices, default=TeacherRole.TEACHER)


class CourseClassSerializer(serializers.ModelSerializer):
    campus = CampusMiniSerializer(read_only=True)
    campus_id = serializers.UUIDField(write_only=True)
    course_id = serializers.UUIDField(write_only=True)
    course_name = serializers.CharField(source="course.name", read_only=True)
    teachers = TeacherSerializer(many=True, read_only=True)
    enrolled_count = serializers.SerializerMethodField()
    lesson_count = serializers.SerializerMethodField()

    class Meta:
        model = CourseClass
        fields = [
            "id", "course_id", "course_name", "campus", "campus_id",
            "name", "weekday", "start_time", "start_date", "end_date",
            "description", "teachers", "enrolled_count", "lesson_count",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_enrolled_count(self, obj):
        return obj.enrollments.filter(status=EnrollmentStatus.ACTIVE).count()

    def get_lesson_count(self, obj):
        return obj.lessons.count()


# ── Enrollment ──────────────────────────────────────────────────────────────


class EnrollmentSerializer(serializers.ModelSerializer):
    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = Enrollment
        fields = ["id", "person", "status", "enrolled_at", "completed_at"]


class EnrollmentInputSerializer(serializers.Serializer):
    person_id = serializers.UUIDField()
    status = serializers.ChoiceField(
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ACTIVE,
    )


# ── Lesson ──────────────────────────────────────────────────────────────────


class LessonSerializer(serializers.ModelSerializer):
    course_class_id = serializers.UUIDField(write_only=True)
    attendance_count = serializers.SerializerMethodField()
    total_enrolled = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = [
            "id", "course_class_id", "order", "title", "date", "notes",
            "attendance_count", "total_enrolled", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def get_attendance_count(self, obj):
        return obj.attendances.filter(present=True).count()

    def get_total_enrolled(self, obj):
        return obj.course_class.enrollments.filter(status=EnrollmentStatus.ACTIVE).count()


# ── Attendance ──────────────────────────────────────────────────────────────


class LessonAttendanceSerializer(serializers.ModelSerializer):
    person = PersonMiniSerializer(read_only=True)

    class Meta:
        model = LessonAttendance
        fields = ["id", "person", "present", "created_at"]


class BulkAttendanceInputSerializer(serializers.Serializer):
    """Receives the full attendance list for one lesson in a single request."""
    attendances = serializers.ListField(
        child=serializers.DictField(), min_length=0
    )

    def validate_attendances(self, value):
        for item in value:
            if "person_id" not in item:
                raise serializers.ValidationError("Each item needs person_id.")
            if "present" not in item:
                item["present"] = True
        return value
