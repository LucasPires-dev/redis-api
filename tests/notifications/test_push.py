"""Push notifications: registering devices, sending, and the preferences."""

import json
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from pywebpush import WebPushException

from apps.notifications import push
from apps.notifications.models import NotificationPreference, PushSubscription
from apps.notifications.service import notify
from tests.conftest import make_person

pytestmark = pytest.mark.django_db

FCM = "https://fcm.googleapis.com/fcm/send/abc123"
MOZILLA = "https://updates.push.services.mozilla.com/wpush/v2/xyz"


@pytest.fixture(autouse=True)
def push_on(settings):
    settings.NOTIFICATION_PUSH_ENABLED = True
    settings.VAPID_PUBLIC_KEY = "public"
    settings.VAPID_PRIVATE_KEY = "private"


@pytest.fixture
def member(campus):
    return make_person(email="membro.p@ilan.church", name="Membro", campus=campus)


@pytest.fixture
def sent(monkeypatch):
    calls = []

    def fake_webpush(subscription_info, data, **kwargs):
        calls.append((subscription_info["endpoint"], json.loads(data)))

    monkeypatch.setattr(push, "webpush", fake_webpush)
    return calls


def subscribe(api_client, person, endpoint=FCM):
    api_client.force_authenticate(user=person)
    return api_client.post(
        "/api/notifications/push-subscriptions/",
        {"endpoint": endpoint, "keys": {"p256dh": "BKey", "auth": "secret"}},
        format="json",
    )


# ── Devices ──────────────────────────────────────────────────────────────────


def test_registers_and_forgets_a_device(api_client, member):
    assert subscribe(api_client, member).status_code == 201
    assert subscribe(api_client, member).status_code == 201  # same device again: still one
    assert PushSubscription.objects.filter(person=member).count() == 1

    res = api_client.delete("/api/notifications/push-subscriptions/", {"endpoint": FCM}, format="json")
    assert res.status_code == 204
    assert not PushSubscription.objects.exists()


def test_a_device_follows_the_account_signed_in(api_client, member, campus):
    other = make_person(email="outro.p@ilan.church", name="Outro", campus=campus)
    subscribe(api_client, member)
    subscribe(api_client, other)
    assert PushSubscription.objects.get().person == other


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://fcm.googleapis.com/x",
        "https://127.0.0.1/x",
        "https://evil.com/fcm.googleapis.com",
        "https://fcm.googleapis.com.evil.com/x",
    ],
)
def test_only_browser_push_services_are_accepted(api_client, member, endpoint):
    assert subscribe(api_client, member, endpoint).status_code == 400


# ── Sending ──────────────────────────────────────────────────────────────────


def test_pushes_to_every_device_of_the_person(api_client, member, sent):
    subscribe(api_client, member, FCM)
    subscribe(api_client, member, MOZILLA)
    (note,) = notify(member, "course_completed", "Você concluiu Fundamentos!", body="Certificado pronto.",
                     link="/app/cursos/matriculas")

    assert push.send_push([note.pk]) == 2
    assert {endpoint for endpoint, _ in sent} == {FCM, MOZILLA}
    assert sent[0][1] == {
        "id": str(note.pk), "title": "Você concluiu Fundamentos!", "body": "Certificado pronto.",
        "link": "/app/cursos/matriculas",
    }


def test_respects_the_category_turned_off_for_push(api_client, member, sent):
    subscribe(api_client, member)
    NotificationPreference.objects.create(person=member, push_off=["courses"], email_off=[])
    (note,) = notify(member, "course_completed", "Concluiu")
    assert push.send_push([note.pk]) == 0
    (note,) = notify(member, "event_tomorrow", "Amanhã")
    assert push.send_push([note.pk]) == 1


def test_forgets_devices_the_push_service_says_are_gone(api_client, member, monkeypatch):
    subscribe(api_client, member)

    def gone(*args, **kwargs):
        raise WebPushException("gone", response=SimpleNamespace(status_code=410))

    monkeypatch.setattr(push, "webpush", gone)
    (note,) = notify(member, "course_completed", "Concluiu")
    assert push.send_push([note.pk]) == 0
    assert not PushSubscription.objects.exists()


def test_nothing_is_pushed_without_vapid_keys(api_client, member, sent, settings):
    subscribe(api_client, member)
    settings.NOTIFICATION_PUSH_ENABLED = False
    (note,) = notify(member, "course_completed", "Concluiu")
    assert push.send_push([note.pk]) == 0 and sent == []


# ── Preferences and keys ─────────────────────────────────────────────────────


def test_preferences_have_push_per_category(api_client, member):
    api_client.force_authenticate(user=member)
    res = api_client.get("/api/notifications/preferences/")
    assert res.data["push_available"] is True and res.data["push_public_key"] == "public"
    assert all(c["push"] for c in res.data["categories"])

    res = api_client.patch("/api/notifications/preferences/", {"push": {"cells": False}}, format="json")
    by_key = {c["key"]: c for c in res.data["categories"]}
    assert by_key["cells"]["push"] is False and by_key["cells"]["email"] is True


def test_generate_vapid_keys(capsys):
    call_command("generate_vapid_keys")
    out = capsys.readouterr().out
    public = out.split("VAPID_PUBLIC_KEY=")[1].split()[0]
    private = out.split("VAPID_PRIVATE_KEY=")[1].split()[0]
    assert len(public) == 87 and len(private) == 43
