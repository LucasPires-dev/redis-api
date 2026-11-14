from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EventExpenseViewSet,
    EventRegistrationViewSet,
    EventTicketConfigViewSet,
    EventTicketTypeViewSet,
    WaitlistViewSet,
    event_report_csv_view,
    event_report_view,
    event_reports_view,
    public_event_view,
)
from .webhooks import asaas_webhook

router = DefaultRouter()
router.register("ticket-configs", EventTicketConfigViewSet, basename="ticket-config")
router.register("ticket-types", EventTicketTypeViewSet, basename="ticket-type")
router.register("registrations", EventRegistrationViewSet, basename="registration")
router.register("waitlist", WaitlistViewSet, basename="event-waitlist")
router.register("expenses", EventExpenseViewSet, basename="event-expense")

urlpatterns = [
    path("payments/public-events/<uuid:event_id>/", public_event_view, name="public-event"),
    path("payments/reports/", event_reports_view, name="event-reports"),
    path("payments/reports/<uuid:event_id>/", event_report_view, name="event-report"),
    path("payments/reports/<uuid:event_id>/csv/", event_report_csv_view, name="event-report-csv"),
    path("payments/", include(router.urls)),
    path("payments/webhooks/asaas/", asaas_webhook, name="asaas-webhook"),
]
