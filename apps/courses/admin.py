from django.contrib import admin

from .models import (
    CampusCoursesLeadership,
    Course,
    CourseClass,
    CourseClassTeacher,
    CourseGeneralLeadership,
    Enrollment,
    Lesson,
    LessonAttendance,
)


class CourseClassInline(admin.TabularInline):
    model = CourseClass
    extra = 0


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ["name", "church", "campus", "created_at"]
    list_filter = ["church", "campus"]
    search_fields = ["name"]
    inlines = [CourseClassInline]


class CourseClassTeacherInline(admin.TabularInline):
    model = CourseClassTeacher
    extra = 0


class EnrollmentInline(admin.TabularInline):
    model = Enrollment
    extra = 0


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 0


@admin.register(CourseClass)
class CourseClassAdmin(admin.ModelAdmin):
    list_display = ["name", "course", "campus", "start_date", "end_date"]
    list_filter = ["campus"]
    search_fields = ["name", "course__name"]
    inlines = [CourseClassTeacherInline, EnrollmentInline, LessonInline]


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ["__str__", "date", "course_class"]
    list_filter = ["course_class__campus"]


@admin.register(LessonAttendance)
class LessonAttendanceAdmin(admin.ModelAdmin):
    list_display = ["person", "lesson", "present", "created_at"]
    list_filter = ["present"]


@admin.register(CampusCoursesLeadership)
class CampusCoursesLeadershipAdmin(admin.ModelAdmin):
    list_display = ["person", "campus", "role"]
    list_filter = ["role", "campus"]
    autocomplete_fields = ["person"]


@admin.register(CourseGeneralLeadership)
class CourseGeneralLeadershipAdmin(admin.ModelAdmin):
    """The manager appoints the general courses leader(s) here."""

    list_display = ["church"]
    filter_horizontal = ["leaders"]
