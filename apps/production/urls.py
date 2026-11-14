from rest_framework.routers import DefaultRouter

from .views import (
    DemandViewSet,
    EventChecklistItemViewSet,
    EventRosterEntryViewSet,
    EventSeriesViewSet,
    EventViewSet,
    RosterSwapViewSet,
    RundownItemViewSet,
)

router = DefaultRouter()
router.register("production-events", EventViewSet, basename="production-event")
router.register("production-series", EventSeriesViewSet, basename="production-series")
router.register("production-rundown-items", RundownItemViewSet, basename="production-rundown-item")
router.register("production-demands", DemandViewSet, basename="production-demand")
router.register("production-roster-entries", EventRosterEntryViewSet, basename="production-roster-entry")
router.register("production-roster-swaps", RosterSwapViewSet, basename="production-roster-swap")
router.register("production-checklist-items", EventChecklistItemViewSet, basename="production-checklist-item")

urlpatterns = router.urls
