import httpx

from apps.payments import asaas


def test_cancel_installment_deletes_only_open_charges(monkeypatch):
    """Asaas refuses DELETE /installments/{id} once any installment is paid, so
    the open charges are deleted one by one and the paid ones are kept."""
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.method == "GET":
            return httpx.Response(200, json={"data": [
                {"id": "pay_1", "status": "RECEIVED"},
                {"id": "pay_2", "status": "PENDING"},
                {"id": "pay_3", "status": "OVERDUE"},
                {"id": "pay_4", "status": "PENDING", "deleted": True},
            ]})
        return httpx.Response(200, json={"deleted": True})

    monkeypatch.setattr(
        asaas, "_client", lambda: httpx.Client(base_url="https://asaas.test/v3", transport=httpx.MockTransport(handler))
    )
    asaas.cancel_installment("ins_1")

    assert calls[0][:2] == ("GET", "/v3/payments") and calls[0][2]["installment"] == "ins_1"
    assert [c[1] for c in calls if c[0] == "DELETE"] == ["/v3/payments/pay_2", "/v3/payments/pay_3"]
