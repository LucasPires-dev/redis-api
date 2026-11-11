from django.contrib import admin

from .models import (
    CampusCoursesLeadership,
    ClassExpense,
    ClassFeeItem,
    Course,
    CourseClass,
    CourseClassTeacher,
    CourseGeneralLeadership,
    Enrollment,
    EnrollmentItem,
    EnrollmentPayment,
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
    fields = ["person", "status", "payment_status", "amount_cents"]
    readonly_fields = ["payment_status", "amount_cents"]


class ClassFeeItemInline(admin.TabularInline):
    model = ClassFeeItem
    extra = 0


class EnrollmentItemInline(admin.TabularInline):
    model = EnrollmentItem
    extra = 0


class EnrollmentPaymentInline(admin.TabularInline):
    model = EnrollmentPayment
    extra = 0
    readonly_fields = ["method", "amount_cents", "asaas_payment_id", "paid_at", "refunded_at"]


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ["person", "course_class", "status", "payment_status", "amount_cents", "enrolled_at"]
    list_filter = ["payment_status", "status", "course_class__campus"]
    search_fields = ["person__name", "person__email", "course_class__name"]
    raw_id_fields = ["person", "course_class"]
    inlines = [EnrollmentItemInline, EnrollmentPaymentInline]


@admin.register(ClassExpense)
class ClassExpenseAdmin(admin.ModelAdmin):
    list_display = ["description", "course_class", "amount_cents", "date"]
    list_filter = ["course_class__campus"]


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 0


@admin.register(CourseClass)
class CourseClassAdmin(admin.ModelAdmin):
    list_display = ["name", "course", "campus", "start_date", "end_date"]
    list_filter = ["campus"]
    search_fields = ["name", "course__name"]
    inlines = [ClassFeeItemInline, CourseClassTeacherInline, EnrollmentInline, LessonInline]


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
