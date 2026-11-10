import pytest
from django.utils import timezone

from apps.payments.models import EventRegistration, EventTicketConfig
from apps.production.models import Event

WEBHOOK_TOKEN = "test-webhook-token"


@pytest.fixture(autouse=True)
def asaas_webhook_token(settings):
    settings.ASAAS_WEBHOOK_TOKEN = WEBHOOK_TOKEN


@pytest.fixture
def event(campus, jovens_ministry):
    return Event.objects.create(
        title="Retiro de Jovens",
        campus=campus,
        ministry_kind="jovens",
        event_type="ministry_event",
        starts_at=timezone.now() + timezone.timedelta(days=30),
    )


@pytest.fixture
def paid_config(event):
    return EventTicketConfig.objects.create(event=event, price_cents=15000, max_capacity=50)


@pytest.fixture
def free_config(event):
    return EventTicketConfig.objects.create(event=event, price_cents=0)


@pytest.fixture
def fake_asaas(monkeypatch):
    """Replaces every Asaas HTTP call. `state` lets a test pick what Asaas answers."""
    from apps.payments.asaas import AsaasError

    state = {"status": "CONFIRMED", "charge_fails": False, "status_fails": False, "charges": []}

    def find_or_create_customer(email, name):
        return "cus_123"

    def create_charge(customer_id, amount_cents, description, external_reference):
        if state["charge_fails"]:
            raise AsaasError(500, "boom")
        state["charges"].append(
            {"amount_cents": amount_cents, "external_reference": external_reference}
        )
        return {"id": "pay_123", "invoiceUrl": "https://asaas.test/i/pay_123"}

    def get_payment_status(payment_id):
        if state["status_fails"]:
            raise AsaasError(503, "down")
        return state["status"]

    monkeypatch.setattr("apps.payments.views.find_or_create_customer", find_or_create_customer)
    monkeypatch.setattr("apps.payments.views.create_charge", create_charge)
    monkeypatch.setattr("apps.payments.webhooks.get_payment_status", get_payment_status)
    return state


@pytest.fixture
def pending_registration(event, paid_config, regular_person):
    return EventRegistration.objects.create(
        event=event, person=regular_person, asaas_payment_id="pay_123"
    )
