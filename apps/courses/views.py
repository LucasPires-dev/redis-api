from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    Course,
    CourseClass,
    CourseClassTeacher,
    Enrollment,
    Lesson,
    LessonAttendance,
)
from .permissions import (
    CourseClassPermission,
    CoursePermission,
    LessonPermission,
    can_manage_class,
    can_manage_course,
    scope_classes,
    scope_courses,
)
from .serializers import (
    BulkAttendanceInputSerializer,
    CourseClassSerializer,
    CourseSerializer,
    EnrollmentInputSerializer,
    EnrollmentSerializer,
    LessonAttendanceSerializer,
    LessonSerializer,
    PersonMiniSerializer,
    TeacherInputSerializer,
    TeacherSerializer,
)

Person = get_user_model()

PEOPLE_SEARCH_LIMIT = 20


def _search_people(qs, request):
    term = (request.query_params.get("q") or "").strip()
    if len(term) < 2:
        return Response([])
    qs = qs.filter(Q(name__icontains=term) | Q(email__icontains=term)).order_by("name")
    return Response(PersonMiniSerializer(qs[:PEOPLE_SEARCH_LIMIT], many=True).data)


class CourseViewSet(viewsets.ModelViewSet):
    serializer_class = CourseSerializer
    permission_classes = [CoursePermission]

    def get_queryset(self):
        qs = Course.objects.select_related("campus").prefetch_related("classes")
        return scope_courses(qs, self.request.user)

    def perform_create(self, serializer):
        from apps.churches.models import Church
        user = self.request.user
        church = user.campus.church if user.campus_id else Church.objects.first()
        serializer.save(church=church, created_by=user)


class CourseClassViewSet(viewsets.ModelViewSet):
    serializer_class = CourseClassSerializer
    permission_classes = [CourseClassPermission]

    def get_queryset(self):
        qs = CourseClass.objects.select_related("course", "campus").prefetch_related(
            "teachers__person", "enrollments", "lessons"
        )
        course_id = self.request.query_params.get("course_id")
        if course_id:
            qs = qs.filter(course_id=course_id)
        return scope_classes(qs, self.request.user)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    # ── Teachers ────────────────────────────────────────────────────────

    @action(detail=True, methods=["get", "post"], url_path="teachers")
    def teachers(self, request, pk=None):
        course_class = self.get_object()
        if request.method == "GET":
            return Response(
                TeacherSerializer(course_class.teachers.select_related("person"), many=True).data
            )
        if not can_manage_class(request.user, course_class):
            raise PermissionDenied()
        ser = TeacherInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=ser.validated_data["person_id"])
        obj, created = CourseClassTeacher.objects.get_or_create(
            course_class=course_class,
            person=person,
            defaults={"role": ser.validated_data["role"]},
        )
        if not created:
            obj.role = ser.validated_data["role"]
            obj.save(update_fields=["role"])
        return Response(
            TeacherSerializer(obj).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["delete"], url_path=r"teachers/(?P<teacher_id>[^/.]+)")
    def remove_teacher(self, request, pk=None, teacher_id=None):
        course_class = self.get_object()
        if not can_manage_class(request.user, course_class):
            raise PermissionDenied()
        CourseClassTeacher.objects.filter(
            course_class=course_class, person_id=teacher_id
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── Enrollments ─────────────────────────────────────────────────────

    @action(detail=True, methods=["get", "post"], url_path="enrollments")
    def enrollments(self, request, pk=None):
        course_class = self.get_object()
        if request.method == "GET":
            return Response(
                EnrollmentSerializer(
                    course_class.enrollments.select_related("person"), many=True
                ).data
            )
        if not can_manage_class(request.user, course_class):
            raise PermissionDenied()
        ser = EnrollmentInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        person = get_object_or_404(Person, pk=ser.validated_data["person_id"])
        obj, created = Enrollment.objects.get_or_create(
            course_class=course_class,
            person=person,
            defaults={"status": ser.validated_data["status"]},
        )
        if not created:
            obj.status = ser.validated_data["status"]
            obj.save(update_fields=["status"])
        return Response(
            EnrollmentSerializer(obj).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=["delete"], url_path=r"enrollments/(?P<person_id>[^/.]+)")
    def remove_enrollment(self, request, pk=None, person_id=None):
        course_class = self.get_object()
        if not can_manage_class(request.user, course_class):
            raise PermissionDenied()
        Enrollment.objects.filter(
            course_class=course_class, person_id=person_id
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ── People search (for enrollment/teacher pickers) ──────────────────

    @action(detail=True, methods=["get"], url_path="search-people")
    def search_people(self, request, pk=None):
        self.get_object()  # permission check
        return _search_people(Person.objects.all(), request)


class LessonViewSet(viewsets.ModelViewSet):
    serializer_class = LessonSerializer
    permission_classes = [LessonPermission]

    def get_queryset(self):
        qs = Lesson.objects.select_related("course_class").prefetch_related("attendances")
        class_id = self.request.query_params.get("course_class_id")
        if class_id:
            qs = qs.filter(course_class_id=class_id)
        return qs

    def perform_create(self, serializer):
        serializer.save()

    # ── Attendance ──────────────────────────────────────────────────────

    @action(detail=True, methods=["get", "post"], url_path="attendance")
    def attendance(self, request, pk=None):
        lesson = self.get_object()
        if request.method == "GET":
            return Response(
                LessonAttendanceSerializer(
                    lesson.attendances.select_related("person"), many=True
                ).data
            )
        if not can_manage_class(request.user, lesson.course_class):
            raise PermissionDenied()
        ser = BulkAttendanceInputSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        results = []
        for item in ser.validated_data["attendances"]:
            person = get_object_or_404(Person, pk=item["person_id"])
            obj, _ = LessonAttendance.objects.update_or_create(
                lesson=lesson,
                person=person,
                defaults={
                    "present": item["present"],
                    "recorded_by": request.user,
                },
            )
            results.append(obj)
        return Response(
            LessonAttendanceSerializer(results, many=True).data,
            status=status.HTTP_200_OK,
        )
