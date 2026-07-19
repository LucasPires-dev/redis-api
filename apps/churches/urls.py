from rest_framework.routers import DefaultRouter

from .views import ActivityViewSet, CampusViewSet, ChurchViewSet, MinistryViewSet, TeamViewSet

router = DefaultRouter()
router.register("churches", ChurchViewSet, basename="church")
router.register("campuses", CampusViewSet, basename="campus")
router.register("ministries", MinistryViewSet, basename="ministry")
router.register("teams", TeamViewSet, basename="team")
router.register("activities", ActivityViewSet, basename="activity")

urlpatterns = router.urls
