import logging

import httpx
from django.conf import settings

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


def _check(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        detail = response.text[:500]
        logger.error("Asaas API error %s: %s", response.status_code, detail)
        raise AsaasError(response.status_code, detail)
    return response.json()


def find_or_create_customer(email: str, name: str) -> str:
    """Returns the Asaas customer id for this email, creating one if needed."""
    with _client() as client:
        resp = client.get("/customers", params={"email": email})
        data = _check(resp)
        if data.get("data"):
            return data["data"][0]["id"]

        resp = client.post("/customers", json={"name": name, "email": email})
        data = _check(resp)
        return data["id"]


def create_charge(
    customer_id: str,
    amount_cents: int,
    description: str,
    external_reference: str,
) -> dict:
    """Creates a charge (Pix + boleto). Returns the full Asaas payment object
    including `id`, `invoiceUrl`, `bankSlipUrl`, and `status`."""
    with _client() as client:
        resp = client.post(
            "/payments",
            json={
                "customer": customer_id,
                "billingType": "UNDEFINED",  # lets the payer choose Pix/boleto/card
                "value": amount_cents / 100,
                "description": description,
                "externalReference": external_reference,
                "dueDate": None,  # immediate
            },
        )
        return _check(resp)


def get_pix_qr(payment_id: str) -> dict:
    """Returns the Pix QR code payload for a payment."""
    with _client() as client:
        resp = client.get(f"/payments/{payment_id}/pixQrCode")
        return _check(resp)


def get_payment_status(payment_id: str) -> str:
    """Returns the current status string from Asaas."""
    with _client() as client:
        resp = client.get(f"/payments/{payment_id}")
        data = _check(resp)
        return data["status"]
