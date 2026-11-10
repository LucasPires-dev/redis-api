from rest_framework.routers import DefaultRouter

from .views import CourseClassViewSet, CourseViewSet, LessonViewSet

router = DefaultRouter()
router.register("courses", CourseViewSet, basename="course")
router.register("course-classes", CourseClassViewSet, basename="course-class")
router.register("lessons", LessonViewSet, basename="lesson")

urlpatterns = router.urls
