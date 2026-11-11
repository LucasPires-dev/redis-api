import logging
from contextlib import contextmanager
from datetime import timedelta

import httpx
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

SANDBOX_URL = "https://sandbox.asaas.com/api/v3"
PRODUCTION_URL = "https://api.asaas.com/v3"


def _base_url() -> str:
    if getattr(settings, "ASAAS_SANDBOX", True):
        return SANDBOX_URL
    return PRODUCTION_URL


def _headers() -> dict:
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "access_token": settings.ASAAS_API_KEY,
    }


def _client() -> httpx.Client:
    return httpx.Client(base_url=_base_url(), headers=_headers(), timeout=30)


class AsaasError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Asaas {status_code}: {detail}")


@contextmanager
def _session():
    """An Asaas client whose network failures (timeout, DNS, connection refused)
    surface as AsaasError, like API errors do — callers handle a single type."""
    try:
        with _client() as client:
            yield client
    except httpx.HTTPError as exc:
        logger.error("Asaas unreachable: %s", exc)
        raise AsaasError(0, str(exc)) from exc


def _check(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        detail = response.text[:500]
        logger.error("Asaas API error %s: %s", response.status_code, detail)
        raise AsaasError(response.status_code, detail)
    return response.json()


def find_or_create_customer(email: str, name: str, cpf: str = "") -> str:
    """Returns the Asaas customer id for this email, creating one if needed.
    Asaas only charges customers with a CPF/CNPJ, so it is set (or updated)
    when given."""
    with _session() as client:
        resp = client.get("/customers", params={"email": email})
        data = _check(resp)
        if data.get("data"):
            customer = data["data"][0]
            if cpf and customer.get("cpfCnpj") != cpf:
                _check(client.post(f"/customers/{customer['id']}", json={"cpfCnpj": cpf}))
            return customer["id"]

        payload = {"name": name, "email": email}
        if cpf:
            payload["cpfCnpj"] = cpf
        data = _check(client.post("/customers", json=payload))
        return data["id"]


# Days the payer has to pay a charge (Asaas requires a due date; a boleto due
# "today" would barely be payable).
CHARGE_DUE_DAYS = 3


def create_charge(
    customer_id: str,
    amount_cents: int,
    description: str,
    external_reference: str,
    *,
    due_days: int = CHARGE_DUE_DAYS,
    installments: int = 1,
) -> dict:
    """Creates a charge (the payer picks Pix, boleto or card). Returns the Asaas
    payment object, including `id`, `invoiceUrl` and `status`.

    With installments > 1 Asaas creates one charge per installment, splitting
    `amount_cents` (any rounding goes to the last one), and returns the first;
    its `installment` field identifies the whole set."""
    due_date = timezone.localdate() + timedelta(days=due_days)
    payload = {
        "customer": customer_id,
        "billingType": "UNDEFINED",  # lets the payer choose Pix/boleto/card
        "description": description,
        "externalReference": external_reference,
        "dueDate": due_date.isoformat(),  # required by Asaas (YYYY-MM-DD)
    }
    if installments > 1:
        payload.update(installmentCount=installments, totalValue=amount_cents / 100)
    else:
        payload["value"] = amount_cents / 100
    with _session() as client:
        return _check(client.post("/payments", json=payload))


def get_pix_qr(payment_id: str) -> dict:
    """Returns the Pix QR code payload for a payment."""
    with _session() as client:
        resp = client.get(f"/payments/{payment_id}/pixQrCode")
        return _check(resp)


def get_payment_status(payment_id: str) -> str:
    """Returns the current status string from Asaas."""
    with _session() as client:
        resp = client.get(f"/payments/{payment_id}")
        data = _check(resp)
        return data["status"]


def cancel_charge(payment_id: str) -> None:
    """Deletes a pending charge, so its payment link stops accepting payments."""
    with _session() as client:
        _check(client.delete(f"/payments/{payment_id}"))


# Charges of an installment plan that can still be deleted (not paid yet).
OPEN_CHARGE_STATUSES = {"PENDING", "OVERDUE"}


def cancel_installment(installment_id: str) -> None:
    """Deletes an installment plan's open charges, keeping the paid ones.
    (DELETE /installments/{id} refuses a plan that has any paid charge.)"""
    with _session() as client:
        data = _check(client.get("/payments", params={"installment": installment_id, "limit": 100}))
        for charge in data.get("data", []):
            if charge.get("status") in OPEN_CHARGE_STATUSES and not charge.get("deleted"):
                _check(client.delete(f"/payments/{charge['id']}"))


def refund_payment(payment_id: str) -> None:
    """Refunds a paid charge in full."""
    with _session() as client:
        _check(client.post(f"/payments/{payment_id}/refund", json={}))
