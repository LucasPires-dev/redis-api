from django.urls import path
from rest_framework.routers import DefaultRouter

from .cell_join import CellJoinRequestViewSet
from .public import explore_view, public_cell_view, public_class_view

from .views import (
    ActivityViewSet,
    CampusJoinRequestViewSet,
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
router.register("campus-join-requests", CampusJoinRequestViewSet, basename="campus-join-request")
router.register("ministries", MinistryViewSet, basename="ministry")
router.register("ministry-general-leaderships", MinistryGeneralLeadershipViewSet, basename="ministry-general-leadership")
router.register("seasons", SeasonViewSet, basename="season")
router.register("cells", CellViewSet, basename="cell")
router.register("cell-meetings", CellMeetingViewSet, basename="cell-meeting")
router.register("cell-visitors", CellVisitorViewSet, basename="cell-visitor")
router.register("cell-join-requests", CellJoinRequestViewSet, basename="cell-join-request")
router.register("cell-supervision-visits", CellSupervisionVisitViewSet, basename="cell-supervision-visit")
router.register("teams", TeamViewSet, basename="team")
router.register("activities", ActivityViewSet, basename="activity")

urlpatterns = [
    path("public/explore/", explore_view, name="public-explore"),
    path("public/cells/<uuid:cell_id>/", public_cell_view, name="public-cell"),
    path("public/classes/<uuid:class_id>/", public_class_view, name="public-class"),
    *router.urls,
]
