from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    CertificateVerifyView,
    ClassExpenseViewSet,
    CourseClassViewSet,
    CourseTrackViewSet,
    CourseViewSet,
    EnrollmentViewSet,
    LessonViewSet,
)

router = DefaultRouter()
router.register("courses", CourseViewSet, basename="course")
router.register("course-classes", CourseClassViewSet, basename="course-class")
router.register("course-enrollments", EnrollmentViewSet, basename="course-enrollment")
router.register("class-expenses", ClassExpenseViewSet, basename="class-expense")
router.register("lessons", LessonViewSet, basename="lesson")
router.register("course-tracks", CourseTrackViewSet, basename="course-track")

urlpatterns = [
    path("certificates/<str:code>/", CertificateVerifyView.as_view(), name="certificate-verify"),
    *router.urls,
]
