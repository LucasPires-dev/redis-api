from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import EventRegistrationViewSet, EventTicketConfigViewSet
from .webhooks import asaas_webhook

router = DefaultRouter()
router.register("ticket-configs", EventTicketConfigViewSet, basename="ticket-config")
router.register("registrations", EventRegistrationViewSet, basename="registration")

urlpatterns = [
    path("payments/", include(router.urls)),
    path("payments/webhooks/asaas/", asaas_webhook, name="asaas-webhook"),
]
