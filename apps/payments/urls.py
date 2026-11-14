from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EventRegistrationViewSet,
    EventTicketConfigViewSet,
    EventTicketTypeViewSet,
    WaitlistViewSet,
    public_event_view,
)
from .webhooks import asaas_webhook

router = DefaultRouter()
router.register("ticket-configs", EventTicketConfigViewSet, basename="ticket-config")
router.register("ticket-types", EventTicketTypeViewSet, basename="ticket-type")
router.register("registrations", EventRegistrationViewSet, basename="registration")
router.register("waitlist", WaitlistViewSet, basename="event-waitlist")

urlpatterns = [
    path("payments/public-events/<uuid:event_id>/", public_event_view, name="public-event"),
    path("payments/", include(router.urls)),
    path("payments/webhooks/asaas/", asaas_webhook, name="asaas-webhook"),
]
