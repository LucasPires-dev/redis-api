from rest_framework.routers import DefaultRouter

from .views import ReconciliationLeadViewSet, ReconciliationViewSet

router = DefaultRouter()
router.register("reconciliation-leads", ReconciliationLeadViewSet, basename="reconciliation-lead")
router.register("reconciliations", ReconciliationViewSet, basename="reconciliation")

urlpatterns = router.urls
