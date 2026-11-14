from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission

from apps.churches.models import Campus
from apps.churches.permissions import can_manage_campus

from .models import CampusCoursesLeadership, Course, CourseGeneralLeadership

# Access levels for courses (same general + local pattern as cells):
# - manager / general courses leader: every course and class, on every campus;
# - campus leader / campus courses leader: courses of their campus and every
#   class held there (including classes of church-wide courses);
# - teacher: only the classes they teach.


def is_courses_general_leader(person) -> bool:
    """General courses leader — responsável geral pelos cursos (CourseGeneralLeadership)."""
    if not person.is_authenticated:
        return False
    return CourseGeneralLeadership.objects.filter(leaders=person).exists()


def can_coordinate_courses(person) -> bool:
    """Church-wide course powers: manager or general courses leader."""
    if not person.is_authenticated:
        return False
    return person.is_manager or is_courses_general_leader(person)


def coordinated_course_campuses(person):
    """Campuses whose courses the person runs locally: as campus leader or as
    the campus's courses leader (CampusCoursesLeadership)."""
    if not person.is_authenticated:
        return Campus.objects.none()
    return Campus.objects.filter(
        Q(leaderships__person=person) | Q(courses_leaderships__person=person)
    ).distinct()


def can_coordinate_campus_courses(person, campus) -> bool:
    """Course powers within one campus: church-wide coordinators, the campus
    leader, or the campus's courses leader."""
    if not person.is_authenticated:
        return False
    if can_coordinate_courses(person) or can_manage_campus(person, campus):
        return True
    return CampusCoursesLeadership.objects.filter(campus=campus, person=person).exists()


def can_assign_campus_courses_leader(person, campus) -> bool:
    """Who appoints the campus's courses leader: the level above — the campus
    leader, the manager or the general courses leader."""
    if not person.is_authenticated:
        return False
    return can_coordinate_courses(person) or can_manage_campus(person, campus)


def can_manage_course(person, course) -> bool:
    """Church-wide coordinators for any course; local coordinators of the
    course's campus for campus-scoped courses. A church-wide course (campus=null)
    belongs to the church-wide level only."""
    if not person.is_authenticated:
        return False
    if can_coordinate_courses(person):
        return True
    if course.campus_id:
        return can_coordinate_campus_courses(person, course.campus)
    return False


def can_manage_class(person, course_class) -> bool:
    """Anyone who can manage the parent course, the local coordinators of the
    campus where the class is held (even for a church-wide course), or a
    teacher of this class."""
    if can_manage_course(person, course_class.course):
        return True
    if can_coordinate_campus_courses(person, course_class.campus):
        return True
    return course_class.teachers.filter(person=person).exists()


def can_manage_class_finances(person, course_class) -> bool:
    """Fees, scholarships, payments, refunds and expenses: the course's and the
    campus's coordinators — not the teachers (they handle lessons, attendance
    and material delivery)."""
    if can_manage_course(person, course_class.course):
        return True
    return can_coordinate_campus_courses(person, course_class.campus)


def scope_courses(qs, person):
    """Courses visible to the person: everything for church-wide coordinators;
    otherwise church-wide courses, the courses of campuses they coordinate and
    the courses of the classes they teach (even a campus course elsewhere)."""
    if can_coordinate_courses(person):
        return qs
    campus_ids = coordinated_course_campuses(person).values("id")
    return qs.filter(
        Q(campus__isnull=True) | Q(campus_id__in=campus_ids) | Q(classes__teachers__person=person)
    ).distinct()


def scope_classes(qs, person):
    """Classes visible to the person: all for church-wide coordinators; for
    local coordinators, classes held at their campus (including those of
    church-wide courses); for teachers, classes they teach."""
    if can_coordinate_courses(person):
        return qs
    campus_ids = coordinated_course_campuses(person).values("id")
    return qs.filter(Q(campus_id__in=campus_ids) | Q(teachers__person=person)).distinct()


class CoursePermission(BasePermission):
    """Read: anyone authenticated (queryset is scoped in the view).
    Create: church-wide coordinators (any course) or the campus's local
    coordinators (campus-scoped courses).
    Update/delete: can_manage_course."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            campus_id = request.data.get("campus_id")
            if not campus_id:
                # A church-wide course belongs to the church-wide level.
                return can_coordinate_courses(request.user)
            campus = Campus.objects.filter(pk=campus_id).first()
            if campus is None:
                return True  # let serializer produce a 400
            return can_coordinate_campus_courses(request.user, campus)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_course(request.user, obj)


class CourseClassPermission(BasePermission):
    """Read: anyone who can see the class (queryset already scoped).
    Create: can_manage_course on the parent course, or the local coordinators of
    the campus in the payload (a church-wide course's class at their campus).
    Update/manage teachers/enrollments/lessons: can_manage_class."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            course_id = request.data.get("course_id")
            course = Course.objects.filter(pk=course_id).first() if course_id else None
            if course is None:
                return True
            if can_manage_course(request.user, course):
                return True
            # Local coordinators may open a class of a church-wide course at their campus.
            campus_id = request.data.get("campus_id")
            campus = Campus.objects.filter(pk=campus_id).first() if campus_id else None
            return campus is not None and can_coordinate_campus_courses(request.user, campus)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_class(request.user, obj)


class LessonPermission(BasePermission):
    """Read: whoever sees the class (queryset scoped in the view). Create and
    write: can_manage_class on the lesson's class."""

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if view.action == "create":
            from .models import CourseClass

            try:
                course_class = CourseClass.objects.filter(pk=request.data.get("course_class_id")).first()
            except (ValueError, DjangoValidationError):  # not a UUID
                course_class = None
            if course_class is None:
                return True  # let the serializer produce a 400
            return can_manage_class(request.user, course_class)
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return can_manage_class(request.user, obj.course_class)
