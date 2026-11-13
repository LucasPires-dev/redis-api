from rest_framework.routers import DefaultRouter

from .views import (
    ActivityViewSet,
    CampusViewSet,
    CellMeetingViewSet,
    CellSupervisionVisitViewSet,
    CellVisitorViewSet,
    CellViewSet,
    ChurchViewSet,
    MinistryGeneralLeadershipViewSet,
    MinistryViewSet,
    SeasonViewSet,
    TeamViewSet,
)

router = DefaultRouter()
router.register("churches", ChurchViewSet, basename="church")
router.register("campuses", CampusViewSet, basename="campus")
router.register("ministries", MinistryViewSet, basename="ministry")
router.register("ministry-general-leaderships", MinistryGeneralLeadershipViewSet, basename="ministry-general-leadership")
router.register("seasons", SeasonViewSet, basename="season")
router.register("cells", CellViewSet, basename="cell")
router.register("cell-meetings", CellMeetingViewSet, basename="cell-meeting")
router.register("cell-visitors", CellVisitorViewSet, basename="cell-visitor")
router.register("cell-supervision-visits", CellSupervisionVisitViewSet, basename="cell-supervision-visit")
router.register("teams", TeamViewSet, basename="team")
router.register("activities", ActivityViewSet, basename="activity")

urlpatterns = router.urls
