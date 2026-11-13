"""Notifications by email, and the per-category email preferences."""

import re
from pathlib import Path

import pytest
from django.core import mail

from apps.notifications import emails
from apps.notifications.categories import CATEGORIES, category_of
from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.service import notify
from tests.conftest import make_person

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def email_on(settings):
    settings.NOTIFICATION_EMAILS_ENABLED = True
    settings.FRONTEND_URL = "https://redis.example.org/"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"


@pytest.fixture
def member(campus):
    return make_person(email="membro.e@ilan.church", name="Maria Souza", campus=campus)


def raise_and_send(person, kind="course_completed", **kwargs):
    kwargs.setdefault("link", "/app/cursos/matriculas")
    (note,) = notify(person, kind, "Você concluiu Fundamentos!", body="Seu certificado já está disponível.", **kwargs)
    return note, emails.send_emails([note.pk])


def test_sends_the_notification_by_email_once(member):
    note, sent = raise_and_send(member)
    assert sent == 1
    (message,) = mail.outbox
    assert message.to == ["membro.e@ilan.church"]
    assert message.subject == "Você concluiu Fundamentos!"
    assert "Olá, Maria!" in message.body
    assert "Abrir: https://redis.example.org/app/cursos/matriculas" in message.body
    assert "https://redis.example.org/app/notificacoes/preferencias" in message.body
    html, mimetype = message.alternatives[0]
    assert mimetype == "text/html" and 'href="https://redis.example.org/app/cursos/matriculas"' in html
    assert Notification.objects.get(pk=note.pk).emailed_at is not None

    assert emails.send_emails([note.pk]) == 0  # never twice
    assert len(mail.outbox) == 1


def test_respects_the_category_turned_off(member):
    NotificationPreference.objects.create(person=member, email_off=["courses"])
    _, sent = raise_and_send(member)
    assert sent == 0
    _, sent = raise_and_send(member, kind="event_tomorrow", dedupe_key="x")
    assert sent == 1


def test_nothing_goes_out_while_emails_are_disabled(member, settings):
    settings.NOTIFICATION_EMAILS_ENABLED = False
    _, sent = raise_and_send(member)
    assert sent == 0 and mail.outbox == []


def test_skips_people_without_email_or_inactive(campus):
    inactive = make_person(email="inativo.e@ilan.church", name="Inativo", campus=campus, is_active=False)
    _, sent = raise_and_send(inactive)
    assert sent == 0


def test_queues_the_email_after_commit(member, django_capture_on_commit_callbacks, monkeypatch):
    queued = []
    monkeypatch.setattr("apps.notifications.service._push", lambda *a: None)
    monkeypatch.setattr("apps.notifications.service._queue_delivery", queued.append)
    with django_capture_on_commit_callbacks(execute=True):
        (note,) = notify(member, "appointment", "Você agora é líder")
    assert queued == [[str(note.pk)]]


def test_every_kind_raised_has_a_category():
    """A new kind must join a category, or it would never go by email."""
    source = "".join(
        Path(f"apps/notifications/{name}.py").read_text(encoding="utf-8")
        for name in ("triggers", "reminders")
    )
    # The kind is notify()'s second argument, alone on its line.
    kinds = set(re.findall(r'^\s+"([a-z_]+)",$', source, re.MULTILINE))
    assert len(kinds) >= 13
    assert {k for k in kinds if category_of(k) is None} == set()


# ── Preferences API ──────────────────────────────────────────────────────────


def test_preferences_start_all_on_and_can_be_changed(api_client, member):
    api_client.force_authenticate(user=member)
    res = api_client.get("/api/notifications/preferences/")
    assert res.status_code == 200
    assert res.data["email_available"] is True
    assert res.data["email_address"] == "membro.e@ilan.church"
    assert [c["key"] for c in res.data["categories"]] == list(CATEGORIES)
    assert all(c["email"] for c in res.data["categories"])

    res = api_client.patch("/api/notifications/preferences/", {"email": {"cells": False}}, format="json")
    assert {c["key"]: c["email"] for c in res.data["categories"]}["cells"] is False
    res = api_client.patch("/api/notifications/preferences/", {"email": {"cells": True}}, format="json")
    assert all(c["email"] for c in res.data["categories"])


def test_preferences_reject_unknown_categories(api_client, member):
    api_client.force_authenticate(user=member)
    res = api_client.patch("/api/notifications/preferences/", {"email": {"spam": False}}, format="json")
    assert res.status_code == 400
