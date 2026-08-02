from rest_framework.routers import DefaultRouter

from .views import (
    DemandViewSet,
    EventChecklistItemViewSet,
    EventRosterEntryViewSet,
    EventViewSet,
    RundownItemViewSet,
)

router = DefaultRouter()
router.register("production-events", EventViewSet, basename="production-event")
router.register("production-rundown-items", RundownItemViewSet, basename="production-rundown-item")
router.register("production-demands", DemandViewSet, basename="production-demand")
router.register("production-roster-entries", EventRosterEntryViewSet, basename="production-roster-entry")
router.register("production-checklist-items", EventChecklistItemViewSet, basename="production-checklist-item")

urlpatterns = router.urls
