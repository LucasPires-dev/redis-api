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
def fake_asaas(monkeypatch, regular_person):
    """Replaces every Asaas HTTP call. `state` lets a test pick what Asaas answers.
    The registering person already has a CPF on file (Asaas requires one)."""
    from apps.payments.asaas import AsaasError

    regular_person.cpf = "52998224725"
    regular_person.save(update_fields=["cpf"])

    state = {
        "status": "CONFIRMED",
        "charge_fails": False,
        "status_fails": False,
        "cancel_fails": False,
        "charges": [],
        "cancelled": [],
    }

    def find_or_create_customer(email, name, cpf=""):
        state.setdefault("customers", []).append({"email": email, "cpf": cpf})
        return "cus_123"

    def create_charge(customer_id, amount_cents, description, external_reference):
        if state["charge_fails"]:
            raise AsaasError(500, "boom")
        state["charges"].append(
            {"amount_cents": amount_cents, "external_reference": external_reference}
        )
        # First charge is pay_123 (matches `pending_registration`), then pay_124...
        payment_id = f"pay_{123 + len(state['charges']) - 1}"
        return {"id": payment_id, "invoiceUrl": f"https://asaas.test/i/{payment_id}"}

    def cancel_charge(payment_id):
        if state["cancel_fails"]:
            raise AsaasError(0, "timeout")
        state["cancelled"].append(payment_id)

    def get_payment_status(payment_id):
        if state["status_fails"]:
            raise AsaasError(503, "down")
        return state["status"]

    monkeypatch.setattr("apps.payments.views.find_or_create_customer", find_or_create_customer)
    monkeypatch.setattr("apps.payments.views.create_charge", create_charge)
    monkeypatch.setattr("apps.payments.views.cancel_charge", cancel_charge)
    monkeypatch.setattr("apps.payments.webhooks.get_payment_status", get_payment_status)
    return state


@pytest.fixture
def pending_registration(event, paid_config, regular_person):
    return EventRegistration.objects.create(
        event=event,
        person=regular_person,
        amount_cents=paid_config.price_cents,
        asaas_payment_id="pay_123",
    )
