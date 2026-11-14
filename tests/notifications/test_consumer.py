import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser

from apps.notifications.consumers import NotificationConsumer
from apps.notifications.service import _push
from tests.conftest import make_person

# transaction=True: see tests/production/test_consumers.py.
pytestmark = pytest.mark.django_db(transaction=True)


async def _connect(user):
    communicator = WebsocketCommunicator(NotificationConsumer.as_asgi(), "/ws/notifications/")
    communicator.scope["user"] = user
    connected, _ = await communicator.connect()
    return communicator, connected


@pytest.fixture
def person(db):
    return make_person(email="ws.n@ilan.church", name="Pessoa")


@pytest.mark.asyncio
async def test_receives_only_own_notifications(person):
    communicator, connected = await _connect(person)
    assert connected

    layer = get_channel_layer()
    await layer.group_send("notifications-someone-else", {"type": "notification.new", "title": "Alheia"})
    await layer.group_send(f"notifications-{person.pk}", {"type": "notification.new", "title": "Olá"})
    assert await communicator.receive_json_from() == {"kind": "notification", "title": "Olá"}
    assert await communicator.receive_nothing()

    await communicator.disconnect()


@pytest.mark.asyncio
async def test_anonymous_is_rejected():
    communicator, connected = await _connect(AnonymousUser())
    assert not connected


def test_push_sends_to_each_recipient(person, monkeypatch):
    sent = []

    class Layer:
        async def group_send(self, group, message):
            sent.append((group, message))

    monkeypatch.setattr("apps.notifications.service.get_channel_layer", lambda: Layer())
    _push([person.pk], "Olá")
    assert sent == [(f"notifications-{person.pk}", {"type": "notification.new", "title": "Olá"})]
