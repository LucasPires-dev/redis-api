from rest_framework.routers import DefaultRouter

from .views import DemandViewSet, EventViewSet, RundownItemViewSet

router = DefaultRouter()
router.register("production-events", EventViewSet, basename="production-event")
router.register("production-rundown-items", RundownItemViewSet, basename="production-rundown-item")
router.register("production-demands", DemandViewSet, basename="production-demand")

urlpatterns = router.urls
