from rest_framework.routers import DefaultRouter

from .views import (
    ActivityViewSet,
    CampusViewSet,
    CellViewSet,
    ChurchViewSet,
    MinistryGeneralLeadershipViewSet,
    MinistryViewSet,
    TeamViewSet,
)

router = DefaultRouter()
router.register("churches", ChurchViewSet, basename="church")
router.register("campuses", CampusViewSet, basename="campus")
router.register("ministries", MinistryViewSet, basename="ministry")
router.register("ministry-general-leaderships", MinistryGeneralLeadershipViewSet, basename="ministry-general-leadership")
router.register("cells", CellViewSet, basename="cell")
router.register("teams", TeamViewSet, basename="team")
router.register("activities", ActivityViewSet, basename="activity")

urlpatterns = router.urls
